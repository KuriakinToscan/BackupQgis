# -*- coding: utf-8 -*-
"""
Inventário de projetos QGIS (.qgz/.qgs) sem abrir o projeto no QGIS.

O XML do projeto é lido diretamente (descompactando o .qgz com ``zipfile``)
e as fontes de cada camada são classificadas em arquivos locais (com seus
arquivos auxiliares), camadas remotas (bancos e serviços, não copiadas),
camadas em memória e fontes ausentes.

Não importa QGIS nem Qt.
"""

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

# Provedores cujos dados ficam em servidor/banco e não são copiados.
REMOTE_PROVIDERS = frozenset({
    "postgres", "postgresraster", "mssql", "oracle", "hana", "db2", "wms", "wfs", "wcs", "oapif",
    "arcgismapserver", "arcgisfeatureserver", "afs", "ams", "geonode", "sensorthings", "cesiumtiles",
    "tiledscene",
})
MEMORY_PROVIDERS = frozenset({"memory", "virtual"})

# Extensões de arquivos referenciados por simbologia, layouts e estilos.
RESOURCE_EXTENSIONS = frozenset({".svg", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".qml", ".sld", ".webp"})

SHAPEFILE_SIDECARS = (".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".fix", ".atx", ".ain", ".aih")
WORLD_FILE_EXTENSIONS = (".tfw", ".tifw", ".tiffw", ".wld", ".jgw", ".jpgw", ".pgw", ".pngw", ".j2w", ".gfw", ".bpw")
ARCHIVE_EXTENSIONS = (".zip", ".gz", ".tar", ".tgz", ".7z", ".rar")
SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".db", ".spatialite", ".mbtiles", ".gpkx")

_SECRET_RE = re.compile(r"(?i)\b(password|passwd|pwd|token|apikey|api_key|access_token)\s*=\s*('[^']*'|\"[^\"]*\"|[^\s&']+)")
_DBNAME_RE = re.compile(r"dbname\s*=\s*'((?:[^'\\]|\\.)*)'|dbname\s*=\s*\"([^\"]*)\"|dbname\s*=\s*(\S+)")
_DRIVER_PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]+):(.+)$")


@dataclass
class InventoryFile:
    """Arquivo local necessário ao projeto."""

    path: str
    role: str  # "projeto", "dados", "auxiliar", "recurso"
    layer: str = ""
    size: int = 0


@dataclass
class ProjectInventory:
    """Resultado do inventário de um projeto."""

    project_path: str
    files: List[InventoryFile] = field(default_factory=list)
    remote: List[Tuple[str, str, str]] = field(default_factory=list)  # (camada, provedor, URI sem segredo)
    memory: List[str] = field(default_factory=list)
    missing: List[Tuple[str, str]] = field(default_factory=list)  # (camada ou recurso, caminho)
    warnings: List[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def sanitize_uri(uri: str) -> str:
    """Remove senhas, tokens e chaves de API de uma URI de fonte de dados."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", uri)


def read_project_xml(project_path: str) -> bytes:
    """
    Lê o XML do projeto.

    :param project_path: caminho de .qgs ou .qgz.
    :raises ValueError: .qgz sem arquivo .qgs interno.
    :raises OSError, zipfile.BadZipFile: arquivo ilegível.
    """
    if project_path.lower().endswith(".qgz"):
        with zipfile.ZipFile(project_path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".qgs")]
            if not names:
                raise ValueError("Arquivo .qgz sem projeto .qgs interno.")
            return archive.read(names[0])
    with open(project_path, "rb") as handle:
        return handle.read()


def _resolve(path: str, base_dir: str) -> str:
    path = path.strip().strip('"').strip("'")
    if not path:
        return ""
    if path.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(path).path)
        if re.match(r"^/[A-Za-z]:", path):  # file:///C:/...
            path = path[1:]
    elif path.startswith("file:"):
        path = urllib.parse.unquote(path[len("file:"):])
    if not (os.path.isabs(path) or re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\\\")):
        path = os.path.join(base_dir, path)
    return os.path.normpath(path)


def _strip_vsi(path: str) -> str:
    """``/vsizip/dados.zip/x.shp`` -> ``dados.zip`` (o arquivo físico)."""
    lower = path.lower()
    for prefix in ("/vsizip/", "/vsigzip/", "/vsitar/", "/vsi7z/", "/vsirar/"):
        if lower.startswith(prefix):
            rest = path[len(prefix):]
            rest_lower = rest.lower()
            for ext in ARCHIVE_EXTENSIONS:
                idx = rest_lower.find(ext)
                if idx >= 0:
                    return rest[: idx + len(ext)]
            return rest
    return path


def _file_from_driver_syntax(rest: str, base_dir: str) -> str:
    """
    Extrai o caminho de sintaxes ``DRIVER:"caminho":sub`` ou ``DRIVER:caminho:sub``.
    Testa os cortes possíveis da direita para a esquerda e usa o primeiro que existe.
    """
    rest = rest.strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return _resolve(rest[1:end] if end > 0 else rest[1:], base_dir)
    parts = rest.split(":")
    for cut in range(len(parts), 0, -1):
        candidate = _resolve(":".join(parts[:cut]), base_dir)
        if os.path.exists(candidate):
            return candidate
    return _resolve(rest, base_dir)


def datasource_to_path(provider: str, datasource: str, base_dir: str) -> Optional[str]:
    """
    Converte a fonte de uma camada de arquivo em caminho absoluto.

    :param provider: chave do provedor (``ogr``, ``gdal``, ``spatialite``...).
    :param datasource: texto de ``<datasource>`` no projeto.
    :param base_dir: pasta do projeto (base dos caminhos relativos).
    :returns: caminho absoluto ou ``None`` se a fonte não for um arquivo.
    """
    ds = datasource.strip()
    if not ds:
        return None
    provider = provider.lower()

    if provider == "spatialite":
        match = _DBNAME_RE.search(ds)
        if not match:
            return None
        return _resolve(next(g for g in match.groups() if g is not None), base_dir)

    if provider == "delimitedtext" or provider == "gpx":
        return _resolve(ds.split("?", 1)[0], base_dir)

    if provider in ("vectortile", "xyzvectortiles", "mbtilesvectortiles"):
        params = dict(urllib.parse.parse_qsl(ds, keep_blank_values=True))
        url = params.get("url", "")
        if url.startswith("file:") or (url and not re.match(r"^[a-z]+://", url)):
            return _resolve(url, base_dir)
        return None

    path = ds.split("|", 1)[0]
    if path.lower().startswith("/vsi"):
        return _resolve(_strip_vsi(path), base_dir)
    if re.match(r"^[a-z][a-z0-9+.-]*://", path, re.IGNORECASE) and not path.lower().startswith("file://"):
        return None
    match = _DRIVER_PREFIX_RE.match(path)
    if match and provider in ("gdal", "mdal", "ogr", "pdal") and not path.lower().startswith("file:"):
        return _file_from_driver_syntax(match.group(2), base_dir)
    return _resolve(path, base_dir)


def sidecar_files(path: str) -> List[str]:
    """
    Arquivos que acompanham uma fonte de dados e existem no disco.

    Inclui componentes de shapefile, world files, ``.aux.xml``, ``.ovr``,
    ``.msk``, estilo padrão ``.qml``, metadados ``.qmd`` e arquivos
    referenciados por ``.vrt`` (recursivamente).
    """
    found: List[str] = []
    stem, ext = os.path.splitext(path)
    ext = ext.lower()
    candidates: List[str] = [stem + ".qml", stem + ".qmd", path + ".aux.xml", path + ".ovr", path + ".msk",
                             stem + ".aux.xml", path + ".xml"]
    if ext == ".shp":
        candidates += [stem + e for e in SHAPEFILE_SIDECARS] + [stem + e.upper() for e in SHAPEFILE_SIDECARS]
    candidates += [stem + e for e in WORLD_FILE_EXTENSIONS]
    seen = {os.path.normcase(path)}
    for cand in candidates:
        key = os.path.normcase(cand)
        if key not in seen and os.path.isfile(cand):
            seen.add(key)
            found.append(cand)
    if ext == ".vrt":
        for src in vrt_sources(path):
            if os.path.normcase(src) not in seen:
                seen.add(os.path.normcase(src))
                found.append(src)
                found.extend(s for s in sidecar_files(src) if os.path.normcase(s) not in seen)
    return found


def vrt_sources(vrt_path: str) -> List[str]:
    """Arquivos de origem referenciados por um VRT do GDAL que existem no disco."""
    try:
        root = ET.parse(vrt_path).getroot()
    except (ET.ParseError, OSError):
        return []
    base = os.path.dirname(vrt_path)
    result: List[str] = []
    for elem in root.iter("SourceFilename"):
        text = (elem.text or "").strip()
        if not text:
            continue
        relative = elem.get("relativeToVRT", "0") == "1"
        path = _strip_vsi(text)
        path = os.path.normpath(os.path.join(base, path)) if relative or not os.path.isabs(path) else path
        if os.path.isfile(path) and path not in result:
            result.append(path)
    return result


def _iter_resource_values(root: ET.Element) -> Iterable[str]:
    """Valores de atributos e de ``<Option value=...>`` com extensão de recurso."""
    for elem in root.iter():
        for value in elem.attrib.values():
            if len(value) < 260 * 4 and os.path.splitext(value)[1].lower() in RESOURCE_EXTENSIONS \
                    and not value.startswith(("base64:", "http://", "https://", ":/")):
                yield value


def inventory_project(project_path: str) -> ProjectInventory:
    """
    Inventaria um projeto QGIS sem carregar camadas.

    :param project_path: caminho do .qgz/.qgs.
    :returns: inventário; erros de leitura vão para ``warnings``.
    """
    project_path = os.path.abspath(project_path)
    inv = ProjectInventory(project_path=project_path)
    base_dir = os.path.dirname(project_path)
    seen: Set[str] = set()

    def add(path: str, role: str, layer: str = "") -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        seen.add(key)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        inv.files.append(InventoryFile(path=path, role=role, layer=layer, size=size))

    if not os.path.isfile(project_path):
        inv.missing.append(("(projeto)", project_path))
        return inv
    add(project_path, "projeto")
    stem = os.path.splitext(project_path)[0]
    for extra in (stem + ".qgd", project_path + ".qgd"):
        if os.path.isfile(extra):
            add(extra, "auxiliar", "(armazenamento auxiliar)")

    try:
        root = ET.fromstring(read_project_xml(project_path))
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        inv.warnings.append(f"Não foi possível ler o projeto: {exc}")
        return inv

    for layer in root.iter("maplayer"):
        name = (layer.findtext("layername") or layer.findtext("id") or "").strip()
        provider = (layer.findtext("provider") or "").strip().lower()
        datasource = layer.findtext("datasource") or ""
        if layer.get("embedded") == "1":
            ref = layer.get("project", "")
            if ref:
                path = _resolve(ref, base_dir)
                (add(path, "recurso", f"{name} (projeto incorporado)") if os.path.isfile(path)
                 else inv.missing.append((name, path)))
            continue
        if provider in MEMORY_PROVIDERS:
            inv.memory.append(name)
            continue
        if provider in REMOTE_PROVIDERS:
            inv.remote.append((name, provider, sanitize_uri(datasource)))
            continue
        if datasource.strip().lower().startswith("localized:"):
            inv.missing.append((name, datasource.strip()))
            continue
        path = datasource_to_path(provider, datasource, base_dir)
        if path is None:
            inv.remote.append((name, provider or "?", sanitize_uri(datasource)))
            continue
        if not os.path.exists(path):
            inv.missing.append((name, path))
            continue
        if os.path.isdir(path):  # ex.: File GDB, pastas de tiles
            for dirpath, _dirs, files in os.walk(path):
                for fname in files:
                    add(os.path.join(dirpath, fname), "dados", name)
            continue
        add(path, "dados", name)
        for side in sidecar_files(path):
            add(side, "auxiliar", name)

    for value in _iter_resource_values(root):
        path = _resolve(value, base_dir)
        if os.path.isfile(path):
            add(path, "recurso")

    return inv


def find_projects(folder: str, max_files: int = 10000) -> List[str]:
    """Lista recursivamente .qgz/.qgs em uma pasta (ignora backups ``~``)."""
    found: List[str] = []
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in files:
            if fname.lower().endswith((".qgz", ".qgs")):
                found.append(os.path.join(dirpath, fname))
                if len(found) >= max_files:
                    return sorted(found)
    return sorted(found)


def summarize(inventories: Iterable[ProjectInventory]) -> Dict[str, int]:
    """Totais para exibição: arquivos únicos, bytes, remotas, ausentes."""
    unique: Dict[str, int] = {}
    remote = missing = 0
    for inv in inventories:
        for f in inv.files:
            unique[os.path.normcase(f.path)] = f.size
        remote += len(inv.remote)
        missing += len(inv.missing)
    return {"files": len(unique), "bytes": sum(unique.values()), "remote": remote, "missing": missing}
