# -*- coding: utf-8 -*-
"""
Classe principal do plugin BackupQgis: registra a ação no menu e na barra de
ferramentas e abre o diálogo.
"""

import os
from typing import Any, Optional

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU_NAME = "BackupQgis"


class BackupPlugin:
    """Ponto de entrada carregado pelo QGIS via ``classFactory``."""

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action: Optional[QAction] = None
        self.dialog = None
        self.automation = None

    def tr(self, message: str) -> str:
        return QCoreApplication.translate("BackupPlugin", message)

    def initGui(self) -> None:
        """Adiciona a ação ao menu Plugins e à barra de ferramentas de plugins."""
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, self.tr("BackupQgis: plugins, configurações e conexões..."),
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.tr(MENU_NAME), self.action)
        self.iface.addToolBarIcon(self.action)

        from .ui.automation import Automation
        from .ui.dialog import read_own_version
        try:
            self.automation = Automation(self.iface, read_own_version(self.plugin_dir))
        except Exception as exc:  # automação nunca pode impedir o carregamento do plugin
            from qgis.core import QgsMessageLog
            QgsMessageLog.logMessage(f"Automação desativada: {exc}", "BackupQgis")
            self.automation = None

    def unload(self) -> None:
        """Remove a ação e encerra tarefas em andamento antes de o plugin ser descarregado."""
        if self.automation is not None:
            self.automation.stop()
            self.automation.deleteLater()
            self.automation = None
        if self.action:
            self.iface.removePluginMenu(self.tr(MENU_NAME), self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dialog is not None:
            try:
                self.dialog.shutdown()
                self.dialog.deleteLater()
            except RuntimeError:
                pass
            self.dialog = None

    def run(self) -> None:
        """Abre (ou traz para frente) o diálogo principal."""
        from .ui.dialog import BackupPluginDialog

        if self.dialog is None:
            self.dialog = BackupPluginDialog(self.iface, self.plugin_dir)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
