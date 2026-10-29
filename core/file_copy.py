# -*- coding: utf-8 -*-
"""
Cópia segura de arquivos: atômica, preservando data de modificação, com
tratamento especial para bancos SQLite (GeoPackage, SpatiaLite, MBTiles).

Não importa QGIS nem Qt.
"""

import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.parse
from typing import Any, Callable, Optional

SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".spatialite", ".db", ".mbtiles", ".gpkx")
TEMP_PREFIX = ".backupqgis_tmp_"
CHUNK = 4 * 1024 * 1024


class CopyCancelled(Exception):
    """Cópia interrompida pelo usuário."""


def is_sqlite_file(path: str) -> bool:
    """Indica, pela extensão e pelo cabeçalho, se o arquivo é um banco SQLite."""
    if not path.lower().endswith(SQLITE_EXTENSIONS):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def long_path(path: str) -> str:
    """No Windows, aplica o prefixo ``\\\\?\\`` para caminhos com mais de 260 caracteres."""
    if os.name == "nt" and len(path) >= 248 and not path.startswith("\\\\?\\"):
        path = os.path.abspath(path)
        return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return path


def _sqlite_backup(src: str, dst: str) -> None:
    """
    Copia um banco SQLite com a API de backup, que produz uma cópia consistente
    mesmo com o arquivo aberto no QGIS (inclui páginas confirmadas no ``-wal``).
    """
    path = os.path.abspath(src).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", path):
        path = "/" + path
    uri = "file://" + urllib.parse.quote(path, safe="/:") + "?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target, pages=4096)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()


def copy_file(src: str, dst: str, is_canceled: Optional[Callable[[], bool]] = None,
              on_bytes: Optional[Callable[[int], None]] = None, hasher: Optional[Any] = None) -> str:
    """
    Copia ``src`` para ``dst`` de forma atômica.

    Grava em arquivo temporário na pasta de destino, faz ``fsync``, renomeia
    com ``os.replace`` e aplica a data de modificação da origem. Bancos SQLite
    usam a API de backup do SQLite; se ela falhar, cai na cópia de bytes.

    :param is_canceled: consultado a cada bloco; se ``True``, apaga o temporário
        e levanta :class:`CopyCancelled`.
    :param on_bytes: recebe a quantidade de bytes copiados a cada bloco.
    :param hasher: objeto ``hashlib`` atualizado com os bytes lidos da origem
        (apenas na cópia de bytes; na cópia SQLite o conteúdo pode diferir do arquivo de origem).
    :returns: ``"sqlite"`` ou ``"bytes"``, conforme o método usado.
    :raises OSError: falha de leitura/gravação (o destino fica intacto).
    """
    src_l, dst_l = long_path(src), long_path(dst)
    dst_dir = os.path.dirname(dst_l)
    os.makedirs(dst_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=dst_dir)
    os.close(fd)
    try:
        copied = False
        if is_sqlite_file(src_l):
            try:
                os.remove(tmp)
                _sqlite_backup(src, tmp)
                copied = True
                if on_bytes:
                    on_bytes(os.path.getsize(tmp))
            except sqlite3.Error:
                copied = False
        if not copied:
            with open(src_l, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    if is_canceled and is_canceled():
                        raise CopyCancelled()
                    chunk = fin.read(CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))
                fout.flush()
                os.fsync(fout.fileno())
        try:
            shutil.copystat(src_l, tmp)
        except OSError:
            pass
        os.replace(tmp, dst_l)
        return "sqlite" if copied else "bytes"
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def sha256_file(path: str, is_canceled: Optional[Callable[[], bool]] = None) -> str:
    """Hash SHA-256 do arquivo, lido em blocos."""
    digest = hashlib.sha256()
    with open(long_path(path), "rb") as handle:
        while True:
            if is_canceled and is_canceled():
                raise CopyCancelled()
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
