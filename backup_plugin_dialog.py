# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QDateTime, QSettings
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                             QTableWidget, QTableWidgetItem, QPushButton, QLineEdit,
                             QLabel, QFileDialog, QProgressBar, QTextEdit,
                             QHeaderView, QMessageBox)
from qgis.PyQt.QtGui import QFont, QColor

from .core import plugins as core_plugins
from .core import connections as core_connections
from .core import backup as core_backup
from .core.dependencies import DEP_QGIS, DEP_PYTHON, find_dependencies

# Rótulos exibidos na tabela de pendências para cada tipo de dependência
DEP_LABELS = {DEP_QGIS: 'Plugin QGIS', DEP_PYTHON: 'Pacote Python'}

# ==============================================================================
# Worker assíncrono para instalação e correção de dependências
# ==============================================================================

class DependencyFixerWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, qgis_plugins, python_packages):
        super(DependencyFixerWorker, self).__init__()
        self.qgis_plugins = qgis_plugins
        self.python_packages = python_packages
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_steps = len(self.qgis_plugins) + len(self.python_packages)
        if total_steps == 0:
            self.finished_signal.emit(True, "Nenhuma pendência para corrigir.")
            return

        completed_steps = 0
        self.progress_signal.emit(0)
        
        # 1. Instalar plugins do QGIS ausentes
        if self.qgis_plugins:
            self.log_signal.emit("=== Iniciando instalação de plugins do QGIS ===")
            try:
                import pyplugin_installer
                installer = pyplugin_installer.instance()
                self.log_signal.emit("Atualizando lista de repositórios de plugins do QGIS...")
                installer.fetchAvailablePlugins(False)
            except Exception as e:
                self.log_signal.emit(f"Erro ao inicializar instalador do QGIS: {str(e)}")
                installer = None

            for plugin_key in self.qgis_plugins:
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Operação cancelada pelo usuário.")
                    return
                
                self.log_signal.emit(f"Instalando plugin do QGIS: {plugin_key}...")
                if installer:
                    try:
                        success = installer.installPlugin(plugin_key)
                        if success:
                            self.log_signal.emit(f"Plugin '{plugin_key}' instalado com sucesso!")
                        else:
                            self.log_signal.emit(f"Falha ao instalar o plugin '{plugin_key}' (não encontrado ou erro de download).")
                    except Exception as e:
                        self.log_signal.emit(f"Erro durante instalação do plugin '{plugin_key}': {str(e)}")
                else:
                    self.log_signal.emit(f"Impossível instalar '{plugin_key}': Módulo pyplugin_installer indisponível.")
                
                completed_steps += 1
                self.progress_signal.emit(int((completed_steps / total_steps) * 100))

        # 2. Instalar pacotes Python ausentes via pip
        if self.python_packages:
            self.log_signal.emit("=== Iniciando instalação de pacotes Python ===")
            python_exe = sys.executable
            
            for package in self.python_packages:
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Operação cancelada pelo usuário.")
                    return
                
                self.log_signal.emit(f"Instalando pacote Python via pip: {package}...")
                cmd = [python_exe, "-m", "pip", "install", "--user", package]
                
                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        shell=False
                    )
                    
                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            self.log_signal.emit(f"[pip] {line.strip()}")
                            
                    rc = process.poll()
                    if rc == 0:
                        self.log_signal.emit(f"Pacote Python '{package}' instalado com sucesso!")
                    else:
                        self.log_signal.emit(f"Erro ao instalar pacote '{package}'. Código de saída: {rc}")
                except Exception as e:
                    self.log_signal.emit(f"Erro ao executar pip para '{package}': {str(e)}")
                
                completed_steps += 1
                self.progress_signal.emit(int((completed_steps / total_steps) * 100))
                
        self.finished_signal.emit(True, "Processo de correção concluído!")


# ==============================================================================
# Diálogo principal do Plugin
# ==============================================================================

class BackupPluginDialog(QDialog):
    def __init__(self, iface, parent=None):
        super(BackupPluginDialog, self).__init__(parent)
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.worker = None
        self.backup_data = None
        self.plugins_cache = []
        self.providers_cache = []
        
        self.init_ui()
        self.refresh_export_table()

    def init_ui(self):
        self.setWindowTitle("Sincronizador de Plugins, Configurações e Provedores")
        self.resize(850, 650)
        self.setMinimumSize(700, 550)
        
        main_layout = QVBoxLayout(self)
        
        # Cabeçalho do Plugin
        header_widget = QWidget(self)
        header_widget.setStyleSheet("background-color: #1565C0; border-radius: 4px;")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(15, 10, 15, 10)
        
        title_label = QLabel("Sincronizador de Plugins e Provedores QGIS", header_widget)
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: white;")
        header_layout.addWidget(title_label)
        
        subtitle_label = QLabel("Faça backup, restaure e sincronize seus plugins, configurações e servidores com seleção granular.", header_widget)
        subtitle_label.setStyleSheet("color: #E3F2FD;")
        header_layout.addWidget(subtitle_label)
        
        main_layout.addWidget(header_widget)
        
        # Criação das Abas Principais
        self.tabs = QTabWidget(self)
        main_layout.addWidget(self.tabs)
        
        # =============================================================
        # ABA 1: EXPORTAR BACKUP (COM SUB-ABAS)
        # =============================================================
        self.tab_export = QWidget()
        export_main_layout = QVBoxLayout(self.tab_export)
        
        self.export_subtabs = QTabWidget(self.tab_export)
        
        # Sub-aba 1.1: Plugins e Configurações
        self.subtab_exp_plugins = QWidget()
        exp_plugins_layout = QVBoxLayout(self.subtab_exp_plugins)
        
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filtrar Plugins:", self.subtab_exp_plugins)
        self.export_search = QLineEdit(self.subtab_exp_plugins)
        self.export_search.setPlaceholderText("Pesquise por nome ou pasta do plugin...")
        self.export_search.textChanged.connect(self.filter_export_plugins_table)
        
        btn_sel_all_p = QPushButton("Selecionar Plugins", self.subtab_exp_plugins)
        btn_sel_all_p.clicked.connect(lambda: self.select_all_export_plugins(True))
        btn_clear_p = QPushButton("Limpar Plugins", self.subtab_exp_plugins)
        btn_clear_p.clicked.connect(lambda: self.select_all_export_plugins(False))
        
        btn_sel_all_c = QPushButton("Selecionar Configs", self.subtab_exp_plugins)
        btn_sel_all_c.clicked.connect(lambda: self.select_all_export_configs(True))
        btn_clear_c = QPushButton("Limpar Configs", self.subtab_exp_plugins)
        btn_clear_c.clicked.connect(lambda: self.select_all_export_configs(False))
        
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.export_search)
        filter_layout.addWidget(btn_sel_all_p)
        filter_layout.addWidget(btn_clear_p)
        filter_layout.addWidget(btn_sel_all_c)
        filter_layout.addWidget(btn_clear_c)
        exp_plugins_layout.addLayout(filter_layout)
        
        self.export_table = QTableWidget(self.subtab_exp_plugins)
        self.export_table.setColumnCount(6)
        self.export_table.setHorizontalHeaderLabels(["Plugin", "Configurações", "Nome do Plugin", "Pasta/Chave", "Versão", "Estado no QGIS"])
        self.export_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.export_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.export_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.export_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        exp_plugins_layout.addWidget(self.export_table)
        
        self.export_subtabs.addTab(self.subtab_exp_plugins, "Plugins e Configurações")
        
        # Sub-aba 1.2: Provedores de Conteúdo e Conexões
        self.subtab_exp_providers = QWidget()
        exp_prov_layout = QVBoxLayout(self.subtab_exp_providers)
        
        prov_filter_layout = QHBoxLayout()
        prov_filter_label = QLabel("Filtrar Provedores:", self.subtab_exp_providers)
        self.export_provider_search = QLineEdit(self.subtab_exp_providers)
        self.export_provider_search.setPlaceholderText("Pesquise por tipo ou nome da conexão...")
        self.export_provider_search.textChanged.connect(self.filter_export_providers_table)
        
        btn_sel_all_prov = QPushButton("Selecionar Todos", self.subtab_exp_providers)
        btn_sel_all_prov.clicked.connect(lambda: self.select_all_export_providers(True))
        btn_clear_prov = QPushButton("Limpar Seleção", self.subtab_exp_providers)
        btn_clear_prov.clicked.connect(lambda: self.select_all_export_providers(False))
        
        prov_filter_layout.addWidget(prov_filter_label)
        prov_filter_layout.addWidget(self.export_provider_search)
        prov_filter_layout.addWidget(btn_sel_all_prov)
        prov_filter_layout.addWidget(btn_clear_prov)
        exp_prov_layout.addLayout(prov_filter_layout)
        
        self.export_providers_table = QTableWidget(self.subtab_exp_providers)
        self.export_providers_table.setColumnCount(4)
        self.export_providers_table.setHorizontalHeaderLabels(["Exportar", "Tipo de Provedor", "Nome da Conexão", "URL / Endereço"])
        self.export_providers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.export_providers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.export_providers_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        exp_prov_layout.addWidget(self.export_providers_table)
        
        self.export_subtabs.addTab(self.subtab_exp_providers, "Provedores de Conteúdo (WMS, WFS, STAC, XYZ, DBs)")
        
        export_main_layout.addWidget(self.export_subtabs)
        
        # Botão Inferior de Exportação
        export_btn_layout = QHBoxLayout()
        btn_export = QPushButton("Gerar e Salvar Backup (JSON)...", self.tab_export)
        btn_export.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 8px 16px; font-size: 12px;")
        btn_export.clicked.connect(self.export_backup)
        export_btn_layout.addStretch()
        export_btn_layout.addWidget(btn_export)
        export_main_layout.addLayout(export_btn_layout)
        
        self.tabs.addTab(self.tab_export, "Exportar Backup")
        
        # =============================================================
        # ABA 2: IMPORTAR / RESTAURAR (COM SUB-ABAS)
        # =============================================================
        self.tab_import = QWidget()
        import_main_layout = QVBoxLayout(self.tab_import)
        
        import_top = QHBoxLayout()
        btn_load_file = QPushButton("Carregar Arquivo de Backup...", self.tab_import)
        btn_load_file.clicked.connect(self.load_backup_file)
        self.lbl_backup_info = QLabel("Nenhum arquivo de backup carregado.", self.tab_import)
        self.lbl_backup_info.setStyleSheet("color: #757575; font-style: italic;")
        import_top.addWidget(btn_load_file)
        import_top.addWidget(self.lbl_backup_info)
        import_top.addStretch()
        import_main_layout.addLayout(import_top)
        
        self.import_subtabs = QTabWidget(self.tab_import)
        
        # Sub-aba 2.1: Plugins no Backup
        self.subtab_imp_plugins = QWidget()
        imp_plugins_layout = QVBoxLayout(self.subtab_imp_plugins)
        
        imp_ctrl_layout = QHBoxLayout()
        btn_imp_sel_p = QPushButton("Selecionar Plugins", self.subtab_imp_plugins)
        btn_imp_sel_p.clicked.connect(lambda: self.select_all_import_plugins(True))
        btn_imp_clr_p = QPushButton("Limpar Plugins", self.subtab_imp_plugins)
        btn_imp_clr_p.clicked.connect(lambda: self.select_all_import_plugins(False))
        
        btn_imp_sel_c = QPushButton("Selecionar Configs", self.subtab_imp_plugins)
        btn_imp_sel_c.clicked.connect(lambda: self.select_all_import_configs(True))
        btn_imp_clr_c = QPushButton("Limpar Configs", self.subtab_imp_plugins)
        btn_imp_clr_c.clicked.connect(lambda: self.select_all_import_configs(False))
        
        imp_ctrl_layout.addWidget(btn_imp_sel_p)
        imp_ctrl_layout.addWidget(btn_imp_clr_p)
        imp_ctrl_layout.addWidget(btn_imp_sel_c)
        imp_ctrl_layout.addWidget(btn_imp_clr_c)
        imp_ctrl_layout.addStretch()
        imp_plugins_layout.addLayout(imp_ctrl_layout)
        
        self.import_table = QTableWidget(self.subtab_imp_plugins)
        self.import_table.setColumnCount(6)
        self.import_table.setHorizontalHeaderLabels(["Restaurar Plugin", "Restaurar Configs", "Nome do Plugin", "Pasta/Chave", "Versão no Backup", "Status Local"])
        self.import_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.import_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.import_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.import_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        imp_plugins_layout.addWidget(self.import_table)
        
        self.import_subtabs.addTab(self.subtab_imp_plugins, "Plugins e Configurações")
        
        # Sub-aba 2.2: Provedores no Backup
        self.subtab_imp_providers = QWidget()
        imp_prov_layout = QVBoxLayout(self.subtab_imp_providers)
        
        imp_prov_ctrl = QHBoxLayout()
        btn_imp_sel_prov = QPushButton("Selecionar Todos", self.subtab_imp_providers)
        btn_imp_sel_prov.clicked.connect(lambda: self.select_all_import_providers(True))
        btn_imp_clr_prov = QPushButton("Limpar Seleção", self.subtab_imp_providers)
        btn_imp_clr_prov.clicked.connect(lambda: self.select_all_import_providers(False))
        imp_prov_ctrl.addWidget(btn_imp_sel_prov)
        imp_prov_ctrl.addWidget(btn_imp_clr_prov)
        imp_prov_ctrl.addStretch()
        imp_prov_layout.addLayout(imp_prov_ctrl)
        
        self.import_providers_table = QTableWidget(self.subtab_imp_providers)
        self.import_providers_table.setColumnCount(4)
        self.import_providers_table.setHorizontalHeaderLabels(["Restaurar Provedor", "Tipo de Provedor", "Nome da Conexão", "URL / Endereço"])
        self.import_providers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.import_providers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.import_providers_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        imp_prov_layout.addWidget(self.import_providers_table)
        
        self.import_subtabs.addTab(self.subtab_imp_providers, "Provedores de Conteúdo")
        
        import_main_layout.addWidget(self.import_subtabs)
        
        # Botão Inferior de Restauração
        import_btn_layout = QHBoxLayout()
        btn_restore = QPushButton("Restaurar e Instalar Selecionados", self.tab_import)
        btn_restore.setStyleSheet("background-color: #D84315; color: white; font-weight: bold; padding: 8px 16px; font-size: 12px;")
        btn_restore.clicked.connect(self.restore_backup)
        import_btn_layout.addStretch()
        import_btn_layout.addWidget(btn_restore)
        import_main_layout.addLayout(import_btn_layout)
        
        self.tabs.addTab(self.tab_import, "Importar Backup")
        
        # =============================================================
        # ABA 3: VERIFICADOR DE PENDÊNCIAS
        # =============================================================
        self.tab_dependencies = QWidget()
        deps_layout = QVBoxLayout(self.tab_dependencies)
        
        deps_top = QHBoxLayout()
        btn_check_deps = QPushButton("Verificar Pendências de Plugins e Python", self.tab_dependencies)
        btn_check_deps.clicked.connect(self.check_dependencies)
        deps_top.addWidget(btn_check_deps)
        deps_top.addStretch()
        deps_layout.addLayout(deps_top)
        
        self.deps_table = QTableWidget(self.tab_dependencies)
        self.deps_table.setColumnCount(4)
        self.deps_table.setHorizontalHeaderLabels(["Plugin de Origem", "Tipo de Dependência", "Nome/Identificador", "Status Local"])
        self.deps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.deps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.deps_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.deps_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        deps_layout.addWidget(self.deps_table)
        
        deps_bottom = QHBoxLayout()
        btn_fix_deps = QPushButton("Corrigir Pendências Automaticamente", self.tab_dependencies)
        btn_fix_deps.setStyleSheet("background-color: #0288D1; color: white; font-weight: bold; padding: 6px;")
        btn_fix_deps.clicked.connect(self.fix_dependencies)
        deps_bottom.addStretch()
        deps_bottom.addWidget(btn_fix_deps)
        deps_layout.addLayout(deps_bottom)
        
        self.tabs.addTab(self.tab_dependencies, "Verificar Pendências")
        
        # =============================================================
        # ÁREA DE LOG E BARRA DE PROGRESSO COMUNS (RODAPÉ)
        # =============================================================
        footer_layout = QVBoxLayout()
        
        log_label = QLabel("Log do Sincronizador:", self)
        log_label.setStyleSheet("font-weight: bold; color: #424242;")
        footer_layout.addWidget(log_label)
        
        self.log_view = QTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(120)
        self.log_view.setStyleSheet("background-color: #212121; color: #00E676; font-family: Consolas, monospace; font-size: 11px;")
        footer_layout.addWidget(self.log_view)
        
        progress_close_layout = QHBoxLayout()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        
        btn_close = QPushButton("Fechar", self)
        btn_close.clicked.connect(self.close)
        
        progress_close_layout.addWidget(self.progress_bar)
        progress_close_layout.addWidget(btn_close)
        footer_layout.addLayout(progress_close_layout)
        
        main_layout.addLayout(footer_layout)
        
        self.log("Sincronizador inicializado com sucesso.")

    def log(self, message):
        self.log_view.append(message)
        self.log_view.moveCursor(self.log_view.textCursor().End)

    def scan_installed_plugins(self):
        """
        Escaneia as pastas de plugins instalados e lê seus metadados.
        """
        import qgis.utils
        paths = getattr(qgis.utils, 'plugin_paths', [])
        return core_plugins.scan_installed_plugins(paths, QSettings(), warn=self.log)

    def scan_server_connections(self):
        """
        Escaneia o QSettings e agrupa conexões individuais por tipo de provedor e nome.
        """
        return core_connections.scan_server_connections(QSettings())

    def refresh_export_table(self):
        """
        Atualiza as tabelas de exportação (plugins e provedores).
        """
        # 1. Tabela de Plugins
        self.export_table.setRowCount(0)
        self.plugins_cache = self.scan_installed_plugins()
        
        for row, plugin in enumerate(self.plugins_cache):
            self.export_table.insertRow(row)
            
            # Coluna 0: Checkbox Plugin
            chk_p = QTableWidgetItem()
            chk_p.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_p.setCheckState(Qt.Checked if plugin['active'] else Qt.Unchecked)
            self.export_table.setItem(row, 0, chk_p)
            
            # Coluna 1: Checkbox Configs
            chk_c = QTableWidgetItem()
            chk_c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_c.setCheckState(Qt.Checked if plugin['active'] else Qt.Unchecked)
            self.export_table.setItem(row, 1, chk_c)
            
            # Coluna 2: Nome do Plugin
            self.export_table.setItem(row, 2, QTableWidgetItem(plugin['name']))
            
            # Coluna 3: Pasta/Chave
            self.export_table.setItem(row, 3, QTableWidgetItem(plugin['key']))
            
            # Coluna 4: Versão
            self.export_table.setItem(row, 4, QTableWidgetItem(plugin['version']))
            
            # Coluna 5: Estado
            status_str = "Ativo" if plugin['active'] else "Inativo"
            status_item = QTableWidgetItem(status_str)
            if plugin['active']:
                status_item.setForeground(QColor("#2E7D32"))
            else:
                status_item.setForeground(QColor("#757575"))
            self.export_table.setItem(row, 5, status_item)

        # 2. Tabela de Provedores de Conteúdo
        self.export_providers_table.setRowCount(0)
        self.providers_cache = self.scan_server_connections()
        
        for row, prov in enumerate(self.providers_cache):
            self.export_providers_table.insertRow(row)
            
            # Coluna 0: Checkbox Provedor
            chk_prov = QTableWidgetItem()
            chk_prov.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_prov.setCheckState(Qt.Checked)
            self.export_providers_table.setItem(row, 0, chk_prov)
            
            # Coluna 1: Tipo
            self.export_providers_table.setItem(row, 1, QTableWidgetItem(prov['provider_type']))
            
            # Coluna 2: Nome
            self.export_providers_table.setItem(row, 2, QTableWidgetItem(prov['name']))
            
            # Coluna 3: URL / Detalhes
            self.export_providers_table.setItem(row, 3, QTableWidgetItem(prov['details']))

    def filter_export_plugins_table(self):
        search_text = self.export_search.text().lower()
        for row in range(self.export_table.rowCount()):
            name = self.export_table.item(row, 2).text().lower()
            key = self.export_table.item(row, 3).text().lower()
            self.export_table.setRowHidden(row, not (search_text in name or search_text in key))

    def filter_export_providers_table(self):
        search_text = self.export_provider_search.text().lower()
        for row in range(self.export_providers_table.rowCount()):
            p_type = self.export_providers_table.item(row, 1).text().lower()
            p_name = self.export_providers_table.item(row, 2).text().lower()
            p_details = self.export_providers_table.item(row, 3).text().lower()
            match = search_text in p_type or search_text in p_name or search_text in p_details
            self.export_providers_table.setRowHidden(row, not match)

    def select_all_export_plugins(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.export_table.rowCount()):
            if not self.export_table.isRowHidden(row):
                self.export_table.item(row, 0).setCheckState(state)

    def select_all_export_configs(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.export_table.rowCount()):
            if not self.export_table.isRowHidden(row):
                self.export_table.item(row, 1).setCheckState(state)

    def select_all_export_providers(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.export_providers_table.rowCount()):
            if not self.export_providers_table.isRowHidden(row):
                self.export_providers_table.item(row, 0).setCheckState(state)

    def select_all_import_plugins(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.import_table.rowCount()):
            if not self.import_table.isRowHidden(row):
                self.import_table.item(row, 0).setCheckState(state)

    def select_all_import_configs(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.import_table.rowCount()):
            if not self.import_table.isRowHidden(row):
                self.import_table.item(row, 1).setCheckState(state)

    def select_all_import_providers(self, check):
        state = Qt.Checked if check else Qt.Unchecked
        for row in range(self.import_providers_table.rowCount()):
            if not self.import_providers_table.isRowHidden(row):
                self.import_providers_table.item(row, 0).setCheckState(state)

    def export_backup(self):
        """
        Exporta a lista de plugins, configurações e provedores selecionados granularmente para um arquivo JSON.
        """
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Salvar Backup de Plugins e Provedores", "", "Arquivos JSON (*.json)"
        )
        if not filepath:
            return
            
        self.log(f"Exportando backup para: {filepath}...")
        
        # 1. Seleção da tabela de plugins e configurações
        plugin_keys = []
        config_plugin_keys = []
        for row in range(self.export_table.rowCount()):
            plugin_key = self.export_table.item(row, 3).text()
            if self.export_table.item(row, 0).checkState() == Qt.Checked:
                plugin_keys.append(plugin_key)
            if self.export_table.item(row, 1).checkState() == Qt.Checked:
                config_plugin_keys.append(plugin_key)

        # 2. Seleção da tabela de provedores de conteúdo / conexões
        provider_ids = []
        for row in range(self.export_providers_table.rowCount()):
            if self.export_providers_table.item(row, 0).checkState() == Qt.Checked:
                provider_ids.append((self.export_providers_table.item(row, 1).text(),
                                     self.export_providers_table.item(row, 2).text()))

        backup_json = core_backup.build_backup(
            QSettings(), self.plugins_cache, plugin_keys, config_plugin_keys,
            self.providers_cache, provider_ids,
            QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')
        )
        n_plugins = len(backup_json['plugins'])
        n_settings = len(backup_json['settings'])
        n_providers = len(backup_json['providers'])
        
        try:
            core_backup.write_backup(filepath, backup_json)
            
            msg = (f"Backup concluído com sucesso!\n\n"
                   f"• Plugins exportados: {n_plugins}\n"
                   f"• Configurações de plugins: {n_settings}\n"
                   f"• Provedores de conteúdo (servidores/DBs): {n_providers}")
            
            self.log(f"Backup concluído! {n_plugins} plugins, {n_settings} configs e {n_providers} provedores exportados.")
            QMessageBox.information(self, "Backup Concluído", msg)
        except Exception as e:
            self.log(f"Erro ao salvar arquivo de backup: {str(e)}")
            QMessageBox.critical(self, "Erro", f"Falha ao exportar backup:\n{str(e)}")

    def load_backup_file(self):
        """
        Carrega o arquivo JSON e monta as tabelas de importação.
        """
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Carregar Backup de Plugins e Provedores", "", "Arquivos JSON (*.json)"
        )
        if not filepath:
            return
            
        try:
            self.backup_data = core_backup.read_backup(filepath)
                
            n_plugins = len(self.backup_data.get('plugins', []))
            n_provs = len(self.backup_data.get('providers', []))
            
            self.lbl_backup_info.setText(
                f"Carregado: {os.path.basename(filepath)} ({n_plugins} plugins, {n_provs} provedores)"
            )
            self.log(f"Backup carregado de: {filepath}")
            self.refresh_import_table()
        except Exception as e:
            self.log(f"Erro ao ler backup: {str(e)}")
            QMessageBox.critical(self, "Erro", f"Falha ao carregar o backup:\n{str(e)}")

    def refresh_import_table(self):
        """
        Monta as tabelas de importação para plugins e provedores.
        """
        if not self.backup_data:
            return
            
        # 1. Tabela de Plugins no Backup
        self.import_table.setRowCount(0)
        local_plugins = self.scan_installed_plugins()
        local_keys = {p['key']: p for p in local_plugins}
        
        plugins_in_backup = self.backup_data.get('plugins', [])
        for row, plugin in enumerate(plugins_in_backup):
            self.import_table.insertRow(row)
            
            chk_p = QTableWidgetItem()
            chk_p.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_p.setCheckState(Qt.Checked)
            self.import_table.setItem(row, 0, chk_p)
            
            chk_c = QTableWidgetItem()
            chk_c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_c.setCheckState(Qt.Checked)
            self.import_table.setItem(row, 1, chk_c)
            
            self.import_table.setItem(row, 2, QTableWidgetItem(plugin['name']))
            self.import_table.setItem(row, 3, QTableWidgetItem(plugin['key']))
            self.import_table.setItem(row, 4, QTableWidgetItem(plugin['version']))
            
            key = plugin['key']
            if key in local_keys:
                if local_keys[key]['active']:
                    status_str = "Instalado e Ativo"
                    color = "#2E7D32"
                else:
                    status_str = "Instalado e Inativo"
                    color = "#E65100"
            else:
                status_str = "Não Instalado"
                color = "#D84315"
                
            status_item = QTableWidgetItem(status_str)
            status_item.setForeground(QColor(color))
            self.import_table.setItem(row, 5, status_item)

        # 2. Tabela de Provedores de Conteúdo no Backup
        self.import_providers_table.setRowCount(0)
        for row, prov in enumerate(core_backup.backup_providers(self.backup_data)):
            self.import_providers_table.insertRow(row)
            
            chk_prov = QTableWidgetItem()
            chk_prov.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_prov.setCheckState(Qt.Checked)
            self.import_providers_table.setItem(row, 0, chk_prov)
            
            self.import_providers_table.setItem(row, 1, QTableWidgetItem(prov.get('provider_type', 'Servidor')))
            self.import_providers_table.setItem(row, 2, QTableWidgetItem(prov.get('name', 'Conexão')))
            self.import_providers_table.setItem(row, 3, QTableWidgetItem(prov.get('details', '')))

    def restore_backup(self):
        """
        Restaura apenas os plugins, configurações e provedores selecionados nas tabelas.
        """
        if not self.backup_data:
            QMessageBox.warning(self, "Aviso", "Por favor, carregue um arquivo de backup antes.")
            return

        # Seleção da tabela de plugins
        plugin_keys = []
        config_plugin_keys = []
        for row in range(self.import_table.rowCount()):
            key = self.import_table.item(row, 3).text()
            if self.import_table.item(row, 0).checkState() == Qt.Checked:
                plugin_keys.append(key)
            if self.import_table.item(row, 1).checkState() == Qt.Checked:
                config_plugin_keys.append(key)

        # Seleção da tabela de provedores
        provider_ids = []
        for row in range(self.import_providers_table.rowCount()):
            if self.import_providers_table.item(row, 0).checkState() == Qt.Checked:
                provider_ids.append((self.import_providers_table.item(row, 1).text(),
                                     self.import_providers_table.item(row, 2).text()))

        installed_keys = [p['key'] for p in self.scan_installed_plugins()]
        result = core_backup.restore_backup(QSettings(), self.backup_data, plugin_keys, config_plugin_keys,
                                            provider_ids, installed_keys, log=self.log)

        n_enabled = len(result['plugins_enabled'])
        n_settings = result['settings_restored']
        n_providers = result['provider_keys_restored']

        # Iniciar thread de instalação para plugins faltantes
        if result['plugins_to_install']:
            self.log(f"Iniciando instalação assíncrona de {len(result['plugins_to_install'])} plugins...")
            self.run_dependency_worker(result['plugins_to_install'], [])
        else:
            msg = (f"Restauração concluída com sucesso!\n\n"
                   f"• Plugins ativados: {n_enabled}\n"
                   f"• Configurações de plugins restauradas: {n_settings}\n"
                   f"• Provedores de conteúdo restaurados: {n_providers}")
            
            self.log(f"Restauração concluída! {n_enabled} plugins, {n_settings} configs e {n_providers} chaves de provedores restauradas.")
            self.refresh_import_table()
            self.refresh_export_table()
            QMessageBox.information(self, "Restauração Concluída", msg)

    # ==============================================================================
    # Lógica da Aba "Verificar Pendências"
    # ==============================================================================
    
    def check_dependencies(self):
        """
        Faz a varredura e lista dependências ausentes de todos os plugins locais ativos.
        """
        self.log("Verificando dependências de plugins...")
        self.deps_table.setRowCount(0)
        
        local_plugins = self.scan_installed_plugins()
        local_keys = [p['key'] for p in local_plugins]
        
        dependencies_found = find_dependencies(local_plugins, local_keys)
                
        # Atualiza a tabela
        for row, dep in enumerate(dependencies_found):
            self.deps_table.insertRow(row)
            
            # Fonte
            self.deps_table.setItem(row, 0, QTableWidgetItem(dep['source']))
            
            # Tipo
            self.deps_table.setItem(row, 1, QTableWidgetItem(DEP_LABELS[dep['kind']]))
            
            # Nome da dependência
            self.deps_table.setItem(row, 2, QTableWidgetItem(dep['name']))
            
            # Status
            status_item = QTableWidgetItem('Ausente' if dep['is_missing'] else 'Instalado')
            if dep['is_missing']:
                status_item.setForeground(QColor("#D84315")) # Vermelho/Laranja escuro
            else:
                status_item.setForeground(QColor("#2E7D32")) # Verde escuro
            self.deps_table.setItem(row, 3, status_item)
            
        missing_count = sum(1 for d in dependencies_found if d['is_missing'])
        self.log(f"Verificação concluída! {missing_count} pendências críticas encontradas.")
        
        if missing_count == 0:
            QMessageBox.information(self, "Pendências", "Nenhuma pendência encontrada! Todos os plugins ativos possuem suas dependências.")

    def fix_dependencies(self):
        """
        Coleta todas as dependências ausentes da tabela e inicia a correção em thread.
        """
        missing_qgis = []
        missing_python = []
        
        for row in range(self.deps_table.rowCount()):
            status = self.deps_table.item(row, 3).text()
            if status == 'Ausente':
                dep_type = self.deps_table.item(row, 1).text()
                dep_name = self.deps_table.item(row, 2).text()
                
                if dep_type == DEP_LABELS[DEP_QGIS]:
                    missing_qgis.append(dep_name)
                elif dep_type == DEP_LABELS[DEP_PYTHON]:
                    missing_python.append(dep_name)
                    
        # Remove duplicados
        missing_qgis = list(set(missing_qgis))
        missing_python = list(set(missing_python))
        
        if not missing_qgis and not missing_python:
            QMessageBox.information(self, "Correção", "Nenhuma pendência para corrigir.")
            return
            
        self.log(f"Iniciando correção automática de {len(missing_qgis)} plugins QGIS e {len(missing_python)} pacotes Python...")
        self.run_dependency_worker(missing_qgis, missing_python)

    # ==============================================================================
    # Lógica do Worker Threading (Evita travamentos)
    # ==============================================================================
    
    def run_dependency_worker(self, qgis_plugins, python_packages):
        """
        Configura e inicializa a QThread de correção de dependências.
        """
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.tabs.setEnabled(False) # Bloqueia abas para evitar modificações concorrentes
        
        self.worker = DependencyFixerWorker(qgis_plugins, python_packages)
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_finished(self, success, message):
        """
        Chamado quando a thread de instalação é encerrada.
        """
        self.log(message)
        self.progress_bar.setVisible(False)
        self.tabs.setEnabled(True)
        self.worker = None
        
        if success:
            QMessageBox.information(self, "Sincronizador", message)
        else:
            QMessageBox.warning(self, "Aviso", message)
            
        # Atualiza tabelas para refletir as instalações concluídas
        self.refresh_export_table()
        if self.backup_data:
            self.refresh_import_table()
        self.check_dependencies() # Re-verifica dependências
