# -*- coding: utf-8 -*-
"""
Cópia (ZIP) de pastas de plugins para acompanhar o backup.

Serve para plugins instalados por ZIP ou de repositórios privados, que não
podem ser baixados do repositório oficial na restauração. O arquivo fica ao
lado do JSON: ``qgis-backup_x.json`` -> ``qgis-backup_x.plugins.zip``, com uma
pasta de primeiro nível por plugin.

Não importa QGIS nem Qt.
"""

import os
import shutil
import tempfile
import zipfile
from typing import Callable, Iterable, List, Tuple

LogFn = Callable[[str], None]
SUFFIX = ".plugins.zip"
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode", ".pytest_cache"}
SKIP_EXT = (".pyc", ".pyo")


class PluginArchiveError(Exception):
    """Arquivo de plugins inválido ou extração recusada."""


def archive_path_for(backup_path: str) -> str:
    """Caminho do ZIP de plugins correspondente a um backup JSON."""
    stem = backup_path[:-5] if backup_path.lower().endswith(".json") else backup_path
    return stem + SUFFIX


def write_plugins_archive(zip_path: str, plugins: Iterable[Tuple[str, str]], log: LogFn) -> List[str]:
    """
    Compacta as pastas dos plugins em ``zip_path`` (gravação atômica).

    :param plugins: pares ``(chave, pasta do plugin)``.
    :returns: chaves incluídas.
    :raises OSError: falha de gravação.
    """
    included: List[str] = []
    folder = os.path.dirname(os.path.abspath(zip_path))
    fd, tmp = tempfile.mkstemp(prefix=".plugins-", suffix=".zip.tmp", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for key, path in plugins:
                if not os.path.isfile(os.path.join(path, "metadata.txt")):
                    log(f"Plugin '{key}' ignorado no ZIP: pasta sem metadata.txt ({path}).")
                    continue
                for root, dirs, files in os.walk(path):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                    for name in sorted(files):
                        if name.endswith(SKIP_EXT):
                            continue
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, path).replace(os.sep, "/")
                        archive.write(full, f"{key}/{rel}")
                included.append(key)
        if not included:
            os.remove(tmp)
            return included
        os.replace(tmp, zip_path)
        return included
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def archive_keys(zip_path: str) -> List[str]:
    """Plugins contidos no ZIP (pastas de primeiro nível com ``metadata.txt``)."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise PluginArchiveError(f"ZIP de plugins inválido: {exc}")
    return sorted({n.split("/", 1)[0] for n in names if n.count("/") == 1 and n.endswith("/metadata.txt")})


def extract_plugin(zip_path: str, key: str, plugins_dir: str) -> str:
    """
    Extrai um plugin do ZIP para ``plugins_dir/<chave>``.

    A extração vai para uma pasta temporária e só então é movida, para não
    deixar plugin pela metade. Recusa sobrescrever pasta existente e entradas
    que escapariam da pasta do plugin (``..``, caminhos absolutos).

    :returns: pasta do plugin extraído.
    :raises PluginArchiveError: plugin ausente no ZIP, destino existente ou entrada insegura.
    """
    if not key or "/" in key or "\\" in key or key in (".", ".."):
        raise PluginArchiveError(f"Nome de plugin inválido: {key!r}")
    target = os.path.join(plugins_dir, key)
    if os.path.exists(target):
        raise PluginArchiveError(f"Já existe {target}; o plugin não foi sobrescrito.")
    os.makedirs(plugins_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=f".{key}-", dir=plugins_dir)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [m for m in archive.infolist() if m.filename.startswith(key + "/")]
            if not any(m.filename == f"{key}/metadata.txt" for m in members):
                raise PluginArchiveError(f"'{key}' não está no ZIP de plugins.")
            base = os.path.realpath(tmp)
            for member in members:
                rel = member.filename[len(key) + 1:]
                if not rel:
                    continue
                dest = os.path.realpath(os.path.join(tmp, *rel.split("/")))
                if not dest.startswith(base + os.sep):
                    raise PluginArchiveError(f"Entrada insegura no ZIP: {member.filename}")
                if member.is_dir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with archive.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        os.replace(tmp, target)
        return target
    except zipfile.BadZipFile as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise PluginArchiveError(f"ZIP de plugins inválido: {exc}")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
