# -*- coding: utf-8 -*-
"""
Funções compartilhadas entre o diálogo e a automação.
"""

import os
import platform
import socket
from typing import Any, Dict

from qgis.core import Qgis, QgsApplication

AUTO_GROUP = "BackupQgis/auto"


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
