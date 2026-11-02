# -*- coding: utf-8 -*-
"""
Pacote autocontido de projeto (projeto + dados numa pasta/ZIP, com caminhos
relativos reescritos) e restauração de snapshots criados por
:mod:`core.project_backup`.

Não importa QGIS nem Qt.
"""

import datetime as _dt
import json
import os
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .file_copy import CopyCancelled, copy_file, sha256_file
from .project_backup import FILES_DIR, MANIFEST, mirror_relpath
from .project_inventory import (MEMORY_PROVIDERS, REMOTE_PROVIDERS, RESOURCE_EXTENSIONS, ProjectInventory,
                                _resolve, datasource_raw_path, read_project_xml)

LogFn = Callable[[str], None]
NO_REWRITE_PROVIDERS = frozenset({"vectortile", "xyzvectortiles", "mbtilesvectortiles"})


class PackageAborted(Exception):
    """Empacotamento ou restauração não pode continuar."""


@dataclass
class RewriteReport:
    """Resultado da reescrita de caminhos de um projeto."""

    rewritten: int = 0
    resources: int = 0
    unresolved: List[str] = field(default_factory=list)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def relative_token(target_abs: str, project_dir: str) -> str:
    """Caminho relativo no estilo do QGIS (``./pasta/arquivo``, barras normais)."""
    rel = os.path.relpath(target_abs, project_dir).replace("\\", "/")
    return rel if rel.startswith("../") else "./" + rel


def rewrite_project_xml(xml_bytes: bytes, old_base_dir: str, new_project_dir: str,
                        mapping: Dict[str, str]) -> Tuple[bytes, RewriteReport]:
    """
    Reescreve fontes de camadas e recursos do projeto para caminhos relativos.

    :param xml_bytes: XML original do projeto.
    :param old_base_dir: pasta original do projeto (para resolver caminhos relativos antigos).
    :param new_project_dir: pasta onde o projeto reescrito ficará.
    :param mapping: ``{caminho original normalizado (_norm): novo caminho absoluto}``.
    :returns: (XML reescrito, relatório).
    """
    root = ET.fromstring(xml_bytes)
    report = RewriteReport()

    for layer in root.iter("maplayer"):
        provider = (layer.findtext("provider") or "").strip().lower()
        ds_elem = layer.find("datasource")
        name = (layer.findtext("layername") or "").strip()
        if ds_elem is None or provider in REMOTE_PROVIDERS or provider in MEMORY_PROVIDERS:
            continue
        datasource = ds_elem.text or ""
        found = datasource_raw_path(provider, datasource, old_base_dir)
        if not found:
            continue
        raw, abs_old = found
        new_abs = mapping.get(_norm(abs_old))
        if new_abs is None or provider in NO_REWRITE_PROVIDERS or raw not in datasource:
            report.unresolved.append(name or datasource)
            continue
        token = relative_token(new_abs, new_project_dir)
        if provider in ("delimitedtext", "gpx") and raw.lower().startswith("file:"):
            token = "file:" + urllib.parse.quote(token, safe="/:._-~")
        ds_elem.text = datasource.replace(raw, token, 1)
        report.rewritten += 1

    for elem in root.iter():
        for attr, value in list(elem.attrib.items()):
            if os.path.splitext(value)[1].lower() not in RESOURCE_EXTENSIONS or \
                    value.startswith(("base64:", "http://", "https://", ":/")):
                continue
            new_abs = mapping.get(_norm(_resolve(value, old_base_dir)))
            if new_abs:
                elem.set(attr, relative_token(new_abs, new_project_dir))
                report.resources += 1

    for absolute in root.iter("Absolute"):  # <properties><Paths><Absolute type="bool">
        absolute.text = "false"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True), report


def write_project(original_path: str, new_path: str, xml_bytes: bytes) -> None:
    """
    Grava o projeto reescrito. Para .qgz, recria o ZIP copiando os demais
    membros (ex.: ``.qgd``) e trocando apenas o ``.qgs`` interno.
    """
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    tmp = new_path + ".tmp"
    if original_path.lower().endswith(".qgz"):
        with zipfile.ZipFile(original_path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            replaced = False
            for info in src.infolist():
                if info.filename.lower().endswith(".qgs") and not replaced:
                    dst.writestr(info.filename, xml_bytes)
                    replaced = True
                else:
                    dst.writestr(info, src.read(info.filename))
    else:
        with open(tmp, "wb") as handle:
            handle.write(xml_bytes)
    os.replace(tmp, new_path)


def _package_relpaths(paths: List[str]) -> Dict[str, str]:
    """
    Caminho relativo de cada arquivo dentro do pacote: relativo à raiz comum
    quando há uma; senão, espelho do caminho absoluto (``C/...``, ``home/...``).
    """
    dirs = [os.path.dirname(os.path.abspath(p)) for p in paths]
    try:
        common = os.path.commonpath(dirs)
    except ValueError:  # unidades diferentes no Windows
        common = ""
    result: Dict[str, str] = {}
    for p in paths:
        if common and os.path.dirname(common) != common:  # raiz comum que não seja "/" ou "C:\\"
            rel = os.path.relpath(os.path.abspath(p), common).replace("\\", "/")
        else:
            rel = mirror_relpath(os.path.abspath(p))[len(FILES_DIR) + 1:]
        result[_norm(p)] = rel
    return result


def build_package(inv: ProjectInventory, out_dir: str, make_zip: bool, log: LogFn,
                  is_canceled: Callable[[], bool], progress: Callable[[int, int], None]) -> Tuple[str, RewriteReport]:
    """
    Copia o projeto e seus arquivos para ``out_dir/<nome do projeto>/`` e
    reescreve as fontes para caminhos relativos, de modo que a pasta (ou o
    ZIP) abra em qualquer máquina.

    :returns: (pasta ou ZIP gerado, relatório da reescrita).
    :raises PackageAborted: destino inválido, já existente ou cancelamento.
    """
    if not os.path.isdir(out_dir):
        raise PackageAborted(f"Pasta de saída não encontrada: {out_dir}")
    name = os.path.splitext(os.path.basename(inv.project_path))[0]
    root = os.path.join(out_dir, name)
    if os.path.exists(root) or (make_zip and os.path.exists(root + ".zip")):
        raise PackageAborted(f"Já existe {root}; escolha outra pasta ou remova o pacote anterior.")

    files = [f for f in inv.files]
    rels = _package_relpaths([f.path for f in files])
    mapping = {k: os.path.join(root, *v.split("/")) for k, v in rels.items()}
    total = sum(f.size for f in files)
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    os.makedirs(root)
    try:
        for f in files:
            if is_canceled():
                raise PackageAborted("Empacotamento cancelado.")
            if _norm(f.path) == _norm(inv.project_path):
                continue
            copy_file(f.path, mapping[_norm(f.path)], is_canceled, advance)
        new_project = mapping[_norm(inv.project_path)]
        xml, report = rewrite_project_xml(read_project_xml(inv.project_path), os.path.dirname(inv.project_path),
                                          os.path.dirname(new_project), mapping)
        write_project(inv.project_path, new_project, xml)
        if inv.remote:
            log(f"{len(inv.remote)} camadas remotas continuam apontando para o servidor/banco original.")
        if make_zip:
            archive = shutil.make_archive(root, "zip", out_dir, name)
            shutil.rmtree(root)
            return archive, report
        return root, report
    except CopyCancelled:
        shutil.rmtree(root, ignore_errors=True)
        raise PackageAborted("Empacotamento cancelado.")
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def read_manifest(snapshot_dir: str) -> Dict:
    """Lê o manifesto de um snapshot do BackupQgis."""
    try:
        with open(os.path.join(snapshot_dir, MANIFEST), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PackageAborted(f"Snapshot inválido ({exc}).")
    if manifest.get("generator") != "BackupQgis" or manifest.get("kind") != "project-backup":
        raise PackageAborted("A pasta não é um snapshot de projetos do BackupQgis.")
    return manifest


def restore_snapshot(snapshot_dir: str, target_dir: Optional[str], overwrite: bool, log: LogFn,
                     is_canceled: Callable[[], bool], progress: Callable[[int, int], None]) -> Dict[str, int]:
    """
    Restaura um snapshot.

    - ``target_dir=None``: devolve cada arquivo ao caminho original. Arquivos
      existentes e idênticos (mesmo SHA-256) são mantidos; diferentes só são
      substituídos com ``overwrite=True``, e a versão atual é renomeada para
      ``<nome>.antes-restauracao-AAAAMMDD-HHMMSS<ext>``.
    - ``target_dir=<pasta>``: recria a árvore espelhada dentro da pasta e
      reescreve os projetos para apontar aos arquivos restaurados.

    :returns: contagens ``{"restored", "skipped", "kept", "projects", "failed"}``.
    :raises PackageAborted: snapshot inválido, destino inexistente ou cancelamento.
    """
    manifest = read_manifest(snapshot_dir)
    if target_dir is not None and not os.path.isdir(target_dir):
        raise PackageAborted(f"Pasta de destino não encontrada: {target_dir}")
    entries = manifest.get("files", [])
    total = sum(int(e.get("size", 0)) for e in entries)
    stats = {"restored": 0, "skipped": 0, "kept": 0, "projects": 0, "failed": 0}
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    done = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        progress(done, total)

    mapping: Dict[str, str] = {}
    try:
        for entry in entries:
            if is_canceled():
                raise PackageAborted("Restauração cancelada.")
            src = os.path.join(snapshot_dir, *entry["path"].split("/"))
            if target_dir is None:
                dst = entry["source"]
            else:
                dst = os.path.join(target_dir, *entry["path"].split("/")[1:])
            mapping[_norm(entry["source"])] = dst
            try:
                if os.path.exists(dst):
                    # Cópia SQLite não é idêntica byte a byte ao original: compara pela data de modificação.
                    same_sqlite = entry.get("method") == "sqlite" and int(os.path.getmtime(dst)) == entry.get("mtime")
                    if same_sqlite or (os.path.getsize(dst) == entry.get("size")
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
                    os.replace(dst, f"{stem}.antes-restauracao-{stamp}{ext}")
                copy_file(src, dst, is_canceled, advance)
                stats["restored"] += 1
            except CopyCancelled:
                raise PackageAborted("Restauração cancelada.")
            except OSError as exc:
                stats["failed"] += 1
                log(f"Falha ao restaurar {dst}: {exc}")

        if target_dir is not None:
            for proj in manifest.get("projects", []):
                original = proj.get("project", "")
                new_path = mapping.get(_norm(original))
                if not new_path or not os.path.isfile(new_path):
                    continue
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
                    log(f"Falha ao reescrever {new_path}: {exc}")
    except CopyCancelled:
        raise PackageAborted("Restauração cancelada.")
    return stats
