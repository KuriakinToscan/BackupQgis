# -*- coding: utf-8 -*-
"""
Leitura de dependências declaradas por plugins e localização do
interpretador Python usado pelo QGIS.

Não importa QGIS nem Qt.
"""

import configparser
import importlib.util
import logging
import os
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Optional

try:  # Python >= 3.8
    from importlib import metadata as importlib_metadata
except ImportError:  # pragma: no cover - QGIS muito antigo
    importlib_metadata = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

# Nome da distribuição no PyPI -> nome do módulo importável, usado apenas
# quando a distribuição não tem metadados instalados (ex.: GDAL do OSGeo4W).
IMPORT_MAP = {
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "scikit-image": "skimage",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "pyshp": "shapefile",
    "python-dateutil": "dateutil",
    "ruamel.yaml": "ruamel",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "opencv-contrib-python": "cv2",
    "pyjwt": "jwt",
    "gdal": "osgeo",
    "psycopg2-binary": "psycopg2",
    "protobuf": "google.protobuf",
}

# Campos não oficiais de metadata.txt que alguns plugins usam para listar
# pacotes Python.
PYTHON_DEP_FIELDS = ("python_dependencies", "pythondependencies", "pip_dependencies", "python_packages")

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SPEC_RE = re.compile(r"(===|==|~=|!=|>=|<=|>|<)")


def parse_requirement_name(line: str) -> Optional[str]:
    """
    Extrai o nome da distribuição de uma linha no formato de ``requirements.txt``.

    Trata comentários, extras (``pkg[x]``), marcadores (``; python_version``),
    especificadores (``==``, ``~=``, ``!=``, ``>=``...) e ignora opções
    (``-r``, ``--index-url``), URLs e caminhos locais.

    :param line: linha bruta.
    :returns: nome da distribuição ou ``None`` se a linha não declarar pacote.
    """
    line = line.split("#", 1)[0].strip()
    if " @ " in line:  # "pkg @ https://..."
        line = line.split(" @ ", 1)[0]
    if not line or line.startswith("-") or "://" in line or line.startswith((".", "/")):
        return None
    line = line.split(";", 1)[0]
    match = _NAME_RE.match(line)
    return match.group(1) if match else None


def parse_requirements_text(text: str) -> List[str]:
    """
    Lista as distribuições declaradas em um texto de ``requirements.txt``.

    :param text: conteúdo do arquivo.
    :returns: nomes únicos, na ordem de aparição.
    """
    names: List[str] = []
    for line in text.splitlines():
        name = parse_requirement_name(line)
        if name and name not in names:
            names.append(name)
    return names


def parse_plugin_dependencies(value: str) -> List[str]:
    """
    Lê o campo ``plugin_dependencies`` de metadata.txt.

    O campo lista **nomes** de plugins (não pastas), separados por vírgula,
    opcionalmente com versão (``Nome==1.0``).

    :param value: valor do campo.
    :returns: nomes de plugins sem especificador de versão.
    """
    result: List[str] = []
    for item in value.split(","):
        name = _SPEC_RE.split(item, 1)[0].strip()
        if name and name not in result:
            result.append(name)
    return result


def read_plugin_dependencies(plugin_dir: str) -> Dict[str, List[str]]:
    """
    Lê dependências de plugins QGIS e de pacotes Python de um plugin instalado.

    :param plugin_dir: pasta do plugin.
    :returns: ``{"qgis": [nomes de plugins], "python": [distribuições], "warnings": [...]}``.
    """
    deps: Dict[str, List[str]] = {"qgis": [], "python": [], "warnings": []}

    metadata_file = os.path.join(plugin_dir, "metadata.txt")
    if os.path.exists(metadata_file):
        try:
            config = configparser.ConfigParser(interpolation=None)
            with open(metadata_file, "r", encoding="utf-8", errors="replace") as handle:
                config.read_file(handle)
            deps["qgis"] = parse_plugin_dependencies(config.get("general", "plugin_dependencies", fallback=""))
            for field in PYTHON_DEP_FIELDS:
                raw = config.get("general", field, fallback="")
                for name in parse_requirements_text(raw.replace(",", "\n")):
                    if name not in deps["python"]:
                        deps["python"].append(name)
        except (configparser.Error, OSError) as exc:
            deps["warnings"].append(f"metadata.txt ilegível: {exc}")

    reqs_file = os.path.join(plugin_dir, "requirements.txt")
    if os.path.exists(reqs_file):
        try:
            with open(reqs_file, "r", encoding="utf-8", errors="replace") as handle:
                for name in parse_requirements_text(handle.read()):
                    if name not in deps["python"]:
                        deps["python"].append(name)
        except OSError as exc:
            deps["warnings"].append(f"requirements.txt ilegível: {exc}")

    return deps


def is_distribution_installed(name: str) -> bool:
    """
    Verifica se uma distribuição Python está disponível **sem importá-la**.

    Consulta primeiro os metadados instalados (``importlib.metadata``) e, se
    não houver, procura o módulo com ``importlib.util.find_spec``, que não
    executa o código do pacote.

    :param name: nome da distribuição no PyPI.
    """
    if importlib_metadata is not None:
        try:
            importlib_metadata.distribution(name)
            return True
        except importlib_metadata.PackageNotFoundError:
            pass
        except Exception as exc:  # metadados corrompidos
            LOGGER.debug("Falha ao ler metadados de %s: %s", name, exc)

    module = IMPORT_MAP.get(name.lower(), name.replace("-", "_"))
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def python_interpreter_candidates(
    executable: Optional[str] = None,
    prefixes: Optional[Iterable[str]] = None,
    version: Optional[tuple] = None,
    windows: Optional[bool] = None,
) -> List[str]:
    """
    Lista caminhos possíveis do interpretador Python embutido no QGIS.

    Dentro do QGIS, ``sys.executable`` pode apontar para o próprio QGIS
    (``qgis-bin.exe`` no Windows, binário do app no macOS); usá-lo com
    ``-m pip`` abriria outra instância do QGIS.

    :param executable: valor de ``sys.executable`` (padrão: o atual).
    :param prefixes: prefixos a examinar (padrão: exec_prefix, base_exec_prefix, prefix).
    :param version: (major, minor) do Python em execução.
    :param windows: força a convenção de nomes do Windows.
    :returns: candidatos na ordem de preferência, sem duplicados (não verifica existência).
    """
    executable = sys.executable if executable is None else executable
    version = tuple(sys.version_info[:2]) if version is None else version
    windows = (os.name == "nt") if windows is None else windows
    if prefixes is None:
        prefixes = [sys.exec_prefix, getattr(sys, "base_exec_prefix", sys.exec_prefix), sys.prefix]

    join = (lambda *p: "\\".join(p)) if windows and os.name != "nt" else os.path.join
    candidates: List[str] = []

    if executable and os.path.basename(executable).lower().startswith("python"):
        candidates.append(executable)

    major, minor = version[0], version[1]
    for prefix in prefixes:
        if not prefix:
            continue
        if windows:
            names = [join(prefix, "python.exe"), join(prefix, "python3.exe"), join(prefix, "bin", "python.exe")]
        else:
            names = [join(prefix, "bin", f"python{major}.{minor}"), join(prefix, "bin", "python3"),
                     join(prefix, "bin", "python")]
        candidates.extend(names)

    if executable:
        exe_dir = os.path.dirname(executable)
        if windows:
            candidates.append(join(exe_dir, "python.exe"))
            candidates.append(join(exe_dir, "python3.exe"))
        else:
            candidates.append(join(exe_dir, f"python{major}.{minor}"))
            candidates.append(join(exe_dir, "python3"))

    unique: List[str] = []
    for cand in candidates:
        if cand not in unique:
            unique.append(cand)
    return unique


def subprocess_flags() -> int:
    """
    Flags de criação de processo que evitam abrir janela de console no Windows.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_python_interpreter(timeout: float = 20.0) -> Optional[str]:
    """
    Localiza um interpretador Python com a mesma versão (major.minor) do QGIS.

    Cada candidato é validado executando ``-c`` e comparando a versão.

    :param timeout: limite em segundos por candidato.
    :returns: caminho do interpretador ou ``None`` se nenhum for válido.
    """
    expected = f"{sys.version_info[0]}.{sys.version_info[1]}"
    for cand in python_interpreter_candidates():
        if not os.path.isfile(cand):
            continue
        try:
            out = subprocess.run(
                [cand, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess_flags(), encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.debug("Candidato %s falhou: %s", cand, exc)
            continue
        if out.returncode == 0 and out.stdout.strip() == expected:
            return cand
    return None
