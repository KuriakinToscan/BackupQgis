# -*- coding: utf-8 -*-
"""
Sincronização unidirecional entre pastas (disco local → HD externo/NAS).

Modos:

- ``additive``: copia arquivos novos e alterados; nunca apaga no destino.
- ``mirror``: o destino fica igual à origem; o que seria apagado é movido
  para ``.backupqgis_trash/AAAA-MM-DD_HHMMSS/`` no próprio destino.

Proteções:

- o destino precisa conter o arquivo-marcador ``.backupqgis-target.json``
  com o UUID registrado no par (evita gravar no disco local quando o HD/NAS
  não está montado e evita sincronizar com a unidade errada após troca de
  letra no Windows);
- origem vazia ou inacessível aborta;
- no modo espelho, apagar mais que ``max_delete_ratio`` dos arquivos do
  destino aborta.

Não importa QGIS nem Qt.
"""

import datetime as _dt
import fnmatch
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .backup_file import write_json_atomic
from .file_copy import TEMP_PREFIX, CopyCancelled, copy_file, is_sqlite_file

MARKER = ".backupqgis-target.json"
TRASH_DIR = ".backupqgis_trash"
DEFAULT_EXCLUDES = ("*.gpkg-wal", "*.gpkg-shm", "*.sqlite-wal", "*.sqlite-shm", "*.qgz~", "*.qgs~", "*.qgd~",
                    "__pycache__", "Thumbs.db", ".DS_Store", "desktop.ini", "~$*", "*.tmp", "*.lock")
DEFAULT_TOLERANCE_S = 2.0
DEFAULT_MAX_DELETE_RATIO = 0.2

FileInfo = Tuple[int, float]  # (tamanho, mtime)
LogFn = Callable[[str], None]


class SyncAborted(Exception):
    """A sincronização não pode começar ou foi interrompida."""


@dataclass
class SyncPlan:
    """Ações calculadas pela simulação."""

    source: str
    dest: str
    mode: str
    copy_new: List[str] = field(default_factory=list)
    update: List[str] = field(default_factory=list)
    delete: List[str] = field(default_factory=list)
    dest_newer: List[str] = field(default_factory=list)  # destino mais recente: não sobrescrito
    bytes_to_copy: int = 0
    source_count: int = 0
    dest_count: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.copy_new or self.update or self.delete)


# ----------------------------------------------------------------------
# Marcador do destino
# ----------------------------------------------------------------------
def read_marker(dest: str) -> Optional[Dict[str, str]]:
    """Conteúdo do marcador do destino ou ``None`` se ausente/ilegível."""
    try:
        with open(os.path.join(dest, MARKER), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and data.get("uuid") else None
    except (OSError, ValueError):
        return None


def init_marker(dest: str, label: str = "") -> str:
    """
    Cria o marcador no destino (a pasta precisa existir) e retorna o UUID.
    Se já existir marcador válido, reutiliza o UUID.

    :raises SyncAborted: pasta inexistente.
    """
    if not os.path.isdir(dest):
        raise SyncAborted(f"Pasta de destino não encontrada: {dest}")
    existing = read_marker(dest)
    if existing:
        return existing["uuid"]
    target_id = str(uuid.uuid4())
    write_json_atomic(os.path.join(dest, MARKER), {
        "generator": "BackupQgis", "uuid": target_id, "label": label,
        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
    })
    return target_id


def check_target(dest: str, expected_uuid: str) -> None:
    """
    Confere se ``dest`` é o destino registrado.

    :raises SyncAborted: pasta inexistente, sem marcador ou com UUID diferente.
    """
    if not os.path.isdir(dest):
        raise SyncAborted(f"Destino não encontrado: {dest}. O HD/NAS está conectado?")
    marker = read_marker(dest)
    if marker is None:
        raise SyncAborted(f"O destino {dest} não tem o marcador {MARKER}. Se é a unidade certa, "
                          "prepare o destino novamente; caso contrário, verifique se o HD/NAS está montado.")
    if expected_uuid and marker["uuid"] != expected_uuid:
        raise SyncAborted(f"O destino {dest} pertence a outro par de sincronização (UUID diferente). "
                          "Verifique se a unidade correta está conectada.")


# ----------------------------------------------------------------------
# Varredura e plano
# ----------------------------------------------------------------------
def _excluded(name: str, rel: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return False


def scan_tree(root: str, excludes: Iterable[str] = DEFAULT_EXCLUDES,
              is_canceled: Optional[Callable[[], bool]] = None) -> Dict[str, FileInfo]:
    """
    Lista os arquivos de ``root`` com tamanho e mtime, por caminho relativo
    com ``/``. Ignora o marcador, a lixeira, temporários do plugin e os
    padrões de exclusão. Links simbólicos não são seguidos.
    """
    excludes = list(excludes)
    result: Dict[str, FileInfo] = {}
    stack = [""]
    while stack:
        if is_canceled and is_canceled():
            raise SyncAborted("Sincronização cancelada.")
        rel_dir = stack.pop()
        abs_dir = os.path.join(root, *rel_dir.split("/")) if rel_dir else root
        try:
            entries = list(os.scandir(abs_dir))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if (not rel_dir and name in (MARKER, TRASH_DIR)) or name.startswith(TEMP_PREFIX):
                continue
            if _excluded(name, rel, excludes):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(rel)
                elif entry.is_file():
                    st = entry.stat()
                    result[rel] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return result


def build_plan(source: str, dest: str, src_tree: Dict[str, FileInfo], dst_tree: Dict[str, FileInfo],
               mode: str, tolerance: float = DEFAULT_TOLERANCE_S) -> SyncPlan:
    """
    Compara as duas árvores e calcula as ações.

    Arquivo alterado = mtime difere mais que ``tolerance`` segundos, ou tamanho
    difere (exceto bancos SQLite, cuja cópia consistente pode ter tamanho
    diferente do original). Se o destino for mais recente que a origem, o
    arquivo não é sobrescrito e vai para ``dest_newer``.
    """
    plan = SyncPlan(source=source, dest=dest, mode=mode, source_count=len(src_tree), dest_count=len(dst_tree))
    dst_lower = {k.lower(): k for k in dst_tree}
    for rel, (size, mtime) in sorted(src_tree.items()):
        target = rel if rel in dst_tree else dst_lower.get(rel.lower())
        if target is None:
            plan.copy_new.append(rel)
            plan.bytes_to_copy += size
            continue
        if target != rel:
            plan.warnings.append(f"Diferença de maiúsculas/minúsculas: {rel} × {target}")
        d_size, d_mtime = dst_tree[target]
        delta = mtime - d_mtime
        sqlite = rel.lower().endswith((".gpkg", ".sqlite", ".spatialite", ".db", ".mbtiles", ".gpkx"))
        if delta > tolerance or (abs(delta) <= tolerance and size != d_size and not sqlite):
            plan.update.append(rel)
            plan.bytes_to_copy += size
        elif delta < -tolerance:
            plan.dest_newer.append(rel)
    if mode == "mirror":
        src_lower = {k.lower() for k in src_tree}
        plan.delete = sorted(rel for rel in dst_tree if rel not in src_tree and rel.lower() not in src_lower)
    return plan


def guard_plan(plan: SyncPlan, max_delete_ratio: float = DEFAULT_MAX_DELETE_RATIO, force: bool = False) -> None:
    """
    Trava de segurança antes de executar.

    :raises SyncAborted: origem vazia, ou apagamento acima do limite sem ``force``.
    """
    if plan.source_count == 0:
        raise SyncAborted("A origem está vazia ou inacessível; nada será feito.")
    if plan.mode == "mirror" and plan.dest_count and not force:
        ratio = len(plan.delete) / plan.dest_count
        if ratio > max_delete_ratio:
            raise SyncAborted(
                f"O espelho apagaria {len(plan.delete)} de {plan.dest_count} arquivos do destino "
                f"({ratio:.0%}, limite {max_delete_ratio:.0%}). Confira a origem antes de continuar.")


def execute_plan(plan: SyncPlan, log: LogFn, is_canceled: Callable[[], bool],
                 progress: Callable[[int, int], None]) -> Dict[str, int]:
    """
    Executa o plano: copia novos/alterados (cópia atômica) e, no modo espelho,
    move para a lixeira do destino os arquivos ausentes na origem.

    :returns: contagens ``{"copied", "updated", "trashed", "failed"}``.
    :raises SyncAborted: cancelamento (o arquivo em cópia é descartado; os já
        copiados permanecem).
    """
    stats = {"copied": 0, "updated": 0, "trashed": 0, "failed": 0}
    total = plan.bytes_to_copy
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    try:
        for kind, items in (("copied", plan.copy_new), ("updated", plan.update)):
            for rel in items:
                if is_canceled():
                    raise SyncAborted("Sincronização cancelada.")
                src = os.path.join(plan.source, *rel.split("/"))
                dst = os.path.join(plan.dest, *rel.split("/"))
                try:
                    if is_sqlite_file(src):
                        log(f"Cópia consistente (SQLite): {rel}")
                    copy_file(src, dst, is_canceled, advance)
                    stats[kind] += 1
                except OSError as exc:
                    stats["failed"] += 1
                    log(f"Falha ao copiar {rel}: {exc}")

        if plan.mode == "mirror" and plan.delete:
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            trash_root = os.path.join(plan.dest, TRASH_DIR, stamp)
            for rel in plan.delete:
                if is_canceled():
                    raise SyncAborted("Sincronização cancelada.")
                src = os.path.join(plan.dest, *rel.split("/"))
                dst = os.path.join(trash_root, *rel.split("/"))
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    stats["trashed"] += 1
                except OSError as exc:
                    stats["failed"] += 1
                    log(f"Falha ao mover para a lixeira {rel}: {exc}")
    except CopyCancelled:
        raise SyncAborted("Sincronização cancelada.")
    return stats


def default_excludes_text() -> str:
    """Padrões de exclusão padrão, separados por ``;``."""
    return ";".join(DEFAULT_EXCLUDES)


def parse_excludes(text: str) -> List[str]:
    """Converte texto ``a;b;c`` em lista de padrões."""
    return [p.strip() for p in text.replace("\n", ";").split(";") if p.strip()]


# ----------------------------------------------------------------------
# Sincronização bidirecional (experimental)
# ----------------------------------------------------------------------
SQLITE_SUFFIXES = (".gpkg", ".sqlite", ".spatialite", ".db", ".mbtiles", ".gpkx")
StateEntry = List[float]  # [tamanho_a, mtime_a, tamanho_b, mtime_b]


@dataclass
class BiPlan:
    """Ações da sincronização bidirecional entre as pastas A (origem) e B (destino)."""

    a: str
    b: str
    a_to_b: List[str] = field(default_factory=list)
    b_to_a: List[str] = field(default_factory=list)
    delete_a: List[str] = field(default_factory=list)
    delete_b: List[str] = field(default_factory=list)
    conflicts: List[Tuple[str, str]] = field(default_factory=list)  # (arquivo, lado vencedor "a"|"b")
    bytes_to_copy: int = 0
    a_count: int = 0
    b_count: int = 0
    first_run: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.a_to_b or self.b_to_a or self.delete_a or self.delete_b or self.conflicts)


def _same(info1: FileInfo, info2: FileInfo, rel: str, tolerance: float) -> bool:
    if abs(info1[1] - info2[1]) > tolerance:
        return False
    return info1[0] == info2[0] or rel.lower().endswith(SQLITE_SUFFIXES)


def build_bidirectional_plan(a: str, b: str, a_tree: Dict[str, FileInfo], b_tree: Dict[str, FileInfo],
                             state: Dict[str, StateEntry], tolerance: float = DEFAULT_TOLERANCE_S,
                             same_content: Optional[Callable[[str], bool]] = None) -> BiPlan:
    """
    Compara A e B com o estado da última sincronização.

    - Alterado só de um lado: copia para o outro.
    - Alterado dos dois lados (ou diferente sem estado anterior): conflito; o
      mais recente vence e o outro é preservado como cópia de conflito.
    - Apagado de um lado e intacto do outro (segundo o estado): apaga do outro
      (movendo para a lixeira daquele lado).
    - Apagado de um lado e alterado do outro: o alterado é copiado de volta.

    :param state: ``{arquivo: [tamanho_a, mtime_a, tamanho_b, mtime_b]}`` da última execução.
    :param same_content: opcional; ``same_content(arquivo)`` compara o conteúdo
        (ex.: SHA-256) quando não há estado e os tamanhos coincidem, evitando
        conflitos falsos na primeira execução.
    """
    plan = BiPlan(a=a, b=b, a_count=len(a_tree), b_count=len(b_tree), first_run=not state)
    for rel in sorted(set(a_tree) | set(b_tree)):
        ia, ib, st = a_tree.get(rel), b_tree.get(rel), state.get(rel)
        changed_a = ia is not None and (st is None or not _same(ia, (st[0], st[1]), rel, tolerance))
        changed_b = ib is not None and (st is None or not _same(ib, (st[2], st[3]), rel, tolerance))
        if ia is not None and ib is not None:
            if _same(ia, ib, rel, tolerance):
                continue
            if st is None and ia[0] == ib[0] and same_content is not None and same_content(rel):
                continue
            if changed_a and not changed_b:
                plan.a_to_b.append(rel)
                plan.bytes_to_copy += ia[0]
            elif changed_b and not changed_a:
                plan.b_to_a.append(rel)
                plan.bytes_to_copy += ib[0]
            else:
                winner = "a" if ia[1] >= ib[1] else "b"
                plan.conflicts.append((rel, winner))
                plan.bytes_to_copy += ia[0] + ib[0]
        elif ia is not None:
            if st is not None and not changed_a:
                plan.delete_a.append(rel)
            else:
                plan.a_to_b.append(rel)
                plan.bytes_to_copy += ia[0]
        elif ib is not None:
            if st is not None and not changed_b:
                plan.delete_b.append(rel)
            else:
                plan.b_to_a.append(rel)
                plan.bytes_to_copy += ib[0]
    return plan


def guard_bidirectional(plan: BiPlan, max_delete_ratio: float = DEFAULT_MAX_DELETE_RATIO, force: bool = False) -> None:
    """
    Trava de segurança da sincronização bidirecional.

    :raises SyncAborted: um dos lados vazio, ou apagamento acima do limite sem ``force``.
    """
    if plan.a_count == 0 or plan.b_count == 0:
        if not plan.first_run or (plan.a_count == 0 and plan.b_count == 0):
            raise SyncAborted("Um dos lados está vazio ou inacessível; nada será feito.")
    if force:
        return
    for side, deletes, count in (("origem", plan.delete_a, plan.a_count), ("destino", plan.delete_b, plan.b_count)):
        if count and len(deletes) / count > max_delete_ratio:
            raise SyncAborted(f"A sincronização apagaria {len(deletes)} de {count} arquivos da {side} "
                              f"(limite {max_delete_ratio:.0%}). Confira as pastas antes de continuar.")


def conflict_name(rel: str, side: str, stamp: str) -> str:
    """``pasta/mapa.qgz`` -> ``pasta/mapa (conflito origem 2026-10-29_1310).qgz``."""
    stem, ext = os.path.splitext(rel)
    label = "origem" if side == "a" else "destino"
    return f"{stem} (conflito {label} {stamp}){ext}"


def execute_bidirectional(plan: BiPlan, log: LogFn, is_canceled: Callable[[], bool],
                          progress: Callable[[int, int], None]) -> Dict[str, int]:
    """
    Executa o plano bidirecional. Apagamentos vão para a lixeira do respectivo
    lado; em conflito, a versão perdedora é renomeada como cópia de conflito
    e replicada para o outro lado, de modo que nenhuma versão se perde.

    :returns: contagens ``{"a_to_b", "b_to_a", "trashed", "conflicts", "failed"}``.
    """
    stats = {"a_to_b": 0, "b_to_a": 0, "trashed": 0, "conflicts": 0, "failed": 0}
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, plan.bytes_to_copy)

    def path(root: str, rel: str) -> str:
        return os.path.join(root, *rel.split("/"))

    try:
        for key, items, src_root, dst_root in (("a_to_b", plan.a_to_b, plan.a, plan.b),
                                               ("b_to_a", plan.b_to_a, plan.b, plan.a)):
            for rel in items:
                if is_canceled():
                    raise SyncAborted("Sincronização cancelada.")
                try:
                    copy_file(path(src_root, rel), path(dst_root, rel), is_canceled, advance)
                    stats[key] += 1
                except OSError as exc:
                    stats["failed"] += 1
                    log(f"Falha ao copiar {rel}: {exc}")

        for rel, winner in plan.conflicts:
            if is_canceled():
                raise SyncAborted("Sincronização cancelada.")
            win_root, lose_root = (plan.a, plan.b) if winner == "a" else (plan.b, plan.a)
            lose_side = "b" if winner == "a" else "a"
            alt = conflict_name(rel, lose_side, stamp)
            try:
                os.replace(path(lose_root, rel), path(lose_root, alt))
                copy_file(path(lose_root, alt), path(win_root, alt), is_canceled, advance)
                copy_file(path(win_root, rel), path(lose_root, rel), is_canceled, advance)
                stats["conflicts"] += 1
                log(f"Conflito em {rel}: mantida a versão mais recente; a outra foi salva como {alt}.")
            except OSError as exc:
                stats["failed"] += 1
                log(f"Falha ao resolver conflito em {rel}: {exc}")

        for root, items in ((plan.a, plan.delete_a), (plan.b, plan.delete_b)):
            for rel in items:
                if is_canceled():
                    raise SyncAborted("Sincronização cancelada.")
                dst = os.path.join(root, TRASH_DIR, stamp, *rel.split("/"))
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(path(root, rel), dst)
                    stats["trashed"] += 1
                except OSError as exc:
                    stats["failed"] += 1
                    log(f"Falha ao mover para a lixeira {rel}: {exc}")
    except CopyCancelled:
        raise SyncAborted("Sincronização cancelada.")
    return stats


def build_state(a_tree: Dict[str, FileInfo], b_tree: Dict[str, FileInfo],
                tolerance: float = DEFAULT_TOLERANCE_S) -> Dict[str, StateEntry]:
    """Estado a gravar após a sincronização: arquivos presentes e iguais nos dois lados."""
    return {rel: [ia[0], ia[1], b_tree[rel][0], b_tree[rel][1]]
            for rel, ia in a_tree.items() if rel in b_tree and _same(ia, b_tree[rel], rel, tolerance)}


def load_state(path: str) -> Dict[str, StateEntry]:
    """Lê o estado da última sincronização bidirecional (vazio se não existir)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {k: list(v) for k, v in data.get("files", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(path: str, state: Dict[str, StateEntry]) -> None:
    """Grava o estado de forma atômica."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, {"generator": "BackupQgis", "files": state})
