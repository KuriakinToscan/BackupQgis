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
from typing import Any, Dict, List, Optional

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QSettings, QStandardPaths

from ..core.compat import enum_value
from ..core.destinations import default_dest, is_within, overlap_message

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


def documents_dir() -> str:
    """Pasta Documentos do usuário (ou a pasta pessoal, se o sistema não informar)."""
    try:
        location = QStandardPaths.writableLocation(enum_value(QStandardPaths, "StandardLocation",
                                                              "DocumentsLocation"))
    except (AttributeError, TypeError):
        location = ""
    return location or os.path.expanduser("~")


def default_config_dir() -> str:
    """Pasta padrão dos backups de configuração: ``<Documentos>/BackupQgis/configuracoes``."""
    return default_dest("config", documents_dir())


def default_projects_dir() -> str:
    """Pasta padrão dos backups de projetos: ``<Documentos>/BackupQgis/projetos``."""
    return default_dest("projects", documents_dir())


def config_auto_dir(settings: Any = None) -> str:
    """Pasta da exportação automática das configurações (vazia se não configurada)."""
    settings = settings or QSettings()
    return str(settings.value(f"{CONFIG_GROUP}/exportDir", "") or "").strip()


def projects_dir(settings: Any = None) -> str:
    """Pasta de destino do backup de projetos (vazia se não configurada)."""
    settings = settings or QSettings()
    return str(settings.value(f"{PROJECTS_GROUP}/dest", "") or "").strip()


def config_conflict(folder: str, settings: Any = None) -> Optional[str]:
    """Aviso se ``folder`` (backup de configurações) conflita com a pasta do backup de projetos."""
    return overlap_message(folder, projects_dir(settings), "backup de projetos")


def projects_conflict(folder: str, settings: Any = None) -> Optional[str]:
    """Aviso se ``folder`` (backup de projetos) conflita com a pasta do backup de configurações."""
    return overlap_message(folder, config_auto_dir(settings), "backup de configurações")


def config_save_conflict(path: str, settings: Any = None) -> Optional[str]:
    """Aviso se o arquivo de backup de configurações seria gravado dentro da pasta do backup de projetos."""
    dest = projects_dir(settings)
    if is_within(os.path.dirname(path), dest):
        return (f"{path} fica dentro da pasta do backup de projetos ({dest}). "
                f"Grave o backup de configurações em outra pasta, por exemplo {default_config_dir()}.")
    return None


def ensure_dir(folder: str) -> bool:
    """Cria a pasta se for uma das pastas padrão; retorna ``True`` se ela existe."""
    if not os.path.isdir(folder) and os.path.normcase(os.path.abspath(folder)) in {
            os.path.normcase(default_config_dir()), os.path.normcase(default_projects_dir())}:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return False
    return os.path.isdir(folder)


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
