# -*- coding: utf-8 -*-
"""
Funções compartilhadas entre as janelas e as automações.

As preferências ficam em dois grupos independentes:

- ``BackupQgis/config``: backup das configurações do QGIS (plugins, conexões,
  autenticação), exportação automática e histórico;
- ``BackupQgis/projects``: backup de projetos, versões, snapshots agendados;
  os pares de sincronização ficam em ``BackupQgis/sync``.
"""

import datetime as _dt
import json
import os
import platform
import socket
from typing import Any, Dict, List

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QSettings

CONFIG_GROUP = "BackupQgis/config"
PROJECTS_GROUP = "BackupQgis/projects"
SYNC_PAIRS_KEY = "BackupQgis/sync/pairs"
HISTORY_LIMIT = 50

# Chaves da 0.3.0 (grupo único "auto") -> grupos novos
_MIGRATIONS = {
    "BackupQgis/auto/exportOnQuit": f"{CONFIG_GROUP}/exportOnQuit",
    "BackupQgis/auto/exportDir": f"{CONFIG_GROUP}/exportDir",
    "BackupQgis/auto/exportKeep": f"{CONFIG_GROUP}/exportKeep",
    "BackupQgis/auto/versionOnSave": f"{PROJECTS_GROUP}/versionOnSave",
    "BackupQgis/auto/versionKeep": f"{PROJECTS_GROUP}/versionKeep",
    "BackupQgis/lastDir": f"{CONFIG_GROUP}/lastDir",
}


def as_bool(value: Any) -> bool:
    """Booleano gravado no QSettings (``True``/``"true"``/``"1"``)."""
    return value if isinstance(value, bool) else str(value).lower() in ("true", "1")


def as_int(value: Any, default: int) -> int:
    """Inteiro gravado no QSettings, com valor padrão se ausente ou inválido."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def migrate_settings(settings: Any = None) -> List[str]:
    """
    Move as preferências da 0.3.0 para os grupos separados. Idempotente: só
    copia quando a chave nova ainda não existe, e remove a antiga.

    :returns: chaves migradas.
    """
    settings = settings or QSettings()
    moved: List[str] = []
    for old, new in _MIGRATIONS.items():
        if settings.contains(old):
            if not settings.contains(new):
                settings.setValue(new, settings.value(old))
                moved.append(new)
            settings.remove(old)
    return moved


def load_config_history(settings: Any = None) -> List[Dict[str, Any]]:
    """Histórico de backups de configuração, do mais recente para o mais antigo."""
    settings = settings or QSettings()
    try:
        items = json.loads(str(settings.value(f"{CONFIG_GROUP}/history", "[]")))
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("path")]


def add_config_history(entry: Dict[str, Any], settings: Any = None) -> None:
    """
    Registra um backup de configuração no histórico (limitado a ``HISTORY_LIMIT``).

    :param entry: ``{"path", "kind" ("manual"|"automático"), "plugins", "connections", "secrets"}``;
        ``created_at`` é preenchido se ausente.
    """
    settings = settings or QSettings()
    entry = dict(entry)
    entry.setdefault("created_at", _dt.datetime.now().replace(microsecond=0).isoformat())
    items = [i for i in load_config_history(settings) if os.path.normcase(i["path"]) != os.path.normcase(entry["path"])]
    items.insert(0, entry)
    settings.setValue(f"{CONFIG_GROUP}/history", json.dumps(items[:HISTORY_LIMIT], ensure_ascii=False))


def source_info(iface: Any) -> Dict[str, str]:
    """Dados da máquina de origem gravados nos backups."""
    profile = ""
    try:
        profile = iface.userProfileManager().userProfile().name()
    except Exception:
        pass
    return {
        "qgis_version": str(getattr(Qgis, "QGIS_VERSION", "")),
        "os": platform.platform(),
        "hostname": socket.gethostname(),
        "profile": profile,
        "python": platform.python_version(),
    }


def sync_state_path(pair_id: str) -> str:
    """Arquivo de estado da sincronização bidirecional de um par (na pasta do perfil)."""
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "backupqgis", "sync_state", f"{pair_id}.json")
