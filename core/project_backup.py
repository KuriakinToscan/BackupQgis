# -*- coding: utf-8 -*-
"""
Backup de projetos QGIS em snapshots datados, com manifesto SHA-256,
verificação pós-cópia e retenção.

Estrutura de um snapshot::

    <destino>/AAAA-MM-DD_HHMMSS/
        manifest.json
        arquivos/<C>/caminho/original/...      (Windows: letra da unidade)
        arquivos/home/usuario/...              (Linux/macOS)
        arquivos/UNC/servidor/compartilhamento/...

O caminho absoluto original é preservado, de modo que caminhos relativos
entre projeto e dados continuam válidos dentro do snapshot.

Não importa QGIS nem Qt.
"""

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .backup_file import write_json_atomic
from .file_copy import CopyCancelled, copy_file, sha256_file
from .project_inventory import ProjectInventory

GENERATOR = "BackupQgis"
MANIFEST = "manifest.json"
FILES_DIR = "arquivos"
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?$")
PARTIAL_SUFFIX = ".parcial"

LogFn = Callable[[str], None]


class BackupAborted(Exception):
    """Backup não pôde começar ou foi cancelado."""


def mirror_relpath(path: str) -> str:
    """
    Caminho relativo, dentro do snapshot, que espelha o caminho absoluto original.

    ``C:\\dados\\a.shp`` -> ``arquivos/C/dados/a.shp``;
    ``\\\\nas\\sig\\a.tif`` -> ``arquivos/UNC/nas/sig/a.tif``;
    ``/home/ana/a.gpkg`` -> ``arquivos/home/ana/a.gpkg``.
    """
    norm = path.replace("\\", "/")
    if norm.startswith("//"):
        parts = ["UNC"] + [p for p in norm[2:].split("/") if p]
    elif re.match(r"^[A-Za-z]:", norm):
        parts = [norm[0].upper()] + [p for p in norm[2:].split("/") if p]
    else:
        parts = [p for p in norm.split("/") if p]
    parts = [p for p in parts if p not in (".", "..")]
    return "/".join([FILES_DIR] + parts)


def _new_snapshot_name(dest_root: str) -> str:
    base = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name, n = base, 1
    while os.path.exists(os.path.join(dest_root, name)) or os.path.exists(os.path.join(dest_root, name + PARTIAL_SUFFIX)):
        n += 1
        name = f"{base}_{n}"
    return name


def run_project_backup(
    inventories: Iterable[ProjectInventory],
    dest_root: str,
    source_info: Dict[str, Any],
    log: LogFn,
    is_canceled: Callable[[], bool],
    progress: Callable[[int, int], None],
) -> Tuple[str, Dict[str, Any]]:
    """
    Copia projetos e dados para um novo snapshot em ``dest_root``.

    A cópia é feita em ``<snapshot>.parcial`` e renomeada só no fim; se for
    cancelada ou falhar, a pasta parcial é removida.

    :param inventories: inventários de :func:`inventory_project`.
    :param dest_root: pasta de destino (precisa existir: evita gravar em ponto
        de montagem vazio quando o HD/NAS não está conectado).
    :param source_info: dados da origem gravados no manifesto.
    :param progress: ``progress(bytes_copiados, bytes_totais)``.
    :returns: (pasta do snapshot, manifesto).
    :raises BackupAborted: destino inexistente, nada a copiar ou cancelamento.
    """
    inventories = list(inventories)
    if not os.path.isdir(dest_root):
        raise BackupAborted(f"Pasta de destino não encontrada: {dest_root} (HD/NAS desconectado?)")

    unique: Dict[str, Tuple[str, str, int]] = {}  # chave -> (caminho, papel, tamanho)
    for inv in inventories:
        for f in inv.files:
            unique.setdefault(os.path.normcase(os.path.normpath(f.path)), (f.path, f.role, f.size))
    if not unique:
        raise BackupAborted("Nenhum arquivo para copiar.")

    dest_abs = os.path.normcase(os.path.abspath(dest_root)) + os.sep
    inside = [p for p, _r, _s in unique.values() if os.path.normcase(os.path.abspath(p)).startswith(dest_abs)]
    if inside:
        raise BackupAborted(f"O destino contém arquivos de origem ({inside[0]}); escolha outra pasta.")

    total = sum(s for _p, _r, s in unique.values())
    name = _new_snapshot_name(dest_root)
    partial = os.path.join(dest_root, name + PARTIAL_SUFFIX)
    final = os.path.join(dest_root, name)
    os.makedirs(partial)

    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    entries: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        for src, role, size in sorted(unique.values(), key=lambda t: t[0].lower()):
            if is_canceled():
                raise BackupAborted("Backup cancelado.")
            rel = mirror_relpath(os.path.abspath(src))
            dst = os.path.join(partial, *rel.split("/"))
            hasher = hashlib.sha256()
            try:
                method = copy_file(src, dst, is_canceled, advance, hasher)
                digest = sha256_file(dst, is_canceled)
                if method == "bytes" and digest != hasher.hexdigest():
                    errors.append(f"Verificação falhou (hash diferente): {src}")
                st = os.stat(src)
                entries.append({"source": src, "path": rel, "role": role, "size": os.path.getsize(dst),
                                "mtime": int(st.st_mtime), "sha256": digest, "method": method})
            except CopyCancelled:
                raise BackupAborted("Backup cancelado.")
            except OSError as exc:
                errors.append(f"{src}: {exc}")
                log(f"Falha ao copiar {src}: {exc}")

        by_source = {os.path.normcase(os.path.normpath(e["source"])): e["path"] for e in entries}
        manifest = {
            "generator": GENERATOR,
            "kind": "project-backup",
            "schema_version": 1,
            "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
            "source": source_info,
            "projects": [
                {
                    "project": inv.project_path,
                    "path": by_source.get(os.path.normcase(os.path.normpath(inv.project_path)), ""),
                    "files": sorted(by_source[k] for k in (os.path.normcase(os.path.normpath(f.path)) for f in inv.files)
                                    if k in by_source),
                    "remote_layers": [{"layer": n, "provider": p, "uri": u} for n, p, u in inv.remote],
                    "memory_layers": inv.memory,
                    "missing": [{"item": n, "path": p} for n, p in inv.missing],
                    "warnings": inv.warnings,
                }
                for inv in inventories
            ],
            "files": entries,
            "errors": errors,
            "total_bytes": sum(e["size"] for e in entries),
        }
        write_json_atomic(os.path.join(partial, MANIFEST), manifest)
        os.replace(partial, final)
        return final, manifest
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def verify_snapshot(snapshot_dir: str, is_canceled: Optional[Callable[[], bool]] = None) -> List[str]:
    """
    Recalcula o SHA-256 de cada arquivo do snapshot e compara com o manifesto.

    :returns: lista de problemas (vazia se tudo confere).
    """
    with open(os.path.join(snapshot_dir, MANIFEST), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    problems: List[str] = []
    for entry in manifest.get("files", []):
        path = os.path.join(snapshot_dir, *entry["path"].split("/"))
        if not os.path.isfile(path):
            problems.append(f"Ausente: {entry['path']}")
        elif sha256_file(path, is_canceled) != entry["sha256"]:
            problems.append(f"Hash diferente: {entry['path']}")
    return problems


def list_own_snapshots(dest_root: str) -> List[str]:
    """
    Snapshots completos criados pelo BackupQgis em ``dest_root``, do mais
    antigo para o mais recente. Pastas sem manifesto do plugin são ignoradas.
    """
    result: List[str] = []
    try:
        names = sorted(os.listdir(dest_root))
    except OSError:
        return result
    for name in names:
        path = os.path.join(dest_root, name)
        if not (SNAPSHOT_RE.match(name) and os.path.isdir(path)):
            continue
        try:
            with open(os.path.join(path, MANIFEST), "r", encoding="utf-8") as handle:
                if json.load(handle).get("generator") == GENERATOR:
                    result.append(path)
        except (OSError, ValueError):
            continue
    return result


def apply_retention(dest_root: str, keep: int, log: LogFn) -> List[str]:
    """
    Remove os snapshots mais antigos do próprio plugin, mantendo os ``keep``
    mais recentes. ``keep <= 0`` desativa a retenção.

    :returns: pastas removidas.
    """
    if keep <= 0:
        return []
    snaps = list_own_snapshots(dest_root)
    removed: List[str] = []
    for path in snaps[: max(0, len(snaps) - keep)]:
        try:
            shutil.rmtree(path)
            removed.append(path)
            log(f"Snapshot antigo removido pela retenção: {path}")
        except OSError as exc:
            log(f"Não foi possível remover {path}: {exc}")
    return removed
