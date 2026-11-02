# -*- coding: utf-8 -*-
"""
Varredura de plugins Python instalados.

A função :func:`scan_plugin_dirs` é pura (sem QGIS) para facilitar testes;
:func:`scan_installed_plugins` obtém os caminhos e o estado de ativação do QGIS.
"""

import configparser
import os
from typing import Callable, Dict, Iterable, List, Optional, Tuple

PluginInfo = Dict[str, object]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def scan_plugin_dirs(
    paths: Iterable[str],
    core_paths: Iterable[str],
    is_active: Callable[[str], bool],
) -> Tuple[List[PluginInfo], List[str]]:
    """
    Lê os metadados de cada plugin encontrado nas pastas informadas.

    :param paths: pastas de plugins (ex.: ``qgis.utils.plugin_paths``).
    :param core_paths: pastas de plugins do núcleo do QGIS (não instaláveis
        pelo repositório: processing, db_manager, MetaSearch...).
    :param is_active: função que diz se o plugin está ativo.
    :returns: (lista de plugins, avisos). Cada plugin tem as chaves
        ``key, name, version, description, active, core, path``.
        Se a mesma pasta existir em dois caminhos, vale a primeira (mesma
        precedência do QGIS).
    """
    core_set = {_norm(p) for p in core_paths if p}
    plugins: List[PluginInfo] = []
    warnings: List[str] = []
    seen = set()

    for path in paths:
        if not path or not os.path.isdir(path):
            continue
        is_core = _norm(path) in core_set
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError as exc:
            warnings.append(f"Não foi possível listar {path}: {exc}")
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith(("__", ".")) or entry.name in seen:
                continue
            metadata_file = os.path.join(entry.path, "metadata.txt")
            if not os.path.isfile(metadata_file):
                continue
            seen.add(entry.name)
            name, version, description = entry.name, "Desconhecida", ""
            try:
                config = configparser.ConfigParser(interpolation=None)
                with open(metadata_file, "r", encoding="utf-8", errors="replace") as handle:
                    config.read_file(handle)
                name = config.get("general", "name", fallback=entry.name)
                version = config.get("general", "version", fallback=version)
                description = config.get("general", "description", fallback="")
            except (configparser.Error, OSError) as exc:
                warnings.append(f"Metadados ilegíveis em '{entry.name}': {exc}")
            plugins.append({
                "key": entry.name,
                "name": name,
                "version": version,
                "description": description,
                "active": bool(is_active(entry.name)),
                "core": is_core,
                "path": entry.path,
            })
    return plugins, warnings


def core_plugin_paths() -> List[str]:
    """
    Pastas de plugins Python que acompanham a instalação do QGIS.
    """
    from qgis.core import QgsApplication
    return [os.path.join(QgsApplication.pkgDataPath(), "python", "plugins")]


def scan_installed_plugins(settings=None) -> Tuple[List[PluginInfo], List[str]]:
    """
    Varre os plugins instalados no QGIS em execução.

    :param settings: ``QSettings`` a usar (útil em threads; padrão: novo QSettings).
    :returns: (plugins, avisos).
    """
    import qgis.utils
    from qgis.PyQt.QtCore import QSettings

    settings = settings or QSettings()

    def active(key: str) -> bool:
        value = settings.value(f"PythonPlugins/{key}", False)
        return str(value).lower() in ("true", "1") if not isinstance(value, bool) else value

    paths = list(getattr(qgis.utils, "plugin_paths", []))
    return scan_plugin_dirs(paths, core_plugin_paths(), active)


def resolve_plugin_name(name_or_key: str, plugins: Iterable[PluginInfo]) -> Optional[PluginInfo]:
    """
    Localiza um plugin pelo nome exibido ou pela pasta, sem diferenciar maiúsculas.

    O campo ``plugin_dependencies`` usa nomes; a tabela local usa pastas.
    """
    target = name_or_key.strip().lower()
    for plugin in plugins:
        if str(plugin["key"]).lower() == target or str(plugin["name"]).lower() == target:
            return plugin
    return None
