# -*- coding: utf-8 -*-
"""
Classe principal do plugin BackupQgis.

Registra duas entradas independentes no menu e na barra de ferramentas:

- "Configurações do QGIS": plugins, configurações, conexões e autenticação;
- "Projetos e dados": backup de projetos com os dados e sincronização de pastas.

Cada uma tem janela, preferências e automação próprias.
"""

import os
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings, QTranslator
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsMessageLog
from qgis.PyQt.QtWidgets import QAction

MENU_NAME = "BackupQgis"


class BackupPlugin:
    """Ponto de entrada carregado pelo QGIS via ``classFactory``."""

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions: List[QAction] = []
        self.windows: Dict[str, Any] = {}
        self.automations: List[Any] = []
        self.translator: Optional[QTranslator] = None
        self._install_translator()

    def _install_translator(self) -> None:
        """
        Carrega ``i18n/BackupQgis_<idioma>.qm`` conforme o idioma da interface do
        QGIS. Os textos-fonte estão em português; sem arquivo .qm, nada muda.
        """
        locale = str(QSettings().value("locale/userLocale", "") or QLocale.system().name())
        for name in (locale, locale[:2]):
            path = os.path.join(self.plugin_dir, "i18n", f"BackupQgis_{name}.qm")
            if name and os.path.isfile(path):
                translator = QTranslator()
                if translator.load(path):
                    QCoreApplication.installTranslator(translator)
                    self.translator = translator
                return

    def tr(self, message: str) -> str:
        return QCoreApplication.translate("BackupPlugin", message)

    def _add_action(self, icon: QIcon, text: str, tip: str, slot: Any) -> None:
        action = QAction(icon, text, self.iface.mainWindow())
        action.setToolTip(tip)
        action.triggered.connect(slot)
        self.iface.addPluginToMenu(self.tr(MENU_NAME), action)
        self.iface.addToolBarIcon(action)
        self.actions.append(action)

    def initGui(self) -> None:
        """Adiciona as duas entradas ao menu Plugins e à barra de ferramentas de plugins."""
        from .ui.common import migrate_settings
        moved = migrate_settings()
        if moved:
            QgsMessageLog.logMessage(f"Preferências migradas para os grupos separados: {', '.join(moved)}",
                                     "BackupQgis")

        self._add_action(QIcon(os.path.join(self.plugin_dir, "icons", "config.png")),
                         self.tr("Configurações do QGIS (plugins, conexões, autenticação)..."),
                         self.tr("BackupQgis: backup e restauração das configurações do QGIS"),
                         lambda: self.show_window("config"))
        self._add_action(QIcon(os.path.join(self.plugin_dir, "icons", "projetos.png")),
                         self.tr("Projetos e dados (backup e sincronização)..."),
                         self.tr("BackupQgis: backup de projetos com os dados e sincronização de pastas"),
                         lambda: self.show_window("projects"))

        from .ui.automation import ConfigAutomation, ProjectsAutomation
        from .ui.dialog import read_own_version
        version = read_own_version(self.plugin_dir)
        for name, factory in (("configurações", lambda: ConfigAutomation(self.iface, version)),
                              ("projetos", lambda: ProjectsAutomation(self.iface))):
            try:
                self.automations.append(factory())
            except Exception as exc:  # automação nunca pode impedir o carregamento do plugin
                QgsMessageLog.logMessage(f"Automação de {name} desativada: {exc}", "BackupQgis")

    def unload(self) -> None:
        """Remove as ações e encerra tarefas em andamento antes de o plugin ser descarregado."""
        for automation in self.automations:
            try:
                automation.stop()
                automation.deleteLater()
            except RuntimeError:
                pass
        self.automations = []
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(MENU_NAME), action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        for window in self.windows.values():
            try:
                window.shutdown()
                window.deleteLater()
            except RuntimeError:
                pass
        self.windows = {}
        if self.translator is not None:
            QCoreApplication.removeTranslator(self.translator)
            self.translator = None

    def show_window(self, which: str) -> None:
        """Abre (ou traz para frente) a janela ``"config"`` ou ``"projects"``."""
        window = self.windows.get(which)
        if window is None:
            if which == "config":
                from .ui.dialog import BackupPluginDialog
                window = BackupPluginDialog(self.iface, self.plugin_dir)
            else:
                from .ui.projects_dialog import ProjectsDialog
                window = ProjectsDialog(self.iface, self.plugin_dir)
            self.windows[which] = window
        window.show()
        window.raise_()
        window.activateWindow()

    def run(self) -> None:
        """Compatibilidade: abre a janela de configurações."""
        self.show_window("config")
