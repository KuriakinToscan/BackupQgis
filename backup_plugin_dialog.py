# -*- coding: utf-8 -*-

import os
import sys
import json
import base64
import subprocess
import importlib
import configparser
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QByteArray, QDateTime, QDate, QTime, QPoint, QSize, QRect, QSettings
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                             QTableWidget, QTableWidgetItem, QPushButton, QLineEdit,
                             QCheckBox, QLabel, QFileDialog, QProgressBar, QTextEdit,
                             QHeaderView, QMessageBox, QApplication)
from qgis.PyQt.QtGui import QFont, QColor
from qgis.core import QgsSettings, QgsApplication

# Mapeamento comum de pacotes PyPI para nomes de importação Python
IMPORT_MAP = {
    'beautifulsoup4': 'bs4',
    'scikit-learn': 'sklearn',
    'pillow': 'PIL',
    'pyyaml': 'yaml',
    'pyshp': 'shapefile',
    'python-dateutil': 'dateutil',
    'ruamel.yaml': 'ruamel',
    'opencv-python': 'cv2',
    'pyjwt': 'jwt',
    'sqlalchemy': 'sqlalchemy',
    'matplotlib': 'matplotlib',
    'numpy': 'numpy',
    'pandas': 'pandas',
    'scipy': 'scipy',
    'osgeo': 'osgeo',
    'psycopg2-binary': 'psycopg2'
}

# Chaves de configuração que carregam credenciais e não são exportadas por padrão
SECRET_SETTINGS_KEYS = {'password', 'pwd', 'pass', 'authcfg'}

# Regras de descoberta de conexões por provedor: (prefixo no QSettings, rótulo exibido)
PROVIDER_CONNECTION_RULES = [
    ('connections-wms', 'WMS / WMTS'),
    ('connections-wfs', 'WFS / OGC API'),
    ('connections-wcs', 'WCS'),
    ('connections-xyz', 'XYZ Tiles'),
    ('connections-vectortiles', 'Vector Tiles'),
    ('connections-stac', 'STAC Catalog'),
    ('connections-arcgisfeatureserver', 'ArcGIS FeatureServer'),
    ('connections-arcgismapserver', 'ArcGIS MapServer'),
    ('connections-arcgisservice', 'ArcGIS Server'),
    ('PostgreSQL/connections', 'PostGIS'),
    ('SpatiaLite/connections', 'SpatiaLite'),
    ('Oracle/connections', 'Oracle'),
    ('MSSQL/connections', 'MS SQL Server'),
    ('HANA/connections', 'SAP HANA'),
    ('DB2/connections', 'DB2')
]

def is_secret_settings_key(key):
    """
    Indica se uma chave do QSettings carrega credencial (senha, authcfg etc.).
    """
    parts = key.split('/')
    return bool(parts) and parts[-1].lower() in SECRET_SETTINGS_KEYS

def settings_key_belongs_to_plugin(key, plugin_key):
    """
    Fonte única de verdade para associar uma chave do QSettings a um plugin,
    usada tanto na exportação quanto na importação (case-insensitive, sem
    vazar configurações de plugins cuja chave é prefixo de outra).
    """
    key_l = key.lower()
    pk = plugin_key.lower()
    if key_l == 'pythonplugins/' + pk:
        return True
    for prefix in (pk + '/', 'plugins/' + pk + '/', 'plugin_' + pk + '/'):
        if key_l.startswith(prefix):
            return True
    return False

def read_metadata_config(metadata_file):
    """
    Lê o metadata.txt de forma tolerante: sem interpolação (o parser padrão
    quebra com '%'), aceitando opções duplicadas e BOM UTF-8.
    """
    config = configparser.ConfigParser(interpolation=None, strict=False)
    with open(metadata_file, 'r', encoding='utf-8-sig', errors='ignore') as f:
        config.read_file(f)
    return config

# ==============================================================================
# Funções de Serialização e Deserialização para QSettings
# ==============================================================================

def serialize_value(val):
    """
    Serializa valores do QSettings (incluindo tipos Qt e Python) para tipos padrão JSON.
    """
    if val is None:
        return {'type': 'basic', 'value': None}
    if isinstance(val, (int, float, bool, str)):
        return {'type': 'basic', 'value': val}
    if isinstance(val, (QByteArray, bytes, bytearray)):
        b_data = val.data() if isinstance(val, QByteArray) else val
        return {'type': 'bytearray', 'value': base64.b64encode(b_data).decode('utf-8')}
    if isinstance(val, QDateTime):
        return {'type': 'datetime', 'value': val.toString('yyyy-MM-ddTHH:mm:ss.zzz')}
    if isinstance(val, QDate):
        return {'type': 'date', 'value': val.toString('yyyy-MM-dd')}
    if isinstance(val, QTime):
        return {'type': 'time', 'value': val.toString('HH:mm:ss.zzz')}
    if isinstance(val, QPoint):
        return {'type': 'point', 'value': [val.x(), val.y()]}
    if isinstance(val, QSize):
        return {'type': 'size', 'value': [val.width(), val.height()]}
    if isinstance(val, QRect):
        return {'type': 'rect', 'value': [val.x(), val.y(), val.width(), val.height()]}
    if isinstance(val, (list, tuple)):
        return {'type': 'list', 'value': [serialize_value(item) for item in val]}
    if isinstance(val, dict):
        return {'type': 'dict', 'value': {str(k): serialize_value(v) for k, v in val.items()}}
    
    # Fallback para string se o tipo for desconhecido
    return {'type': 'fallback', 'value': str(val)}

def deserialize_value(data):
    """
    Reverte a serialização e retorna o tipo Qt correspondente.
    """
    if not isinstance(data, dict) or 'type' not in data or 'value' not in data:
        return data
    
    t = data['type']
    val = data['value']
    
    if t == 'basic':
        return val
    if t == 'bytearray':
        try:
            return QByteArray(base64.b64decode(val.encode('utf-8')))
        except Exception:
            return QByteArray()
    if t == 'datetime':
        dt = QDateTime.fromString(val, 'yyyy-MM-ddTHH:mm:ss.zzz')
        if not dt.isValid():
            dt = QDateTime.fromString(val, Qt.ISODate)
        return dt
    if t == 'date':
        return QDate.fromString(val, 'yyyy-MM-dd')
    if t == 'time':
        return QTime.fromString(val, 'HH:mm:ss.zzz')
    if t == 'point':
        return QPoint(val[0], val[1])
    if t == 'size':
        return QSize(val[0], val[1])
    if t == 'rect':
        return QRect(val[0], val[1], val[2], val[3])
    if t == 'list':
        return [deserialize_value(item) for item in val]
    if t == 'dict':
        return {k: deserialize_value(v) for k, v in val.items()}
    return val

# ==============================================================================
# Worker assíncrono para instalação de pacotes Python via pip.
# Operações do instalador de plugins do QGIS (pyplugin_installer) não rodam
# aqui: elas carregam e ativam plugins e devem executar na thread principal.
# ==============================================================================

class PipInstallWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, python_packages):
        super(PipInstallWorker, self).__init__()
        self.python_packages = list(python_packages)
        self._is_cancelled = False
        self._process = None

    def cancel(self):
        self._is_cancelled = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _python_base_cmd(self):
        """
        Localiza o interpretador Python do QGIS. No Windows, sys.executable é o
        binário do QGIS (ex.: qgis-ltr-bin.exe) e não serve para rodar pip.
        """
        if sys.platform.startswith('win'):
            for base in (getattr(sys, 'base_prefix', ''), sys.prefix):
                exe = os.path.join(base, 'python.exe')
                if os.path.isfile(exe):
                    return [exe]
            exe_dir = os.path.dirname(sys.executable)
            for bat_name in ('python-qgis-ltr.bat', 'python-qgis.bat'):
                bat = os.path.join(exe_dir, bat_name)
                if os.path.isfile(bat):
                    return ['cmd', '/c', bat]
            return [sys.executable]
        return [sys.executable]

    def _run_pip(self, base_cmd, package, extra_args):
        cmd = base_cmd + ['-m', 'pip', 'install', '--no-input',
                          '--disable-pip-version-check'] + extra_args + [package]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            shell=False,
            creationflags=creationflags
        )
        self._process = proc

        for line in proc.stdout:
            if self._is_cancelled:
                break
            if line.strip():
                self.log_signal.emit(f"[pip] {line.strip()}")

        if self._is_cancelled and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            except Exception:
                pass

        rc = proc.wait()
        return rc == 0

    def run(self):
        try:
            if not self.python_packages:
                self.finished_signal.emit(True, "")
                return

            base_cmd = self._python_base_cmd()
            if sys.platform.startswith('win') and base_cmd[0] == sys.executable:
                self.log_signal.emit("Aviso: python.exe do QGIS não localizado; usando o executável atual (em instalações Windows isso pode abrir outra instância do QGIS).")

            total = len(self.python_packages)
            successes = 0

            for i, package in enumerate(self.python_packages):
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Instalação de pacotes cancelada pelo usuário.")
                    return

                self.log_signal.emit(f"Instalando pacote Python via pip: {package}...")
                ok = self._run_pip(base_cmd, package, [])
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Instalação de pacotes cancelada pelo usuário.")
                    return
                if not ok:
                    self.log_signal.emit("Tentando novamente com instalação no perfil do usuário (--user)...")
                    ok = self._run_pip(base_cmd, package, ['--user'])

                if ok:
                    successes += 1
                    self.log_signal.emit(f"Pacote Python '{package}' processado com sucesso!")
                else:
                    self.log_signal.emit(f"Erro ao instalar pacote '{package}'.")

                self.progress_signal.emit(int((i + 1) / total * 100))

            self.finished_signal.emit(True, f"Pacotes Python concluídos: {successes}/{total} com sucesso.")
        except Exception as e:
            self.finished_signal.emit(False, f"Erro inesperado durante o pip: {str(e)}")


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
        self._busy = False
        self._install_cancelled = False
        self._install_queue = []
        self._newly_installed = []
        self._pending_restore_summary = None
        self._force_close = False
        
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

        secrets_layout = QHBoxLayout()
        self.chk_include_secrets = QCheckBox("Incluir credenciais (senhas / authcfg) no arquivo de backup", self.subtab_exp_providers)
        self.chk_include_secrets.setChecked(False)
        self.chk_include_secrets.setToolTip("Desmarcado (recomendado): chaves de senha e authcfg não são gravadas no JSON.")
        secrets_layout.addWidget(self.chk_include_secrets)
        secrets_layout.addStretch()
        exp_prov_layout.addLayout(secrets_layout)

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

        self.btn_cancel = QPushButton("Cancelar", self)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self.cancel_installation)

        btn_close = QPushButton("Fechar", self)
        btn_close.clicked.connect(self.close)

        progress_close_layout.addWidget(self.progress_bar)
        progress_close_layout.addWidget(self.btn_cancel)
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
        seen_keys = set()

        paths = getattr(qgis.utils, 'plugin_paths', [])

        for path in paths:
            if not os.path.exists(path):
                continue
            for folder in sorted(os.listdir(path)):
                folder_path = os.path.join(path, folder)
                if not os.path.isdir(folder_path):
                    continue

                if folder.startswith('__'):
                    continue

                if folder in seen_keys:
                    continue

                metadata_file = os.path.join(folder_path, 'metadata.txt')
                if os.path.exists(metadata_file):
                    try:
                        config = read_metadata_config(metadata_file)

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
                        seen_keys.add(folder)
                    except Exception as e:
                        self.log(f"Aviso ao ler metadados do plugin '{folder}': {str(e)}")

        plugins_list.sort(key=lambda p: p['name'].lower())
        return plugins_list

    def scan_server_connections(self):
        """
        Escaneia o QSettings e agrupa conexões individuais por tipo de provedor e nome.
        Usa as regras de PROVIDER_CONNECTION_RULES, que cobrem tanto as conexões
        raiz (connections-wms, connections-xyz, ...) quanto as de bancos de dados
        (PostgreSQL/connections, SpatiaLite/connections, Oracle/connections, ...).
        """
        settings = QSettings()
        connections_map = {}

        rules = [(prefix.lower(), label) for prefix, label in PROVIDER_CONNECTION_RULES]
        detail_priority = ['url', 'host', 'sqlitepath', 'database', 'dbname']

        for key in settings.allKeys():
            key_l = key.lower()
            provider_type = None
            name_idx = 0

            for prefix, label in rules:
                if key_l.startswith(prefix + '/'):
                    provider_type = label
                    name_idx = len(prefix.split('/'))
                    break

            if provider_type is None:
                parts = key.split('/')
                if len(parts) >= 2 and 'stac' in parts[0].lower():
                    provider_type = 'STAC Catalog'
                    name_idx = 2 if len(parts) >= 3 and parts[1].lower() == 'connections' else 1

            if provider_type is None:
                continue

            parts = key.split('/')
            if len(parts) <= name_idx:
                continue
            conn_name = parts[name_idx]
            if not conn_name.strip():
                continue

            map_key = (provider_type, conn_name)
            if map_key not in connections_map:
                connections_map[map_key] = {
                    'id': f"{provider_type}::{conn_name}",
                    'provider_type': provider_type,
                    'name': conn_name,
                    'details': '',
                    'keys': {},
                    '_candidates': {}
                }

            entry = connections_map[map_key]
            entry['keys'][key] = serialize_value(settings.value(key))

            key_end = parts[-1].lower()
            if key_end in detail_priority:
                entry['_candidates'][key_end] = str(settings.value(key))

        for entry in connections_map.values():
            for field in detail_priority:
                if entry['_candidates'].get(field):
                    entry['details'] = entry['_candidates'][field]
                    break
            del entry['_candidates']

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
        plugin_settings[activation_key] = serialize_value(settings.value(activation_key, False, type=bool))

        for key in settings.allKeys():
            if settings_key_belongs_to_plugin(key, plugin_key):
                plugin_settings[key] = serialize_value(settings.value(key))

        return plugin_settings

    def _read_plugin_repositories(self, include_secrets=True):
        """
        Lê os repositórios de plugins da chave usada pelo pyplugin_installer
        (app/plugin_repositories), opcionalmente sem credenciais.
        """
        try:
            repos = QgsSettings().value("app/plugin_repositories", {}, type=dict)
        except Exception:
            repos = {}
        if not isinstance(repos, dict):
            repos = {}
        if not include_secrets and repos:
            cleaned = {}
            for repo_id, cfg in repos.items():
                if isinstance(cfg, dict):
                    cfg = {k: v for k, v in cfg.items()
                           if str(k).lower() not in SECRET_SETTINGS_KEYS}
                cleaned[repo_id] = cfg
            return cleaned
        return dict(repos)

    def _is_plugin_installed(self, plugin_key):
        """
        Verifica se a pasta do plugin existe em algum dos caminhos de plugins do QGIS.
        """
        import qgis.utils
        for path in getattr(qgis.utils, 'plugin_paths', []):
            if os.path.isdir(os.path.join(path, plugin_key)):
                return True
        return False

    def create_snapshot(self):
        """
        Salva um backup completo do estado atual (plugins, configurações e
        provedores), usado como salvaguarda antes de uma restauração.
        """
        timestamp = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
        base_dir = ''
        try:
            base_dir = QgsApplication.qgisSettingsDirPath()
        except Exception:
            pass
        if not base_dir or not os.path.isdir(base_dir):
            base_dir = os.path.expanduser('~')
        filepath = os.path.join(base_dir, f"backup_qgis_snapshot_{timestamp}.json")

        plugins_list = []
        settings_to_backup = {}
        for plugin in self.scan_installed_plugins():
            plugins_list.append({
                'key': plugin['key'],
                'name': plugin['name'],
                'version': plugin['version'],
                'active': plugin['active']
            })
            settings_to_backup.update(self.collect_plugin_settings(plugin['key']))

        server_connections = {}
        providers_list = []
        for prov in self.scan_server_connections():
            server_connections.update(prov['keys'])
            providers_list.append({
                'provider_type': prov['provider_type'],
                'name': prov['name'],
                'details': prov['details'],
                'keys': list(prov['keys'].keys())
            })

        backup_json = {
            'generator': 'QGIS Plugin Sync',
            'version': '0.1',
            'date': QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'),
            'snapshot': True,
            'repositories': self._read_plugin_repositories(include_secrets=True),
            'plugins': plugins_list,
            'settings': settings_to_backup,
            'server_connections': server_connections,
            'providers': providers_list
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(backup_json, f, indent=4, ensure_ascii=False)
        return filepath

    def export_backup(self):
        """
        Exporta a lista de plugins, configurações e provedores selecionados granularmente para um arquivo JSON.
        """
        include_secrets = self.chk_include_secrets.isChecked()

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
        skipped_secret_count = 0

        for row in range(self.export_providers_table.rowCount()):
            chk_prov = self.export_providers_table.item(row, 0)
            if chk_prov.checkState() == Qt.Checked:
                p_type = self.export_providers_table.item(row, 1).text()
                p_name = self.export_providers_table.item(row, 2).text()

                for prov in self.providers_cache:
                    if prov['provider_type'] == p_type and prov['name'] == p_name:
                        exported_keys = {}
                        for k, v in prov['keys'].items():
                            if include_secrets or not is_secret_settings_key(k):
                                exported_keys[k] = v
                            else:
                                skipped_secret_count += 1
                        server_connections_to_backup.update(exported_keys)
                        exported_providers_list.append({
                            'provider_type': prov['provider_type'],
                            'name': prov['name'],
                            'details': prov['details'],
                            'keys': list(exported_keys.keys())
                        })
                        break

        if not selected_plugins and not settings_to_backup and not exported_providers_list:
            QMessageBox.warning(self, "Aviso", "Nenhum item selecionado para exportar.")
            return

        default_name = os.path.join(
            os.path.expanduser('~'),
            f"backup_qgis_{QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')}.json"
        )
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Salvar Backup de Plugins e Provedores", default_name, "Arquivos JSON (*.json)"
        )
        if not filepath:
            return
        if not filepath.lower().endswith('.json'):
            filepath += '.json'

        self.log(f"Exportando backup para: {filepath}...")

        repositories = self._read_plugin_repositories(include_secrets)

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

            if include_secrets:
                cred_msg = "• Credenciais incluídas: Sim — o arquivo contém senhas/authcfg; guarde-o em local seguro"
            else:
                cred_msg = f"• Credenciais incluídas: Não ({skipped_secret_count} chaves sensíveis ignoradas)"

            msg = (f"Backup concluído com sucesso!\n\n"
                   f"• Plugins exportados: {len(selected_plugins)}\n"
                   f"• Configurações de plugins: {len(settings_to_backup)}\n"
                   f"• Provedores de conteúdo (servidores/DBs): {len(exported_providers_list)}\n"
                   f"{cred_msg}")

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
        if self._busy:
            QMessageBox.information(self, "Sincronizador", "Aguarde a operação em andamento.")
            return

        # --- 1. Coleta da seleção (nenhuma gravação ainda) ---
        local_plugins = self.scan_installed_plugins()
        local_keys = [p['key'] for p in local_plugins]

        plugins_to_install = []
        plugins_to_activate = []
        configs_to_restore_keys = []

        for row in range(self.import_table.rowCount()):
            chk_p = self.import_table.item(row, 0)
            chk_c = self.import_table.item(row, 1)
            key = self.import_table.item(row, 3).text()

            if chk_p.checkState() == Qt.Checked:
                if key in local_keys:
                    plugins_to_activate.append(key)
                else:
                    plugins_to_install.append(key)

            if chk_c.checkState() == Qt.Checked:
                configs_to_restore_keys.append(key)

        backup_settings = self.backup_data.get('settings', {})
        if not isinstance(backup_settings, dict):
            backup_settings = {}

        settings_entries = []
        if backup_settings and configs_to_restore_keys:
            for key, val_serialized in backup_settings.items():
                for sel_key in configs_to_restore_keys:
                    if settings_key_belongs_to_plugin(key, sel_key):
                        settings_entries.append((key, val_serialized))
                        break

        server_conns = self.backup_data.get('server_connections', {})
        if not isinstance(server_conns, dict):
            server_conns = {}
        providers_in_backup = self.backup_data.get('providers', [])
        if not isinstance(providers_in_backup, list):
            providers_in_backup = []

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

        if not selected_provider_keys and server_conns and self.import_providers_table.rowCount() == 0:
            # Backups antigos, sem a lista estruturada de provedores
            selected_provider_keys = set(server_conns.keys())

        backup_repos = self.backup_data.get('repositories', {})
        if not isinstance(backup_repos, dict):
            backup_repos = {}

        # --- 2. Confirmação com resumo e snapshot opcional ---
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Question)
        msg_box.setWindowTitle("Confirmar restauração")
        msg_box.setText(
            f"Aplicar a restauração selecionada?\n\n"
            f"• Plugins a ativar: {len(plugins_to_activate) + len(plugins_to_install)} "
            f"(sendo {len(plugins_to_install)} a instalar)\n"
            f"• Configurações de plugins: {len(settings_entries)} chaves\n"
            f"• Provedores de conteúdo: {len(selected_provider_keys)} chaves\n"
            f"• Repositórios de plugins: {len(backup_repos)}"
        )
        msg_box.setInformativeText(
            "Configurações e conexões locais com o mesmo nome serão sobrescritas.\n"
            "Plugins ativados passam a valer após reiniciar o QGIS."
        )
        snapshot_chk = QCheckBox("Salvar snapshot do estado atual antes de restaurar", msg_box)
        snapshot_chk.setChecked(True)
        msg_box.setCheckBox(snapshot_chk)
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.Yes)
        if msg_box.exec_() != QMessageBox.Yes:
            self.log("Restauração cancelada pelo usuário.")
            return

        if snapshot_chk.isChecked():
            try:
                snapshot_path = self.create_snapshot()
                self.log(f"Snapshot do estado atual salvo em: {snapshot_path}")
            except Exception as e:
                ret = QMessageBox.question(
                    self, "Snapshot",
                    f"Falha ao salvar o snapshot do estado atual:\n{str(e)}\n\n"
                    f"Continuar a restauração mesmo assim?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if ret != QMessageBox.Yes:
                    self.log("Restauração abortada: falha ao gerar o snapshot de segurança.")
                    return

        # --- 3. Aplicação ---
        q_settings = QSettings()

        if backup_repos:
            self.log("Restaurando repositórios customizados (podem exigir reinício do QGIS)...")
            try:
                current_repos = QgsSettings().value("app/plugin_repositories", {}, type=dict)
                if not isinstance(current_repos, dict):
                    current_repos = {}
                merged = dict(current_repos)
                merged.update(backup_repos)
                QgsSettings().setValue("app/plugin_repositories", merged)
            except Exception as e:
                self.log(f"Aviso ao restaurar repositórios: {str(e)}")

        restored_settings_count = 0
        if settings_entries:
            self.log(f"Restaurando {len(settings_entries)} configurações de plugins...")
            for key, val_serialized in settings_entries:
                q_settings.setValue(key, deserialize_value(val_serialized))
                restored_settings_count += 1

        restored_prov_count = 0
        if selected_provider_keys:
            self.log(f"Restaurando {len(selected_provider_keys)} chaves de provedores de conteúdo...")
            for key in selected_provider_keys:
                if key in server_conns:
                    q_settings.setValue(key, deserialize_value(server_conns[key]))
                    restored_prov_count += 1

        for key in plugins_to_activate:
            q_settings.setValue(f"PythonPlugins/{key}", True)

        q_settings.sync()

        # --- 4. Instalação dos plugins ausentes (ativação só após sucesso) ---
        if plugins_to_install:
            self._pending_restore_summary = {
                'ativados': len(plugins_to_activate),
                'a_instalar': len(plugins_to_install),
                'configs': restored_settings_count,
                'provedores': restored_prov_count
            }
            self.log(f"Restauração aplicada. Iniciando instalação de {len(plugins_to_install)} plugins ausentes...")
            self.start_installation(plugins_to_install, [])
        else:
            msg = (f"Restauração concluída com sucesso!\n\n"
                   f"• Plugins ativados: {len(plugins_to_activate)}\n"
                   f"• Configurações de plugins restauradas: {restored_settings_count}\n"
                   f"• Provedores de conteúdo restaurados: {restored_prov_count}\n\n"
                   f"Reinicie o QGIS para carregar os plugins ativados.")

            self.log(f"Restauração concluída! {len(plugins_to_activate)} plugins, {restored_settings_count} configs e {restored_prov_count} chaves de provedores restauradas.")
            self.refresh_import_table()
            self.refresh_export_table()
            QMessageBox.information(self, "Restauração Concluída", msg)

    # ==============================================================================
    # Lógica da Aba "Verificar Pendências"
    # ==============================================================================
    
    def extract_plugin_dependencies(self, plugin_dir):
        """
        Lê dependências QGIS e Python declaradas em metadata.txt e requirements.txt.
        """
        deps = {'qgis': [], 'python': []}
        
        # 1. Analisa metadata.txt
        metadata_file = os.path.join(plugin_dir, 'metadata.txt')
        if os.path.exists(metadata_file):
            try:
                config = read_metadata_config(metadata_file)

                # Dependências QGIS
                qgis_deps_str = config.get('general', 'plugin_dependencies', fallback='')
                if qgis_deps_str:
                    # Limpa e divide por vírgula
                    for p in qgis_deps_str.split(','):
                        p = p.strip()
                        # Trata possíveis versionamentos ex: MyPlugin==1.2
                        p_key = p.split('==')[0].split('>=')[0].split('<=')[0].strip()
                        if p_key:
                            deps['qgis'].append(p_key)
                            
                # Dependências Python nos metadados (customizadas)
                for field in ['python_dependencies', 'pythondependencies', 'pip_dependencies', 'python_packages']:
                    py_deps_str = config.get('general', field, fallback='')
                    if py_deps_str:
                        for pkg in py_deps_str.split(','):
                            pkg = pkg.strip()
                            if pkg:
                                # Remove versioning specifiers e.g. requests>=2.20 -> requests
                                clean_pkg = pkg.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].split(' ')[0].strip()
                                if clean_pkg:
                                    deps['python'].append(clean_pkg)
            except Exception as e:
                pass

        # 2. Analisa requirements.txt
        reqs_file = os.path.join(plugin_dir, 'requirements.txt')
        if os.path.exists(reqs_file):
            try:
                with open(reqs_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        # Ignora versionamento
                        pkg = line.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].split(' ')[0].strip()
                        if pkg:
                            deps['python'].append(pkg)
            except Exception as e:
                pass
                
        # Remove duplicados
        deps['qgis'] = list(set(deps['qgis']))
        deps['python'] = list(set(deps['python']))
        return deps

    def check_dependencies(self, interactive=True):
        """
        Faz a varredura e lista dependências ausentes de todos os plugins locais ativos.
        """
        self.log("Verificando dependências de plugins...")
        self.deps_table.setRowCount(0)
        
        local_plugins = self.scan_installed_plugins()
        local_keys = [p['key'] for p in local_plugins]
        
        dependencies_found = []
        
        for plugin in local_plugins:
            # Analisa apenas dependências de plugins que estão ativos
            if not plugin['active']:
                continue
                
            p_deps = self.extract_plugin_dependencies(plugin['path'])
            
            # Verifica dependências de plugins QGIS
            for qgis_dep in p_deps['qgis']:
                is_installed = qgis_dep in local_keys
                dependencies_found.append({
                    'source': plugin['name'],
                    'type': 'Plugin QGIS',
                    'name': qgis_dep,
                    'status': 'Instalado' if is_installed else 'Ausente',
                    'is_missing': not is_installed
                })
                
            # Verifica dependências de pacotes Python
            for py_dep in p_deps['python']:
                # Mapeia para o nome de importação correspondente
                import_name = IMPORT_MAP.get(py_dep.lower(), py_dep)
                
                is_installed = False
                try:
                    importlib.import_module(import_name)
                    is_installed = True
                except Exception:
                    pass
                
                dependencies_found.append({
                    'source': plugin['name'],
                    'type': 'Pacote Python',
                    'name': py_dep,
                    'status': 'Instalado' if is_installed else 'Ausente',
                    'is_missing': not is_installed
                })
                
        # Atualiza a tabela
        for row, dep in enumerate(dependencies_found):
            self.deps_table.insertRow(row)
            
            # Fonte
            self.deps_table.setItem(row, 0, QTableWidgetItem(dep['source']))
            
            # Tipo
            self.deps_table.setItem(row, 1, QTableWidgetItem(dep['type']))
            
            # Nome da dependência
            self.deps_table.setItem(row, 2, QTableWidgetItem(dep['name']))
            
            # Status
            status_item = QTableWidgetItem(dep['status'])
            if dep['is_missing']:
                status_item.setForeground(QColor("#D84315")) # Vermelho/Laranja escuro
            else:
                status_item.setForeground(QColor("#2E7D32")) # Verde escuro
            self.deps_table.setItem(row, 3, status_item)
            
        missing_count = sum(1 for d in dependencies_found if d['is_missing'])
        self.log(f"Verificação concluída! {missing_count} pendências críticas encontradas.")
        
        if missing_count == 0 and interactive:
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
                
                if dep_type == 'Plugin QGIS':
                    missing_qgis.append(dep_name)
                elif dep_type == 'Pacote Python':
                    missing_python.append(dep_name)
                    
        # Remove duplicados
        missing_qgis = list(set(missing_qgis))
        missing_python = list(set(missing_python))
        
        if not missing_qgis and not missing_python:
            QMessageBox.information(self, "Correção", "Nenhuma pendência para corrigir.")
            return
            
        self.log(f"Iniciando correção automática de {len(missing_qgis)} plugins QGIS e {len(missing_python)} pacotes Python...")
        self.start_installation(missing_qgis, missing_python)

    # ==============================================================================
    # Coordenação de instalação: pip em QThread; plugins do QGIS na thread principal
    # ==============================================================================

    def start_installation(self, qgis_plugins, python_packages):
        """
        Inicia o processo de instalação: pacotes Python via pip numa QThread e,
        em seguida, plugins do QGIS na thread principal (o pyplugin_installer
        carrega e ativa plugins e não é seguro chamá-lo fora dela).
        """
        if self._busy:
            QMessageBox.information(self, "Sincronizador", "Já existe uma operação em andamento.")
            return
        if not qgis_plugins and not python_packages:
            return

        self._busy = True
        self._install_cancelled = False
        self._install_queue = list(qgis_plugins)
        self._newly_installed = []

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_cancel.setVisible(True)
        self.tabs.setEnabled(False)

        if python_packages:
            self.log(f"=== Instalando {len(python_packages)} pacotes Python (pip) ===")
            self.worker = PipInstallWorker(python_packages)
            self.worker.log_signal.connect(self.log)
            self.worker.progress_signal.connect(lambda value: self.progress_bar.setValue(int(value / 2)))
            self.worker.finished_signal.connect(self._on_pip_phase_finished)
            self.worker.start()
        else:
            self._on_pip_phase_finished(True, "")

    def _on_pip_phase_finished(self, success, message):
        if message:
            self.log(message)
        if not success:
            self._finish_installation(cancelled=True)
            return
        self._install_qgis_plugins_phase()

    def _install_qgis_plugins_phase(self):
        """
        Instala os plugins do QGIS pendentes na thread principal.
        """
        queue = self._install_queue
        total = len(queue)
        if total == 0:
            self._finish_installation(cancelled=False)
            return

        self.log(f"=== Instalando {total} plugins do QGIS ===")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            installer = None
            try:
                import pyplugin_installer
                installer = pyplugin_installer.instance()
                self.log("Atualizando lista de repositórios de plugins do QGIS...")
                installer.fetchAvailablePlugins(False)
            except Exception as e:
                self.log(f"Aviso: instalador de plugins do QGIS indisponível ({str(e)}).")

            for i, plugin_key in enumerate(queue):
                if self._install_cancelled:
                    self.log("Instalação de plugins cancelada pelo usuário.")
                    break

                self.log(f"Instalando plugin do QGIS: {plugin_key}...")
                ok = False
                if installer:
                    try:
                        # installPlugin() é void no QGIS: o sucesso é verificado
                        # pela presença da pasta do plugin após a instalação.
                        installer.installPlugin(plugin_key)
                        ok = self._is_plugin_installed(plugin_key)
                    except Exception as e:
                        self.log(f"Erro durante instalação do plugin '{plugin_key}': {str(e)}")
                else:
                    self.log(f"Impossível instalar '{plugin_key}': módulo pyplugin_installer indisponível.")

                if ok:
                    self._newly_installed.append(plugin_key)
                    self.log(f"Plugin '{plugin_key}' instalado com sucesso!")
                else:
                    self.log(f"Falha ao instalar o plugin '{plugin_key}' (não encontrado ou erro de download).")

                self.progress_bar.setValue(50 + int((i + 1) / total * 50))
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()

        self._finish_installation(cancelled=self._install_cancelled)

    def _finish_installation(self, cancelled):
        """
        Sempre executado ao término da instalação (mesmo com erro ou cancelamento),
        garantindo que a interface seja reativada.
        """
        self._busy = False
        self.progress_bar.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.tabs.setEnabled(True)
        self.worker = None

        newly_installed = self._newly_installed
        self._newly_installed = []

        restore_summary = self._pending_restore_summary
        self._pending_restore_summary = None

        if restore_summary is not None:
            # Ativa apenas os plugins cuja instalação foi confirmada
            q_settings = QSettings()
            for key in newly_installed:
                q_settings.setValue(f"PythonPlugins/{key}", True)

            if cancelled:
                QMessageBox.warning(
                    self, "Restauração",
                    "Operação interrompida antes da instalação dos plugins pendentes.\n"
                    "As configurações e provedores já restaurados foram mantidos.")
            else:
                msg = (f"Restauração concluída!\n\n"
                       f"• Plugins ativados: {restore_summary['ativados'] + len(newly_installed)}\n"
                       f"• Plugins instalados agora: {len(newly_installed)} de {restore_summary['a_instalar']}\n"
                       f"• Configurações de plugins restauradas: {restore_summary['configs']}\n"
                       f"• Provedores de conteúdo restaurados: {restore_summary['provedores']}\n\n"
                       f"Reinicie o QGIS para carregar os plugins ativados.")
                self.log("Restauração concluída.")
                QMessageBox.information(self, "Restauração Concluída", msg)
        elif cancelled:
            QMessageBox.warning(self, "Aviso", "Operação cancelada pelo usuário.")
        else:
            QMessageBox.information(
                self, "Sincronizador",
                "Processo de instalação concluído!\nReinicie o QGIS se plugins foram instalados ou ativados.")

        self.refresh_export_table()
        if self.backup_data:
            self.refresh_import_table()
        self.check_dependencies(interactive=False)

    def cancel_installation(self):
        self._install_cancelled = True
        worker = self.worker
        if worker is not None:
            worker.cancel()
        self.log("Cancelamento solicitado. Finalizando a etapa atual...")

    def closeEvent(self, event):
        if self._busy and not self._force_close:
            QMessageBox.information(
                self, "Sincronizador",
                "Há uma operação em andamento. Cancele-a ou aguarde a conclusão antes de fechar.")
            event.ignore()
            return
        event.accept()

    def shutdown(self):
        """
        Encerra o diálogo com segurança: cancela operações em curso e aguarda o worker.
        """
        if self._busy:
            self.cancel_installation()
        worker = self.worker
        if worker is not None:
            worker.wait(10000)
        self._force_close = True
        self.close()
