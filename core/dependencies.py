# -*- coding: utf-8 -*-
"""
Leitura e verificação de dependências declaradas pelos plugins
(plugins QGIS em metadata.txt e pacotes Python em metadata.txt/requirements.txt).
"""

import os
import importlib
import configparser

# Mapeamento comum de pacotes PyPI para nomes de importação Python
IMPORT_MAP = {
    'beautifulsoup4': 'bs4',
    'scikit-learn': 'sklearn',
    'pillow': 'PIL',
    'pyyaml': 'yaml',
    'pyshp': 'shapefile',
    'python-dateutil': 'dateutil',
    'ruamel.yaml': 'ruamel',
    'opencv-python': 'cv2',
    'pyjwt': 'jwt',
    'sqlalchemy': 'sqlalchemy',
    'matplotlib': 'matplotlib',
    'numpy': 'numpy',
    'pandas': 'pandas',
    'scipy': 'scipy',
    'osgeo': 'osgeo',
    'psycopg2-binary': 'psycopg2'
}

# Campos customizados de metadata.txt usados por alguns plugins para declarar pacotes pip
PYTHON_DEPENDENCY_FIELDS = ['python_dependencies', 'pythondependencies', 'pip_dependencies', 'python_packages']

DEP_QGIS = 'qgis'
DEP_PYTHON = 'python'

# Nomes de plugins podem conter espaços, por isso não cortamos em ' ' nem em '<'/'>'
_QGIS_SPEC_SEPARATORS = ('==', '>=', '<=')
_PYTHON_SPEC_SEPARATORS = ('==', '>=', '<=', '>', '<', ' ')


def _strip_spec(spec, separators):
    for sep in separators:
        spec = spec.split(sep)[0]
    return spec.strip()


def strip_qgis_spec(spec):
    """
    'MeuPlugin==1.2' -> 'MeuPlugin'
    """
    return _strip_spec(spec.strip(), _QGIS_SPEC_SEPARATORS)


def strip_python_spec(spec):
    """
    'requests>=2.20' -> 'requests'
    """
    return _strip_spec(spec.strip(), _PYTHON_SPEC_SEPARATORS)


def extract_plugin_dependencies(plugin_dir):
    """
    Lê dependências QGIS e Python declaradas em metadata.txt e requirements.txt.
    """
    deps = {DEP_QGIS: [], DEP_PYTHON: []}

    # 1. Analisa metadata.txt
    metadata_file = os.path.join(plugin_dir, 'metadata.txt')
    if os.path.exists(metadata_file):
        try:
            config = configparser.ConfigParser()
            with open(metadata_file, 'r', encoding='utf-8', errors='ignore') as f:
                config.read_file(f)

            # Dependências QGIS
            qgis_deps_str = config.get('general', 'plugin_dependencies', fallback='')
            for p in qgis_deps_str.split(','):
                p_key = strip_qgis_spec(p)
                if p_key:
                    deps[DEP_QGIS].append(p_key)

            # Dependências Python nos metadados (customizadas)
            for field in PYTHON_DEPENDENCY_FIELDS:
                py_deps_str = config.get('general', field, fallback='')
                for pkg in py_deps_str.split(','):
                    clean_pkg = strip_python_spec(pkg)
                    if clean_pkg:
                        deps[DEP_PYTHON].append(clean_pkg)
        except Exception:
            pass

    # 2. Analisa requirements.txt
    reqs_file = os.path.join(plugin_dir, 'requirements.txt')
    if os.path.exists(reqs_file):
        try:
            with open(reqs_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    pkg = strip_python_spec(line)
                    if pkg:
                        deps[DEP_PYTHON].append(pkg)
        except Exception:
            pass

    # Remove duplicados
    deps[DEP_QGIS] = list(set(deps[DEP_QGIS]))
    deps[DEP_PYTHON] = list(set(deps[DEP_PYTHON]))
    return deps


def import_name_for(package):
    """
    Nome usado no 'import' para um pacote PyPI.
    """
    return IMPORT_MAP.get(package.lower(), package)


def is_python_package_installed(package):
    try:
        importlib.import_module(import_name_for(package))
        return True
    except Exception:
        return False


def find_dependencies(plugins, installed_plugin_keys, python_checker=is_python_package_installed):
    """
    Lista as dependências de todos os plugins ativos, marcando as ausentes.

    Retorna dicts com: source, kind (DEP_QGIS/DEP_PYTHON), name, is_missing.
    """
    installed_plugin_keys = set(installed_plugin_keys)
    dependencies = []

    for plugin in plugins:
        # Analisa apenas dependências de plugins que estão ativos
        if not plugin['active']:
            continue

        p_deps = extract_plugin_dependencies(plugin['path'])

        for qgis_dep in p_deps[DEP_QGIS]:
            dependencies.append({
                'source': plugin['name'],
                'kind': DEP_QGIS,
                'name': qgis_dep,
                'is_missing': qgis_dep not in installed_plugin_keys
            })

        for py_dep in p_deps[DEP_PYTHON]:
            dependencies.append({
                'source': plugin['name'],
                'kind': DEP_PYTHON,
                'name': py_dep,
                'is_missing': not python_checker(py_dep)
            })

    return dependencies
