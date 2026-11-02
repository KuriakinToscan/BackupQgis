# -*- coding: utf-8 -*-
"""
Backup de projeto em pasta única ``<projeto>_bkp``, com a estrutura da pasta
de origem e histórico incremental.

Estrutura::

    <destino>/<projeto>_bkp/
        mapa.qgz, dados/..., estilos/...     cópia atual, igual à pasta do projeto
        _externos/<D>/caminho/...            dados de fora da pasta do projeto
        _historico/AAAA-MM-DD_HHMMSS/...     versões anteriores dos arquivos que
                                             mudaram ou sumiram naquele backup
        _versoes/mapa_AAAA-MM-DD_HHMMSS.qgz  arquivo de projeto a cada salvamento
        _backupqgis/marcador.json            projeto de origem desta pasta
        _backupqgis/manifesto.json           estado atual (SHA-256 de cada arquivo)
        _backupqgis/estados/<data>.json      estado de cada backup (para restaurar datas)

A cada backup só os arquivos novos ou alterados são copiados; a versão
anterior de um arquivo alterado ou removido vai para ``_historico/<data>/``.
Para reconstruir o estado de uma data D, cada arquivo vem do primeiro
``_historico/<data posterior a D>`` que o contém ou, se nenhum, da cópia atual.

Não importa QGIS nem Qt.
"""

import datetime as _dt
import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backup_file import write_json_atomic
from .file_copy import CopyCancelled, copy_file, long_path, sha256_file
from .project_backup import FILES_DIR, mirror_relpath
from .project_inventory import ProjectInventory, inventory_project, read_project_xml
from .report import RunReport, human_size

LogFn = Callable[[str], None]
GENERATOR = "BackupQgis"
KIND = "project-mirror"
BKP_SUFFIX = "_bkp"
CONTROL = "_backupqgis"
EXTERNAL = "_externos"
HISTORY = "_historico"
VERSIONS = "_versoes"
RESERVED = frozenset({CONTROL, EXTERNAL, HISTORY, VERSIONS})
MARKER = "marcador.json"
MANIFEST = "manifesto.json"
STATES = "estados"
MTIME_TOLERANCE = 2.0
NEW_SUFFIX = ".bkq-novo"


class MirrorAborted(Exception):
    """Backup ou restauração não pode continuar (destino ausente, cancelamento...)."""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(long_path(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _join(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


# ---------------------------------------------------------------------------
# Localização
# ---------------------------------------------------------------------------
def bkp_relpath(file_path: str, project_dir: str) -> str:
    """
    Caminho de um arquivo dentro de ``<projeto>_bkp``: relativo à pasta do
    projeto quando está dentro dela; senão (ou se colidir com as pastas de
    controle), em ``_externos/`` espelhando o caminho absoluto.
    """
    path = os.path.normpath(os.path.abspath(file_path))
    base = os.path.normpath(os.path.abspath(project_dir))
    inside = os.path.normcase(path).startswith(os.path.normcase(base) + os.sep)
    if inside:
        rel = os.path.relpath(path, base).replace("\\", "/")
        if rel.split("/", 1)[0] not in RESERVED:
            return rel
    return EXTERNAL + "/" + mirror_relpath(path)[len(FILES_DIR) + 1:]


def is_mirror(path: str) -> bool:
    """``True`` se a pasta é um ``<projeto>_bkp`` do BackupQgis."""
    marker = _read_json(os.path.join(path, CONTROL, MARKER))
    return bool(marker and marker.get("generator") == GENERATOR and marker.get("kind") == KIND)


def resolve_bkp_dir(dest_root: str, project_path: str, create: bool = True) -> Optional[str]:
    """
    Pasta ``<projeto>_bkp`` do projeto em ``dest_root``. Se já existir uma
    pasta com o mesmo nome para outro projeto (ou sem marcador), usa
    ``<projeto>_bkp_2``, ``_3``... Nunca grava em pasta alheia.

    :param create: criar a pasta e o marcador se não existir.
    :returns: caminho da pasta, ou ``None`` se não existir e ``create=False``.
    """
    stem = os.path.splitext(os.path.basename(project_path))[0]
    n = 1
    while True:
        name = f"{stem}{BKP_SUFFIX}" if n == 1 else f"{stem}{BKP_SUFFIX}_{n}"
        path = os.path.join(dest_root, name)
        if not os.path.exists(path):
            if not create:
                return None
            os.makedirs(os.path.join(path, CONTROL))
            write_json_atomic(os.path.join(path, CONTROL, MARKER),
                              {"generator": GENERATOR, "kind": KIND, "project": os.path.abspath(project_path),
                               "created_at": _now_iso()})
            return path
        marker = _read_json(os.path.join(path, CONTROL, MARKER))
        if marker and marker.get("kind") == KIND and _norm(marker.get("project", "")) == _norm(project_path):
            return path
        n += 1


def find_mirrors(dest_root: str) -> List[str]:
    """Pastas ``<projeto>_bkp`` do BackupQgis diretamente em ``dest_root``."""
    try:
        names = sorted(os.listdir(dest_root))
    except OSError:
        return []
    return [os.path.join(dest_root, n) for n in names if os.path.isdir(os.path.join(dest_root, n))
            and is_mirror(os.path.join(dest_root, n))]


def read_mirror_manifest(bkp_dir: str) -> Dict[str, Any]:
    """Manifesto do estado atual (vazio se a pasta ainda não tem backup)."""
    return _read_json(os.path.join(bkp_dir, CONTROL, MANIFEST)) or {}


def list_states(bkp_dir: str) -> List[Dict[str, Any]]:
    """Estados registrados (um por backup concluído), do mais antigo para o mais recente."""
    folder = os.path.join(bkp_dir, CONTROL, STATES)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return []
    states = []
    for name in names:
        data = _read_json(os.path.join(folder, name))
        if data:
            data.setdefault("stamp", name[:-5])
            states.append(data)
    return states


def _new_stamp(bkp_dir: str) -> str:
    base = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    stamp, n = base, 1
    while (os.path.exists(os.path.join(bkp_dir, CONTROL, STATES, stamp + ".json"))
           or os.path.exists(os.path.join(bkp_dir, HISTORY, stamp))):
        n += 1
        stamp = f"{base}_{n}"
    return stamp


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
def run_mirror_backup(inv: ProjectInventory, dest_root: str, source_info: Dict[str, Any], keep: int,
                      log: LogFn, is_canceled: Callable[[], bool], progress: Callable[[int, int], None]
                      ) -> Tuple[str, Dict[str, Any]]:
    """
    Atualiza ``<destino>/<projeto>_bkp`` com o inventário do projeto.

    :param keep: estados mantidos (``<= 0``: todos); o histórico que só servia
        aos estados apagados é removido.
    :returns: (pasta ``_bkp``, resultado) com ``stamp``, ``copied``, ``updated``,
        ``unchanged``, ``removed``, ``bytes``, ``errors`` (lista), ``manifest``,
        ``files_copied``/``files_updated``/``files_removed`` (caminhos relativos) e
        ``pruned`` (estados apagados pela retenção).
    :raises MirrorAborted: destino ausente, destino dentro dos dados ou cancelamento.
    """
    if not os.path.isdir(dest_root):
        raise MirrorAborted(f"Pasta de destino não encontrada: {dest_root} (HD/NAS desconectado?)")
    project_dir = os.path.dirname(os.path.abspath(inv.project_path))
    dest_abs = _norm(dest_root) + os.sep
    for f in inv.files:
        if _norm(f.path).startswith(dest_abs):
            raise MirrorAborted(f"O destino contém arquivos de origem ({f.path}); escolha outra pasta.")

    bkp = resolve_bkp_dir(dest_root, inv.project_path)
    prev = {e["path"]: e for e in read_mirror_manifest(bkp).get("files", []) if isinstance(e, dict) and "path" in e}
    stamp = _new_stamp(bkp)
    hist_root = os.path.join(bkp, HISTORY, stamp)

    current: Dict[str, Tuple[str, str]] = {}  # rel -> (origem, papel)
    for f in inv.files:
        current.setdefault(bkp_relpath(f.path, project_dir), (f.path, f.role))

    plan: List[Tuple[str, str, str, bool]] = []  # (rel, origem, papel, é atualização)
    unchanged: List[str] = []
    for rel, (src, role) in sorted(current.items()):
        try:
            st = os.stat(long_path(src))
        except OSError as exc:
            plan.append((rel, src, role, rel in prev))  # falha registrada adiante
            log(f"Origem inacessível {src}: {exc}")
            continue
        old = prev.get(rel)
        same = (old is not None and old.get("size_src") == st.st_size
                and abs(float(old.get("mtime", 0)) - st.st_mtime) <= MTIME_TOLERANCE
                and os.path.isfile(long_path(_join(bkp, rel))))
        if same:
            unchanged.append(rel)
        else:
            plan.append((rel, src, role, old is not None))
    removed = sorted(r for r in prev if r not in current)

    total = 0
    for _rel, src, _role, _upd in plan:
        try:
            total += os.path.getsize(long_path(src))
        except OSError:
            pass
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    def to_history(rel: str) -> None:
        cur = _join(bkp, rel)
        if os.path.exists(long_path(cur)):
            target = _join(hist_root, rel)
            os.makedirs(long_path(os.path.dirname(target)), exist_ok=True)
            os.replace(long_path(cur), long_path(target))

    entries: Dict[str, Dict[str, Any]] = {rel: dict(prev[rel], source=current[rel][0], role=current[rel][1])
                                          for rel in unchanged}
    result: Dict[str, Any] = {"stamp": stamp, "copied": 0, "updated": 0, "unchanged": len(unchanged), "removed": 0,
                              "bytes": 0, "errors": [], "files_copied": [], "files_updated": [],
                              "files_removed": [], "pruned": []}
    complete = False
    try:
        for rel, src, role, is_update in plan:
            if is_canceled():
                raise MirrorAborted("Backup cancelado.")
            dst = _join(bkp, rel)
            tmp = dst + NEW_SUFFIX
            hasher = hashlib.sha256()
            try:
                st = os.stat(long_path(src))
                method = copy_file(src, tmp, is_canceled, advance, hasher)
                digest = sha256_file(tmp, is_canceled)
                if method == "bytes" and digest != hasher.hexdigest():
                    raise OSError("verificação falhou (hash diferente)")
                to_history(rel)
                os.replace(long_path(tmp), long_path(dst))
                entries[rel] = {"path": rel, "source": src, "role": role, "size": os.path.getsize(long_path(dst)),
                                "size_src": st.st_size, "mtime": st.st_mtime, "sha256": digest, "method": method}
                result["bytes"] += entries[rel]["size"]
                key = "updated" if is_update else "copied"
                result[key] += 1
                result["files_" + key].append(rel)
            except CopyCancelled:
                raise MirrorAborted("Backup cancelado.")
            except OSError as exc:
                if os.path.exists(long_path(tmp)):
                    os.remove(long_path(tmp))
                result["errors"].append(f"{src}: {exc}")
                log(f"Falha ao copiar {src}: {exc}")
                if rel in prev and os.path.isfile(long_path(dst)):
                    entries[rel] = dict(prev[rel], source=src, role=role)  # mantém a cópia anterior
        for rel in removed:
            if is_canceled():
                raise MirrorAborted("Backup cancelado.")
            try:
                to_history(rel)
                result["removed"] += 1
                result["files_removed"].append(rel)
            except OSError as exc:
                result["errors"].append(f"{rel}: {exc}")
                entries[rel] = prev[rel]
        complete = True
    finally:
        manifest = {
            "generator": GENERATOR,
            "kind": KIND,
            "schema_version": 1,
            "stamp": stamp,
            "complete": complete,
            "created_at": _now_iso(),
            "source": source_info,
            "project": os.path.abspath(inv.project_path),
            "project_rel": bkp_relpath(inv.project_path, project_dir),
            "files": [entries[r] for r in sorted(entries)],
            "remote_layers": [{"layer": n, "provider": p, "uri": u} for n, p, u in inv.remote],
            "memory_layers": inv.memory,
            "missing": [{"item": n, "path": p} for n, p in inv.missing],
            "warnings": inv.warnings,
            "errors": result["errors"],
            "changes": {k: result[k] for k in ("copied", "updated", "unchanged", "removed")},
            "total_bytes": sum(int(e.get("size", 0)) for e in entries.values()),
        }
        write_json_atomic(os.path.join(bkp, CONTROL, MANIFEST), manifest)
        if complete:
            os.makedirs(os.path.join(bkp, CONTROL, STATES), exist_ok=True)
            write_json_atomic(os.path.join(bkp, CONTROL, STATES, stamp + ".json"), manifest)
    result["manifest"] = manifest
    result["pruned"] = apply_mirror_retention(bkp, keep, log)
    return bkp, result


def apply_mirror_retention(bkp_dir: str, keep: int, log: LogFn) -> List[str]:
    """
    Mantém os ``keep`` estados mais recentes e apaga o histórico que só servia
    para reconstruir os estados removidos. ``keep <= 0`` mantém tudo.

    :returns: datas dos estados removidos.
    """
    if keep <= 0:
        return []
    states = list_states(bkp_dir)
    if len(states) <= keep:
        return []
    drop = states[: len(states) - keep]
    oldest_kept = states[len(states) - keep]["stamp"]
    removed: List[str] = []
    for state in drop:
        try:
            os.remove(os.path.join(bkp_dir, CONTROL, STATES, state["stamp"] + ".json"))
            removed.append(state["stamp"])
        except OSError as exc:
            log(f"Não foi possível remover o estado {state['stamp']}: {exc}")
    hist = os.path.join(bkp_dir, HISTORY)
    try:
        folders = sorted(os.listdir(hist))
    except OSError:
        folders = []
    for name in folders:
        if name <= oldest_kept:
            try:
                shutil.rmtree(os.path.join(hist, name))
            except OSError as exc:
                log(f"Não foi possível remover {os.path.join(hist, name)}: {exc}")
    if removed:
        log(f"Retenção: {len(removed)} estados antigos removidos de {bkp_dir}.")
    return removed


# ---------------------------------------------------------------------------
# Leitura de um estado e restauração
# ---------------------------------------------------------------------------
def state_files(bkp_dir: str, stamp: Optional[str] = None) -> Tuple[Dict[str, Any], List[Tuple[Dict[str, Any], str]]]:
    """
    Arquivos de um estado e onde está a cópia de cada um.

    :param stamp: data do estado; ``None`` = estado atual.
    :returns: (manifesto do estado, [(entrada, caminho da cópia)]).
    :raises MirrorAborted: estado inexistente.
    """
    if stamp is None:
        manifest = read_mirror_manifest(bkp_dir)
        if not manifest:
            raise MirrorAborted(f"{bkp_dir} ainda não tem backup.")
        return manifest, [(e, _join(bkp_dir, e["path"])) for e in manifest.get("files", [])]
    state = _read_json(os.path.join(bkp_dir, CONTROL, STATES, stamp + ".json"))
    if not state:
        raise MirrorAborted(f"Estado {stamp} não encontrado em {bkp_dir}.")
    try:
        later = sorted(n for n in os.listdir(os.path.join(bkp_dir, HISTORY)) if n > stamp)
    except OSError:
        later = []
    files = []
    for entry in state.get("files", []):
        stored = _join(bkp_dir, entry["path"])
        for name in later:
            candidate = _join(os.path.join(bkp_dir, HISTORY, name), entry["path"])
            if os.path.isfile(long_path(candidate)):
                stored = candidate
                break
        files.append((entry, stored))
    return state, files


def verify_mirror(bkp_dir: str, stamp: Optional[str] = None,
                  is_canceled: Optional[Callable[[], bool]] = None) -> List[str]:
    """Confere o SHA-256 de cada arquivo do estado com o manifesto; retorna os problemas."""
    _state, files = state_files(bkp_dir, stamp)
    problems: List[str] = []
    for entry, stored in files:
        if not os.path.isfile(long_path(stored)):
            problems.append(f"Ausente: {entry['path']}")
        elif sha256_file(stored, is_canceled) != entry.get("sha256"):
            problems.append(f"Hash diferente: {entry['path']}")
    return problems


def restore_mirror(bkp_dir: str, stamp: Optional[str], target_dir: Optional[str], overwrite: bool, log: LogFn,
                   is_canceled: Callable[[], bool], progress: Callable[[int, int], None]) -> Dict[str, Any]:
    """
    Restaura o estado atual (``stamp=None``) ou o de uma data.

    - ``target_dir=None``: cada arquivo volta ao caminho original; idênticos
      são mantidos; diferentes só são substituídos com ``overwrite=True`` e a
      versão atual vira ``<nome>.antes-restauracao-<data><ext>``.
    - ``target_dir=<pasta>``: recria a estrutura do ``_bkp`` na pasta e
      reescreve o projeto para os arquivos restaurados.

    :returns: ``{"restored", "skipped", "kept", "projects", "failed", "errors"}``.
    """
    from .project_package import rewrite_project_xml, write_project

    if target_dir is not None and not os.path.isdir(target_dir):
        raise MirrorAborted(f"Pasta de destino não encontrada: {target_dir}")
    state, files = state_files(bkp_dir, stamp)
    total = sum(int(e.get("size", 0)) for e, _s in files)
    stats: Dict[str, Any] = {"restored": 0, "skipped": 0, "kept": 0, "projects": 0, "failed": 0, "errors": []}
    now = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    mapping: Dict[str, str] = {}
    try:
        for entry, stored in files:
            if is_canceled():
                raise MirrorAborted("Restauração cancelada.")
            dst = entry["source"] if target_dir is None else _join(target_dir, entry["path"])
            mapping[_norm(entry["source"])] = dst
            try:
                if os.path.exists(long_path(dst)):
                    same_sqlite = (entry.get("method") == "sqlite"
                                   and abs(os.path.getmtime(long_path(dst)) - float(entry.get("mtime", 0)))
                                   <= MTIME_TOLERANCE)
                    if same_sqlite or (os.path.getsize(long_path(dst)) == entry.get("size")
                                       and sha256_file(dst) == entry.get("sha256")):
                        stats["skipped"] += 1
                        advance(int(entry.get("size", 0)))
                        continue
                    if not overwrite:
                        stats["kept"] += 1
                        log(f"Mantido (existe e é diferente): {dst}")
                        advance(int(entry.get("size", 0)))
                        continue
                    stem, ext = os.path.splitext(dst)
                    os.replace(long_path(dst), long_path(f"{stem}.antes-restauracao-{now}{ext}"))
                copy_file(stored, dst, is_canceled, advance)
                stats["restored"] += 1
            except CopyCancelled:
                raise MirrorAborted("Restauração cancelada.")
            except OSError as exc:
                stats["failed"] += 1
                stats["errors"].append(f"{dst}: {exc}")
                log(f"Falha ao restaurar {dst}: {exc}")
        if target_dir is not None and state.get("project"):
            original = state["project"]
            new_path = mapping.get(_norm(original))
            if new_path and os.path.isfile(new_path):
                try:
                    xml, report = rewrite_project_xml(read_project_xml(new_path), os.path.dirname(original),
                                                      os.path.dirname(new_path), mapping)
                    write_project(new_path, new_path, xml)
                    stats["projects"] += 1
                    if report.unresolved:
                        log(f"{os.path.basename(new_path)}: {len(report.unresolved)} fontes não reescritas: "
                            + ", ".join(report.unresolved[:10]))
                except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
                    stats["failed"] += 1
                    stats["errors"].append(f"{new_path}: {exc}")
                    log(f"Falha ao reescrever {new_path}: {exc}")
    except CopyCancelled:
        raise MirrorAborted("Restauração cancelada.")
    return stats


# ---------------------------------------------------------------------------
# Operação completa (janela e automação) com registro de tarefas
# ---------------------------------------------------------------------------
def report_backup(report: RunReport, inv: ProjectInventory, bkp: str, result: Dict[str, Any]) -> None:
    """Registra no ``report`` as tarefas de um backup ``run_mirror_backup`` concluído."""
    name = os.path.basename(inv.project_path)
    changes = (f"{result['copied']} novos, {result['updated']} alterados, {result['unchanged']} sem mudança, "
               f"{result['removed']} removidos; {human_size(result['bytes'])} copiados para {bkp}")
    items = ([f"novo: {r}" for r in result["files_copied"]] + [f"alterado: {r}" for r in result["files_updated"]]
             + [f"removido (anterior em {HISTORY}/{result['stamp']}): {r}" for r in result["files_removed"]])
    if result["errors"]:
        report.error(f"{name}: cópia", f"{len(result['errors'])} falhas; {changes}", result["errors"] + items)
    else:
        report.ok(f"{name}: cópia", changes, items)
    if inv.missing:
        report.warn(f"{name}: itens ausentes", f"{len(inv.missing)} não encontrados na origem",
                    [f"{n}: {p}" for n, p in inv.missing])
    if inv.memory:
        report.warn(f"{name}: camadas em memória", "não são gravadas em arquivo e se perdem ao fechar o QGIS",
                    list(inv.memory))
    if inv.warnings:
        report.warn(f"{name}: avisos do inventário", f"{len(inv.warnings)}", list(inv.warnings))
    if inv.remote:
        report.ok(f"{name}: camadas remotas", f"{len(inv.remote)} listadas no manifesto (banco/serviço, não copiadas)",
                  [f"{n} ({p})" for n, p, _u in inv.remote])
    if result["pruned"]:
        report.ok(f"{name}: retenção", f"{len(result['pruned'])} estados antigos removidos", result["pruned"])


def backup_projects(paths: List[str], dest_root: str, source_info: Dict[str, Any], keep: int, log: LogFn,
                    is_canceled: Callable[[], bool], progress: Callable[[int, int], None],
                    title: str = "Backup de projetos") -> Tuple[RunReport, List[ProjectInventory]]:
    """
    Inventaria e faz o backup de cada projeto em ``<destino>/<projeto>_bkp``.
    A falha de um projeto é registrada e não interrompe os demais; o
    cancelamento interrompe a operação.

    :returns: (registro de tarefas encerrado, inventários lidos).
    """
    report = RunReport(title)
    inventories: List[ProjectInventory] = []
    n = max(len(paths), 1)
    for i, path in enumerate(paths):
        name = os.path.basename(path)
        if is_canceled():
            report.error("Cancelamento", f"{len(paths) - i} projetos não processados")
            break
        if not os.path.isfile(path):
            report.error(f"{name}: inventário", f"arquivo de projeto não encontrado: {path}")
            continue
        try:
            inv = inventory_project(path)
        except Exception as exc:  # projeto ilegível não interrompe os demais
            report.error(f"{name}: inventário", str(exc) or exc.__class__.__name__)
            continue
        inventories.append(inv)
        report.ok(f"{name}: inventário", f"{len(inv.files)} arquivos ({human_size(inv.total_size)})")
        try:
            bkp, result = run_mirror_backup(
                inv, dest_root, source_info, keep, log, is_canceled,
                lambda d, t, i=i: progress(i * 1000 + (1000 * d // max(t, 1)), n * 1000))
        except MirrorAborted as exc:
            report.error(f"{name}: cópia", str(exc))
            if is_canceled() or not os.path.isdir(dest_root):
                break
            continue
        except OSError as exc:
            report.error(f"{name}: cópia", str(exc))
            continue
        report_backup(report, inv, bkp, result)
    return report.finish(), inventories
