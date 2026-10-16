# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
import configparser
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QDateTime, QSettings
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                             QTableWidget, QTableWidgetItem, QPushButton, QLineEdit,
                             QLabel, QFileDialog, QProgressBar, QTextEdit,
                             QHeaderView, QMessageBox)
from qgis.PyQt.QtGui import QFont, QColor

from .core.serialization import serialize_value, deserialize_value
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
        plugins_list = []
        
        paths = getattr(qgis.utils, 'plugin_paths', [])
        
        for path in paths:
            if not os.path.exists(path):
                continue
            for folder in os.listdir(path):
                folder_path = os.path.join(path, folder)
                if not os.path.isdir(folder_path):
                    continue
                
                if folder.startswith('__'):
                    continue
                
                metadata_file = os.path.join(folder_path, 'metadata.txt')
                if os.path.exists(metadata_file):
                    try:
                        config = configparser.ConfigParser()
                        with open(metadata_file, 'r', encoding='utf-8', errors='ignore') as f:
                            config.read_file(f)
                        
                        name = config.get('general', 'name', fallback=folder)
                        version = config.get('general', 'version', fallback='Desconhecida')
                        description = config.get('general', 'description', fallback='')
                        
                        settings = QSettings()
                        is_active = settings.value(f"PythonPlugins/{folder}", False, type=bool)
                        
                        plugins_list.append({
                            'key': folder,
                            'name': name,
                            'version': version,
                            'description': description,
                            'active': is_active,
                            'path': folder_path
                        })
                    except Exception as e:
                        self.log(f"Aviso ao ler metadados do plugin '{folder}': {str(e)}")
                        
        return plugins_list

    def scan_server_connections(self):
        """
        Escaneia o QSettings e agrupa conexões individuais por tipo de provedor e nome.
        """
        settings = QSettings()
        connections_map = {}
        
        provider_labels = {
            'wms': 'WMS / WMTS',
            'wfs': 'WFS / OGC API',
            'wcs': 'WCS',
            'xyz': 'XYZ Tiles',
            'vectortiles': 'Vector Tiles',
            'stac': 'STAC Catalog',
            'arcgis-mapserver': 'ArcGIS MapServer',
            'arcgis-featureserver': 'ArcGIS FeatureServer',
            'postgis': 'PostGIS',
            'spatialite': 'SpatiaLite',
            'oracle': 'Oracle',
            'hana': 'SAP HANA',
            'mssql': 'MS SQL Server'
        }

        for key in settings.allKeys():
            parts = key.split('/')
            provider_type = None
            conn_name = None
            
            for p_idx, part in enumerate(parts):
                if part.lower().startswith('connections-'):
                    raw_type = part.lower().replace('connections-', '')
                    provider_type = provider_labels.get(raw_type, raw_type.upper())
                    if p_idx + 1 < len(parts):
                        conn_name = parts[p_idx + 1]
                    break
                elif part.lower() in ['wms', 'wfs', 'wcs', 'xyz', 'stac', 'vectortiles'] and p_idx > 0 and parts[p_idx-1].lower() in ['qgis', 'plugins']:
                    provider_type = provider_labels.get(part.lower(), part.upper())
                    if p_idx + 1 < len(parts):
                        conn_name = parts[p_idx + 1]
                    break
            
            if not provider_type and len(parts) >= 2:
                first_lower = parts[0].lower()
                if 'stac' in first_lower:
                    provider_type = 'STAC Catalog'
                    conn_name = parts[1] if len(parts) > 1 else parts[0]
            
            if provider_type and conn_name and conn_name.strip():
                map_key = (provider_type, conn_name)
                if map_key not in connections_map:
                    connections_map[map_key] = {
                        'id': f"{provider_type}::{conn_name}",
                        'provider_type': provider_type,
                        'name': conn_name,
                        'details': '',
                        'keys': {}
                    }
                
                serialized_val = serialize_value(settings.value(key))
                connections_map[map_key]['keys'][key] = serialized_val
                
                key_end = parts[-1].lower()
                if key_end in ['url', 'host', 'sqlitepath', 'database', 'dbname'] and not connections_map[map_key]['details']:
                    connections_map[map_key]['details'] = str(settings.value(key))

        return list(connections_map.values())

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

    def collect_plugin_settings(self, plugin_key):
        """
        Vasculha o QSettings em busca de configurações associadas à chave do plugin.
        """
        settings = QSettings()
        plugin_settings = {}
        
        activation_key = f"PythonPlugins/{plugin_key}"
        plugin_settings[activation_key] = serialize_value(settings.value(activation_key))
        
        prefixes = [
            f"{plugin_key}/",
            f"/{plugin_key}/",
            f"plugins/{plugin_key}/",
            f"/plugins/{plugin_key}/",
            f"plugin_{plugin_key}/",
            f"Plugin_{plugin_key}/"
        ]
        
        for key in settings.allKeys():
            key_lower = key.lower()
            match = False
            for prefix in prefixes:
                if key_lower.startswith(prefix.lower()):
                    match = True
                    break
            if match:
                val = settings.value(key)
                plugin_settings[key] = serialize_value(val)
                
        return plugin_settings

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
        
        selected_plugins = []
        settings_to_backup = {}
        
        # 1. Processa tabela de plugins e configurações
        for row in range(self.export_table.rowCount()):
            chk_p = self.export_table.item(row, 0)
            chk_c = self.export_table.item(row, 1)
            
            if chk_p.checkState() == Qt.Checked or chk_c.checkState() == Qt.Checked:
                plugin_key = self.export_table.item(row, 3).text()
                
                plugin_data = None
                for p in self.plugins_cache:
                    if p['key'] == plugin_key:
                        plugin_data = p
                        break
                        
                if plugin_data and chk_p.checkState() == Qt.Checked:
                    selected_plugins.append({
                        'key': plugin_data['key'],
                        'name': plugin_data['name'],
                        'version': plugin_data['version'],
                        'active': plugin_data['active']
                    })
                    
                if plugin_key and chk_c.checkState() == Qt.Checked:
                    p_settings = self.collect_plugin_settings(plugin_key)
                    settings_to_backup.update(p_settings)

        # 2. Processa tabela de provedores de conteúdo / conexões
        server_connections_to_backup = {}
        exported_providers_list = []
        
        for row in range(self.export_providers_table.rowCount()):
            chk_prov = self.export_providers_table.item(row, 0)
            if chk_prov.checkState() == Qt.Checked:
                p_type = self.export_providers_table.item(row, 1).text()
                p_name = self.export_providers_table.item(row, 2).text()
                
                for prov in self.providers_cache:
                    if prov['provider_type'] == p_type and prov['name'] == p_name:
                        server_connections_to_backup.update(prov['keys'])
                        exported_providers_list.append({
                            'provider_type': prov['provider_type'],
                            'name': prov['name'],
                            'details': prov['details'],
                            'keys': list(prov['keys'].keys())
                        })
                        break

        q_settings = QSettings()
        repositories = q_settings.value("pythonPlugins/pluginRepositories", {})
        
        backup_json = {
            'generator': 'QGIS Plugin Sync',
            'version': '0.1',
            'date': QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'),
            'repositories': repositories,
            'plugins': selected_plugins,
            'settings': settings_to_backup,
            'server_connections': server_connections_to_backup,
            'providers': exported_providers_list
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(backup_json, f, indent=4, ensure_ascii=False)
            
            msg = (f"Backup concluído com sucesso!\n\n"
                   f"• Plugins exportados: {len(selected_plugins)}\n"
                   f"• Configurações de plugins: {len(settings_to_backup)}\n"
                   f"• Provedores de conteúdo (servidores/DBs): {len(exported_providers_list)}")
            
            self.log(f"Backup concluído! {len(selected_plugins)} plugins, {len(settings_to_backup)} configs e {len(exported_providers_list)} provedores exportados.")
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
            with open(filepath, 'r', encoding='utf-8') as f:
                self.backup_data = json.load(f)
                
            if not isinstance(self.backup_data, dict) or ('plugins' not in self.backup_data and 'server_connections' not in self.backup_data):
                raise ValueError("Arquivo de backup inválido ou incompatível.")
                
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
        providers_in_backup = self.backup_data.get('providers', [])
        
        if not providers_in_backup and 'server_connections' in self.backup_data:
            s_conns = self.backup_data['server_connections']
            temp_map = {}
            for key, val_ser in s_conns.items():
                parts = key.split('/')
                p_type = parts[0] if len(parts) > 0 else 'Servidor'
                c_name = parts[1] if len(parts) > 1 else 'Conexão'
                m_key = (p_type, c_name)
                if m_key not in temp_map:
                    temp_map[m_key] = {'provider_type': p_type, 'name': c_name, 'details': '', 'keys': []}
                temp_map[m_key]['keys'].append(key)
            providers_in_backup = list(temp_map.values())

        for row, prov in enumerate(providers_in_backup):
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

        q_settings = QSettings()
        
        # 1. Adicionar repositórios customizados
        backup_repos = self.backup_data.get('repositories', {})
        if backup_repos:
            self.log("Restaurando repositórios customizados...")
            current_repos = q_settings.value("pythonPlugins/pluginRepositories", {}, type=dict)
            current_repos.update(backup_repos)
            q_settings.setValue("pythonPlugins/pluginRepositories", current_repos)

        # 2. Processar seleção da tabela de plugins
        plugins_to_install = []
        plugins_to_enable = []
        configs_to_restore_keys = []
        
        local_plugins = self.scan_installed_plugins()
        local_keys = [p['key'] for p in local_plugins]
        
        for row in range(self.import_table.rowCount()):
            chk_p = self.import_table.item(row, 0)
            chk_c = self.import_table.item(row, 1)
            key = self.import_table.item(row, 3).text()
            
            if chk_p.checkState() == Qt.Checked:
                if key not in local_keys:
                    plugins_to_install.append(key)
                plugins_to_enable.append(key)
                
            if chk_c.checkState() == Qt.Checked:
                configs_to_restore_keys.append(key)

        # 3. Restaurar configurações dos plugins selecionados
        restored_settings_count = 0
        if 'settings' in self.backup_data and configs_to_restore_keys:
            self.log("Restaurando configurações dos plugins selecionados...")
            for key, val_serialized in self.backup_data['settings'].items():
                belongs_to_selected = False
                for sel_key in configs_to_restore_keys:
                    if key.startswith(f"PythonPlugins/{sel_key}") or key.startswith(f"{sel_key}/") or key.startswith(f"/{sel_key}/") or key.startswith(f"plugin_{sel_key}/") or key.startswith(f"Plugin_{sel_key}/"):
                        belongs_to_selected = True
                        break
                
                if belongs_to_selected:
                    value = deserialize_value(val_serialized)
                    q_settings.setValue(key, value)
                    restored_settings_count += 1

        # 4. Restaurar provedores de conteúdo selecionados
        restored_prov_count = 0
        server_conns = self.backup_data.get('server_connections', {})
        providers_in_backup = self.backup_data.get('providers', [])
        
        selected_provider_keys = set()
        for row in range(self.import_providers_table.rowCount()):
            chk_prov = self.import_providers_table.item(row, 0)
            if chk_prov.checkState() == Qt.Checked:
                p_type = self.import_providers_table.item(row, 1).text()
                p_name = self.import_providers_table.item(row, 2).text()
                
                for prov in providers_in_backup:
                    if prov.get('provider_type') == p_type and prov.get('name') == p_name:
                        for k in prov.get('keys', []):
                            selected_provider_keys.add(k)
                        break
                        
        if selected_provider_keys and server_conns:
            self.log(f"Restaurando {len(selected_provider_keys)} chaves de provedores de conteúdo...")
            for key in selected_provider_keys:
                if key in server_conns:
                    value = deserialize_value(server_conns[key])
                    q_settings.setValue(key, value)
                    restored_prov_count += 1
        elif server_conns and self.import_providers_table.rowCount() == 0:
            for key, val_ser in server_conns.items():
                q_settings.setValue(key, deserialize_value(val_ser))
                restored_prov_count += 1

        # Ativa o estado de ativação para os plugins selecionados
        for sel_key in plugins_to_enable:
            q_settings.setValue(f"PythonPlugins/{sel_key}", True)

        # 5. Iniciar thread de instalação para plugins faltantes
        if plugins_to_install:
            self.log(f"Iniciando instalação assíncrona de {len(plugins_to_install)} plugins...")
            self.run_dependency_worker(plugins_to_install, [])
        else:
            msg = (f"Restauração concluída com sucesso!\n\n"
                   f"• Plugins ativados: {len(plugins_to_enable)}\n"
                   f"• Configurações de plugins restauradas: {restored_settings_count}\n"
                   f"• Provedores de conteúdo restaurados: {restored_prov_count}")
            
            self.log(f"Restauração concluída! {len(plugins_to_enable)} plugins, {restored_settings_count} configs e {restored_prov_count} chaves de provedores restauradas.")
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
