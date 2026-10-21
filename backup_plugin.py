# -*- coding: utf-8 -*-

import os
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

class BackupPlugin:
    """
    Classe principal do plugin QGIS para Sincronização, Backup e Correção de Pendências.
    """
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def tr(self, message):
        # Tradução simples ou padrão do QGIS
        return QCoreApplication.translate('BackupPlugin', message)

    def initGui(self):
        """
        Adiciona os botões e itens de menu do plugin na interface do QGIS.
        """
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        icon = QIcon(icon_path)
        
        # Cria a ação que abre o diálogo
        self.action = QAction(
            icon,
            self.tr('Sincronizar Plugins e Configurações...'),
            self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)

        # Adiciona a ação ao menu 'Plugins' e à barra de ferramentas de plugins
        self.iface.addPluginToMenu(self.tr('Sincronizador de Plugins'), self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """
        Remove os botões e itens de menu do plugin quando ele é desativado.
        """
        if self.action:
            self.iface.removePluginMenu(self.tr('Sincronizador de Plugins'), self.action)
            self.iface.removeToolBarIcon(self.action)
            
        if self.dialog:
            try:
                self.dialog.shutdown()
            except Exception:
                pass
            self.dialog = None

    def run(self):
        """
        Executa a ação principal do plugin abrindo o diálogo do usuário.
        """
        from .backup_plugin_dialog import BackupPluginDialog
        
        # Cria o diálogo se ele ainda não existir
        if self.dialog is None:
            self.dialog = BackupPluginDialog(self.iface)
            
        # Exibe o diálogo
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
