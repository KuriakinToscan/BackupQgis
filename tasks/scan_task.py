# -*- coding: utf-8 -*-
"""
Varredura de plugins, configurações e conexões em segundo plano.
"""

from typing import Any, Callable, Dict, List, Optional

from qgis.core import QgsTask
from qgis.PyQt.QtCore import QSettings

from ..core.compat import enum_value
from ..core.keys import SettingsIndex, is_detail_key
from ..core.plugins import scan_installed_plugins


class ScanResult:
    """Resultado da varredura, consumido pela interface na thread principal."""

    def __init__(self) -> None:
        self.plugins: List[Dict[str, Any]] = []
        self.plugin_key_counts: Dict[str, int] = {}
        self.connections: List[Dict[str, Any]] = []
        self.repository_keys: List[str] = []
        self.index: Optional[SettingsIndex] = None
        self.warnings: List[str] = []
        self.error: Optional[str] = None


class ScanTask(QgsTask):
    """
    Lê ``QSettings.allKeys()`` uma única vez, indexa as chaves e varre as
    pastas de plugins. Uma instância nova de ``QSettings`` é criada na
    thread da tarefa (QSettings é reentrante, não compartilhável entre threads).
    """

    def __init__(self, on_done: Callable[["ScanTask"], None]) -> None:
        """
        :param on_done: chamado na thread principal ao terminar (com sucesso,
            erro ou cancelamento); consulte ``self.result``.
        """
        super().__init__("BackupQgis: lendo plugins e configurações", enum_value(QgsTask, "Flag", "CanCancel"))
        self._on_done = on_done
        self.result = ScanResult()

    def run(self) -> bool:
        res = self.result
        try:
            settings = QSettings()
            self.setProgress(5)
            res.index = SettingsIndex(settings.allKeys())
            if self.isCanceled():
                return False
            self.setProgress(35)

            res.plugins, res.warnings = scan_installed_plugins(settings)
            for plugin in res.plugins:
                res.plugin_key_counts[plugin["key"]] = len(res.index.plugin_keys(plugin["key"]))
            if self.isCanceled():
                return False
            self.setProgress(70)

            for (label, name), keys in sorted(res.index.group_connections().items(),
                                              key=lambda item: (item[0][0].lower(), item[0][1].lower())):
                details = ""
                for key in keys:
                    if is_detail_key(key):
                        details = str(settings.value(key) or "")
                        if details:
                            break
                res.connections.append({
                    "id": f"{label}::{name}",
                    "provider_type": label,
                    "name": name,
                    "details": details,
                    "keys": keys,
                })
            res.repository_keys = res.index.repository_keys()
            self.setProgress(100)
            return True
        except Exception as exc:  # erro inesperado não pode derrubar o QGIS
            res.error = f"{type(exc).__name__}: {exc}"
            return False

    def finished(self, result: bool) -> None:  # thread principal
        if not result and not self.result.error and self.isCanceled():
            self.result.error = "Leitura cancelada."
        self._on_done(self)
