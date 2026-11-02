# -*- coding: utf-8 -*-
"""
Exportação automática de plugins, configurações, conexões e repositórios
(sem senhas), usada ao fechar o QGIS.
"""

import datetime as _dt
import glob
import os
import socket
from typing import Any, Callable, Dict, List

from qgis.PyQt.QtCore import QSettings

from .backup_file import build_backup_document, write_json_atomic
from .keys import SettingsIndex, is_sensitive_key
from .plugins import scan_installed_plugins
from .settings_io import read_values

PREFIX = "qgis-config_"


def export_all(dest_dir: str, keep: int, plugin_version: str, source_info: Dict[str, Any],
               log: Callable[[str], None]) -> str:
    """
    Exporta plugins ativos (exceto os do núcleo) com suas configurações, todas
    as conexões e os repositórios, omitindo senhas e tokens.

    :param dest_dir: pasta de destino (precisa existir).
    :param keep: quantidade de arquivos automáticos mantidos (``<= 0``: todos).
    :returns: caminho do arquivo gravado.
    :raises OSError: destino inexistente ou não gravável.
    """
    if not os.path.isdir(dest_dir):
        raise OSError(f"Pasta de destino não encontrada: {dest_dir}")
    settings = QSettings()
    index = SettingsIndex(settings.allKeys())
    plugins, warnings = scan_installed_plugins(settings)
    for warning in warnings:
        log(warning)
    selected = [p for p in plugins if p["active"] and not p["core"]]
    omitted: List[str] = []
    fallbacks: List[str] = []

    def keep_keys(keys: List[str]) -> List[str]:
        out = []
        for key in keys:
            (omitted.append(key) if is_sensitive_key(key) else out.append(key))
        return out

    plugin_settings = {p["key"]: read_values(keep_keys(index.plugin_keys(p["key"])), settings, fallbacks)
                       for p in selected}
    providers = []
    for (label, name), keys in index.group_connections().items():
        providers.append({"provider_type": label, "name": name, "details": "",
                          "keys": read_values(keep_keys(keys), settings, fallbacks)})
    repos = read_values(keep_keys(index.repository_keys()), settings, fallbacks)
    doc = build_backup_document(plugin_version=plugin_version, source=source_info, repositories=repos,
                                plugins=selected, plugin_settings=plugin_settings, providers=providers,
                                omitted_sensitive_keys=omitted, fallback_keys=fallbacks)
    host = socket.gethostname().split(".")[0] or "qgis"
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(dest_dir, f"{PREFIX}{host}_{stamp}.json")
    n = 1
    while os.path.exists(path):  # duas exportações no mesmo segundo não se sobrescrevem
        n += 1
        path = os.path.join(dest_dir, f"{PREFIX}{host}_{stamp}_{n}.json")
    write_json_atomic(path, doc)
    if keep > 0:
        mine = sorted(glob.glob(os.path.join(dest_dir, f"{PREFIX}{host}_*.json")))
        for old in mine[: max(0, len(mine) - keep)]:
            try:
                os.remove(old)
            except OSError as exc:
                log(f"Não foi possível remover {old}: {exc}")
    return path
