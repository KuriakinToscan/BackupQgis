# -*- coding: utf-8 -*-
"""
Janela "Configurações do QGIS": exportação e restauração de plugins,
configurações, conexões e autenticação, histórico e pendências.

É independente da janela de projetos (ui/projects_dialog.py): preferências,
log, tarefas e automação próprios. Operações longas rodam em ``QgsTask``; a
instalação de plugins roda na thread principal, um plugin por vez.
"""

import configparser
import datetime
import os
import socket
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                                 QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle, QTabWidget,
                                 QToolButton, QVBoxLayout, QWidget)

from ..core import installer
from ..core.backup_file import (BackupFormatError, build_backup_document, build_restore_plan, list_snapshots,
                                load_backup, load_snapshot, save_snapshot, snapshot_dir, write_json_atomic)
from ..core.compat import enum_value
from ..core.credentials import Credential, apply_authcfg, find_credentials
from ..core.deps import find_python_interpreter, is_distribution_installed, read_plugin_dependencies
from ..core.keys import is_sensitive_key, remap_path_prefix
from ..core.plugin_archive import PluginArchiveError, archive_keys, archive_path_for, write_plugins_archive
from ..core.plugins import resolve_plugin_name
from ..core.settings_io import canonical, deserialize_value, read_values, serialize_value, snapshot_keys
from ..tasks.pip_task import PipInstallTask
from ..tasks.scan_task import ScanTask
from .auth_io import AuthTransferError, auth_transfer_available, authcfg_path_for, export_protected, import_authcfg
from .common import CONFIG_GROUP, add_config_history, as_bool, as_int
from .config_history import ConfigHistoryPage
from .preview_dialog import RestorePreviewDialog, build_preview_rows
from .shell import ShellDialog
from .widgets import CheckTable, check_item, text_item

LOG_TAG = "BackupQgis"

# Colunas das tabelas
EXP_PLUGIN, EXP_CONFIG, EXP_NAME, EXP_KEY, EXP_VERSION, EXP_STATE = range(6)
IMP_PLUGIN, IMP_CONFIG, IMP_NAME, IMP_KEY, IMP_VERSION, IMP_LOCAL, IMP_ACTION = range(7)
CON_CHECK, CON_TYPE, CON_NAME, CON_DETAILS, CON_INFO = range(5)
DEP_CHECK, DEP_SOURCE, DEP_TYPE, DEP_NAME, DEP_STATE = range(5)

TYPE_QGIS = "Plugin QGIS"
TYPE_PYTHON = "Pacote Python"


def read_own_version(plugin_dir: str) -> str:
    """Versão declarada no metadata.txt do próprio plugin."""
    config = configparser.ConfigParser(interpolation=None)
    try:
        with open(os.path.join(plugin_dir, "metadata.txt"), "r", encoding="utf-8") as handle:
            config.read_file(handle)
        return config.get("general", "version", fallback="desconhecida")
    except (OSError, configparser.Error):
        return "desconhecida"


class BackupPluginDialog(ShellDialog):
    """Janela "Configurações do QGIS": plugins, configurações, conexões e autenticação."""

    def __init__(self, iface: Any, plugin_dir: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(iface, plugin_dir,
                         QCoreApplication.translate("BackupPluginDialog",
                                                    "BackupQgis — configurações do QGIS (plugins, conexões, "
                                                    "autenticação)"),
                         os.path.join(plugin_dir, "icon.png"), parent)
        self.version = read_own_version(plugin_dir)

        self.scan = None  # ScanResult mais recente
        self.backup_doc: Optional[Dict[str, Any]] = None
        self.backup_path = ""
        self.backup_archive: Optional[str] = None
        self.backup_archive_keys: List[str] = []
        self.backup_authcfg: Optional[str] = None
        self._scan_started = False

        self._build_ui()

    # ------------------------------------------------------------------
    # Construção da interface
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        sp = QStyle.StandardPixmap
        self._add_page(self._build_export_page(), self.tr("Exportar"), self._icon("/mActionFileSave.svg", sp.SP_DialogSaveButton))
        self.import_page = self._build_import_page()
        self._add_page(self.import_page, self.tr("Importar"), self._icon("/mActionFileOpen.svg", sp.SP_DialogOpenButton))
        self.history_page = ConfigHistoryPage(self)
        self._add_page(self.history_page, self.tr("Histórico"), self._icon("/mActionHistory.svg", sp.SP_FileDialogDetailedView))
        self._add_page(self._build_deps_page(), self.tr("Pendências"), self._icon("/mIconWarning.svg", sp.SP_MessageBoxWarning))
        self.nav.setCurrentRow(0)

    def _filter_row(self, parent: QWidget, placeholder: str) -> Tuple[QHBoxLayout, QLineEdit, QToolButton]:
        row = QHBoxLayout()
        edit = QLineEdit(parent)
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        marker = QToolButton(parent)
        marker.setText(self.tr("Marcar"))
        marker.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        marker.setMenu(QMenu(marker))
        row.addWidget(edit, 1)
        row.addWidget(marker)
        return row, edit, marker

    def _build_export_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        row, self.exp_filter, self.exp_marker = self._filter_row(page, self.tr("Filtrar por nome, pasta, tipo ou endereço..."))
        self.btn_refresh = QToolButton(page)
        self.btn_refresh.setIcon(self._icon("/mActionRefresh.svg", QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_refresh.setToolTip(self.tr("Ler novamente plugins e configurações"))
        self.btn_refresh.clicked.connect(self.start_scan)
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)

        self.exp_tabs = QTabWidget(page)
        self.exp_plugins = CheckTable([self.tr("Plugin"), self.tr("Configurações"), self.tr("Nome"), self.tr("Pasta"),
                                       self.tr("Versão"), self.tr("Situação")], EXP_NAME, EXP_NAME, page)
        self.exp_conns = CheckTable([self.tr("Exportar"), self.tr("Tipo"), self.tr("Nome"), self.tr("Endereço"),
                                     self.tr("Chaves")], CON_DETAILS, CON_TYPE, page)
        self.exp_tabs.addTab(self.exp_plugins, self.tr("Plugins e configurações"))
        self.exp_tabs.addTab(self.exp_conns, self.tr("Conexões (WMS, WFS, XYZ, PostGIS...)"))
        layout.addWidget(self.exp_tabs, 1)

        options = QHBoxLayout()
        self.chk_exp_repos = QCheckBox(self.tr("Incluir repositórios de plugins"), page)
        self.chk_exp_repos.setChecked(True)
        self.cmb_secrets = QComboBox(page)
        self.cmb_secrets.addItem(self.tr("Senhas: não incluir"), "none")
        if auth_transfer_available():
            self.cmb_secrets.addItem(self.tr("Senhas: protegidas por authcfg (recomendado)"), "authcfg")
        self.cmb_secrets.addItem(self.tr("Senhas: texto puro no JSON (avançado)"), "plain")
        self.cmb_secrets.setToolTip(self.tr(
            "Protegidas: as senhas das conexões viram configurações de autenticação do QGIS e vão, com as que você "
            "já tem, para <backup>.authcfg.xml, criptografado com uma senha escolhida na exportação. O JSON fica "
            "sem senhas. É o caminho recomendado para guardar o backup em NAS ou HD externo (QGIS 3.20+).\n"
            "Texto puro: senhas e tokens gravados legíveis no JSON."))
        self.cmb_secrets.currentIndexChanged.connect(self._warn_secrets)
        self.chk_exp_zip = QCheckBox(self.tr("Incluir ZIP dos plugins fora dos repositórios"), page)
        self.chk_exp_zip.setToolTip(self.tr("Copia para <backup>.plugins.zip os plugins selecionados que não estão "
                                            "em nenhum repositório configurado (instalados por ZIP ou de repositório "
                                            "privado). Na restauração, o repositório tem preferência."))
        self.chk_exp_zip.setChecked(True)
        options.addWidget(self.chk_exp_repos)
        options.addWidget(self.chk_exp_zip)
        options.addWidget(self.cmb_secrets)
        options.addStretch()
        layout.addLayout(options)

        auto_row = QHBoxLayout()
        auto_label = QLabel(self.tr("Exportação automática (sem senhas):"), page)
        auto_label.setToolTip(self.tr("Grava plugins ativos, configurações, conexões e repositórios, sem senhas, em "
                                      "qgis-config_<computador>_<data>.json. Independe do backup de projetos."))
        self.chk_auto_export = QCheckBox(self.tr("ao fechar o QGIS"), page)
        self.auto_export_days = QSpinBox(page)
        self.auto_export_days.setRange(0, 365)
        self.auto_export_days.setPrefix(self.tr("a cada "))
        self.auto_export_days.setSuffix(self.tr(" dia(s)"))
        self.auto_export_days.setSpecialValueText(self.tr("sem agendamento"))
        self.auto_export_days.setToolTip(self.tr("Exporta em segundo plano enquanto o QGIS estiver aberto "
                                                 "(0 = desligado)."))
        self.auto_export_dir = QLineEdit(page)
        self.auto_export_dir.setPlaceholderText(self.tr("Pasta (ex.: pasta sincronizada com o NAS)"))
        browse = QToolButton(page)
        browse.setText("...")
        browse.clicked.connect(self._browse_auto_export)
        self.auto_export_keep = QSpinBox(page)
        self.auto_export_keep.setRange(0, 999)
        self.auto_export_keep.setSpecialValueText(self.tr("todos"))
        self.auto_export_keep.setToolTip(self.tr("Arquivos mantidos na pasta (0 = todos)"))
        auto_row.addWidget(auto_label)
        auto_row.addWidget(self.chk_auto_export)
        auto_row.addWidget(self.auto_export_days)
        auto_row.addWidget(QLabel(self.tr("em:"), page))
        auto_row.addWidget(self.auto_export_dir, 1)
        auto_row.addWidget(browse)
        auto_row.addWidget(QLabel(self.tr("Manter:"), page))
        auto_row.addWidget(self.auto_export_keep)
        layout.addLayout(auto_row)
        settings = QSettings()
        self.chk_auto_export.setChecked(as_bool(settings.value(f"{CONFIG_GROUP}/exportOnQuit", False)))
        self.auto_export_days.setValue(as_int(settings.value(f"{CONFIG_GROUP}/exportEveryDays", 0), 0))
        self.auto_export_dir.setText(str(settings.value(f"{CONFIG_GROUP}/exportDir", "")))
        self.auto_export_keep.setValue(as_int(settings.value(f"{CONFIG_GROUP}/exportKeep", 10), 10))
        self.chk_auto_export.toggled.connect(self._save_auto_export)
        self.auto_export_days.valueChanged.connect(self._save_auto_export)
        self.auto_export_dir.editingFinished.connect(self._save_auto_export)
        self.auto_export_keep.valueChanged.connect(self._save_auto_export)

        bottom = QHBoxLayout()
        self.exp_counter = QLabel("", page)
        self.btn_export = QPushButton(self._icon("/mActionFileSave.svg"), self.tr("Salvar backup..."), page)
        self.btn_export.setDefault(True)
        self.btn_export.clicked.connect(self.export_backup)
        bottom.addWidget(self.exp_counter, 1)
        bottom.addWidget(self.btn_export)
        layout.addLayout(bottom)

        self.exp_filter.textChanged.connect(self.exp_plugins.set_filter)
        self.exp_filter.textChanged.connect(self.exp_conns.set_filter)
        self.exp_plugins.checks_changed.connect(self._update_export_counter)
        self.exp_conns.checks_changed.connect(self._update_export_counter)
        self.exp_marker.menu().aboutToShow.connect(
            lambda: self._fill_marker_menu(self.exp_marker.menu(), self.exp_tabs, self.exp_plugins, self.exp_conns,
                                           EXP_PLUGIN, EXP_CONFIG))
        return page

    def _build_import_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        top = QHBoxLayout()
        btn_open = QPushButton(self._icon("/mActionFileOpen.svg"), self.tr("Abrir backup..."), page)
        btn_open.clicked.connect(self.load_backup_file)
        self.imp_info = QLabel(self.tr("Nenhum arquivo carregado."), page)
        self.imp_info.setWordWrap(True)
        self.imp_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(btn_open)
        top.addWidget(self.imp_info, 1)
        layout.addLayout(top)

        self.imp_notes = QLabel("", page)
        self.imp_notes.setWordWrap(True)
        self.imp_notes.setVisible(False)
        layout.addWidget(self.imp_notes)

        row, self.imp_filter, self.imp_marker = self._filter_row(page, self.tr("Filtrar..."))
        layout.addLayout(row)

        self.imp_tabs = QTabWidget(page)
        self.imp_plugins = CheckTable([self.tr("Plugin"), self.tr("Configurações"), self.tr("Nome"), self.tr("Pasta"),
                                       self.tr("Versão no backup"), self.tr("Versão local"), self.tr("Ação")],
                                      IMP_NAME, IMP_NAME, page)
        self.imp_conns = CheckTable([self.tr("Restaurar"), self.tr("Tipo"), self.tr("Nome"), self.tr("Endereço"),
                                     self.tr("Situação local")], CON_DETAILS, CON_TYPE, page)
        self.imp_tabs.addTab(self.imp_plugins, self.tr("Plugins e configurações"))
        self.imp_tabs.addTab(self.imp_conns, self.tr("Conexões"))
        layout.addWidget(self.imp_tabs, 1)

        options = QHBoxLayout()
        self.chk_imp_repos = QCheckBox(self.tr("Restaurar repositórios de plugins"), page)
        self.chk_imp_repos.setChecked(True)
        self.remap_from = QLineEdit(page)
        self.remap_from.setPlaceholderText(self.tr("ex.: C:/Users/ana"))
        self.remap_from.setToolTip(self.tr("Prefixo de caminho gravado no backup, a ser trocado na restauração"))
        self.remap_to = QLineEdit(page)
        self.remap_to.setPlaceholderText(self.tr("ex.: /home/ana"))
        options.addWidget(self.chk_imp_repos)
        options.addSpacing(16)
        options.addWidget(QLabel(self.tr("Trocar caminho:"), page))
        options.addWidget(self.remap_from, 1)
        options.addWidget(QLabel("→", page))
        options.addWidget(self.remap_to, 1)
        layout.addLayout(options)

        self.imp_counter = QLabel("", page)
        layout.addWidget(self.imp_counter)
        bottom = QHBoxLayout()
        self.btn_undo = QPushButton(self._icon("/mActionUndo.svg"), self.tr("Desfazer última restauração"), page)
        self.btn_undo.clicked.connect(self.undo_last_restore)
        self.btn_simulate = QPushButton(self.tr("Simular..."), page)
        self.btn_simulate.clicked.connect(lambda: self.restore_backup(simulate=True))
        self.btn_restore = QPushButton(self.tr("Restaurar..."), page)
        self.btn_restore.clicked.connect(lambda: self.restore_backup(simulate=False))
        bottom.addWidget(self.btn_undo)
        bottom.addStretch()
        bottom.addWidget(self.btn_simulate)
        bottom.addWidget(self.btn_restore)
        layout.addLayout(bottom)

        self.imp_filter.textChanged.connect(self.imp_plugins.set_filter)
        self.imp_filter.textChanged.connect(self.imp_conns.set_filter)
        self.imp_plugins.checks_changed.connect(self._update_import_counter)
        self.imp_conns.checks_changed.connect(self._update_import_counter)
        self.imp_marker.menu().aboutToShow.connect(
            lambda: self._fill_marker_menu(self.imp_marker.menu(), self.imp_tabs, self.imp_plugins, self.imp_conns,
                                           IMP_PLUGIN, IMP_CONFIG))
        self._update_import_buttons()
        return page

    def _build_deps_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = QLabel(self.tr("Verifica, nos plugins ativos, dependências de outros plugins (plugin_dependencies) e "
                               "de pacotes Python (requirements.txt e campos equivalentes do metadata.txt). "
                               "A verificação não importa os pacotes."), page)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top = QHBoxLayout()
        btn_check = QPushButton(self._icon("/mActionRefresh.svg"), self.tr("Verificar pendências"), page)
        btn_check.clicked.connect(self.check_dependencies)
        self.dep_summary = QLabel("", page)
        top.addWidget(btn_check)
        top.addWidget(self.dep_summary, 1)
        layout.addLayout(top)

        self.deps_table = CheckTable([self.tr("Instalar"), self.tr("Plugin de origem"), self.tr("Tipo"),
                                      self.tr("Dependência"), self.tr("Situação")], DEP_SOURCE, DEP_STATE, page)
        layout.addWidget(self.deps_table, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_fix = QPushButton(self.tr("Instalar selecionadas..."), page)
        self.btn_fix.clicked.connect(self.fix_dependencies)
        bottom.addWidget(self.btn_fix)
        layout.addLayout(bottom)
        return page

    def _fill_marker_menu(self, menu: QMenu, tabs: QTabWidget, plugins: CheckTable, conns: CheckTable,
                          col_plugin: int, col_config: int) -> None:
        menu.clear()
        if tabs.currentWidget() is plugins:
            menu.addAction(self.tr("Marcar plugins visíveis"), lambda: plugins.set_visible_checks(col_plugin, True))
            menu.addAction(self.tr("Desmarcar plugins visíveis"), lambda: plugins.set_visible_checks(col_plugin, False))
            menu.addSeparator()
            menu.addAction(self.tr("Marcar configurações visíveis"), lambda: plugins.set_visible_checks(col_config, True))
            menu.addAction(self.tr("Desmarcar configurações visíveis"), lambda: plugins.set_visible_checks(col_config, False))
        else:
            menu.addAction(self.tr("Marcar conexões visíveis"), lambda: conns.set_visible_checks(CON_CHECK, True))
            menu.addAction(self.tr("Desmarcar conexões visíveis"), lambda: conns.set_visible_checks(CON_CHECK, False))

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._scan_started:
            self._scan_started = True
            self.start_scan()

    # ------------------------------------------------------------------
    # Varredura
    # ------------------------------------------------------------------
    def start_scan(self) -> None:
        """Inicia a leitura de plugins, configurações e conexões em segundo plano."""
        if self._busy:
            return
        self._set_busy(True, self.tr("Lendo plugins e configurações..."), cancellable=True)
        task = ScanTask(self._on_scan_done)
        task.progressChanged.connect(lambda p: self._set_progress(int(p), 100))
        self._keep_task(task)

    def _on_scan_done(self, task: ScanTask) -> None:
        self._drop_task(task)
        self._set_busy(False)
        res = task.result
        if res.error:
            level = "Warning" if task.isCanceled() else "Critical"
            self.log(self.tr("Falha na leitura: {0}").format(res.error), level)
            self.notify(res.error, level)
            return
        self.scan = res
        for warning in res.warnings:
            self.log(warning, "Warning")
        self._fill_export_tables()
        if self.backup_doc:
            self._fill_import_tables()
        self.log(self.tr("{0} plugins, {1} conexões e {2} chaves de configuração lidos.").format(
            len(res.plugins), len(res.connections), len(res.index.keys)))

    def _fill_export_tables(self) -> None:
        res = self.scan
        rows, ids = [], []
        for p in res.plugins:
            n_keys = res.plugin_key_counts.get(p["key"], 0)
            core = bool(p["core"])
            state = self.tr("Núcleo do QGIS") if core else (self.tr("Ativo") if p["active"] else self.tr("Inativo"))
            icon = self.icon_info if core else (self.icon_ok if p["active"] else None)
            rows.append([
                check_item(p["active"] and not core, enabled=not core,
                           tooltip=self.tr("Plugins do núcleo acompanham o QGIS e não são reinstalados.") if core else ""),
                check_item(p["active"] and not core and n_keys > 0, enabled=n_keys > 0,
                           text=self.tr("{0} chaves").format(n_keys)),
                text_item(p["name"], tooltip=str(p.get("description", ""))),
                text_item(p["key"], tooltip=str(p["path"])),
                text_item(p["version"]),
                text_item(state, icon),
            ])
            ids.append(p["key"])
        self.exp_plugins.set_rows(rows, ids)

        rows, ids = [], []
        for c in res.connections:
            n_secret = sum(1 for k in c["keys"] if is_sensitive_key(k))
            info = self.tr("{0} chaves").format(len(c["keys"]))
            if n_secret:
                info += self.tr(" ({0} com senha/token)").format(n_secret)
            rows.append([check_item(True), text_item(c["provider_type"]), text_item(c["name"]),
                         text_item(c["details"], tooltip=c["details"]), text_item(info)])
            ids.append(c["id"])
        self.exp_conns.set_rows(rows, ids)
        self._update_export_counter()

    def _update_export_counter(self) -> None:
        n_plugins = len(self.exp_plugins.checked_ids(EXP_PLUGIN))
        n_configs = len(self.exp_plugins.checked_ids(EXP_CONFIG))
        n_conns = len(self.exp_conns.checked_ids(CON_CHECK))
        self.exp_counter.setText(self.tr("Selecionados: {0} plugins, configurações de {1} plugins, {2} conexões.")
                                 .format(n_plugins, n_configs, n_conns))

    def _warn_secrets(self, *args: Any) -> None:
        if self.cmb_secrets.currentData() == "plain":
            QMessageBox.warning(self, self.tr("Senhas no arquivo"), self.tr(
                "Senhas e tokens serão gravados sem criptografia no JSON. Qualquer pessoa com acesso ao arquivo "
                "poderá lê-los. Prefira a opção \"protegidas por authcfg\", que guarda as senhas criptografadas."))

    # ------------------------------------------------------------------
    # Exportação
    # ------------------------------------------------------------------
    def _browse_auto_export(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Pasta da exportação automática"),
                                                  self.auto_export_dir.text())
        if folder:
            self.auto_export_dir.setText(folder)
            self._save_auto_export()

    def _save_auto_export(self, *args: Any) -> None:
        folder = self.auto_export_dir.text().strip()
        if (self.chk_auto_export.isChecked() or self.auto_export_days.value()) and not folder:
            self.notify(self.tr("Escolha a pasta da exportação automática."), "Warning")
        settings = QSettings()
        settings.setValue(f"{CONFIG_GROUP}/exportOnQuit", self.chk_auto_export.isChecked())
        settings.setValue(f"{CONFIG_GROUP}/exportEveryDays", self.auto_export_days.value())
        settings.setValue(f"{CONFIG_GROUP}/exportDir", folder)
        settings.setValue(f"{CONFIG_GROUP}/exportKeep", self.auto_export_keep.value())

    def _last_dir(self) -> str:
        return str(QSettings().value(f"{CONFIG_GROUP}/lastDir", os.path.expanduser("~")))

    def _remember_dir(self, path: str) -> None:
        QSettings().setValue(f"{CONFIG_GROUP}/lastDir", os.path.dirname(path))

    def export_backup(self) -> None:
        """Lê os valores selecionados e grava o JSON de forma atômica."""
        if not self.scan:
            self.notify(self.tr("Aguarde a leitura dos plugins terminar."), "Warning")
            return
        secrets_mode = self.cmb_secrets.currentData()
        include_secrets = secrets_mode == "plain"
        index = self.scan.index
        plugins_by_key = {p["key"]: p for p in self.scan.plugins}
        conns_by_id = {c["id"]: c for c in self.scan.connections}

        plugin_keys = [k for k in self.exp_plugins.checked_ids(EXP_PLUGIN) if not plugins_by_key[k]["core"]]
        config_keys = self.exp_plugins.checked_ids(EXP_CONFIG)
        conn_ids = self.exp_conns.checked_ids(CON_CHECK)
        if not (plugin_keys or config_keys or conn_ids):
            self.notify(self.tr("Nada selecionado para exportar."), "Warning")
            return

        host = socket.gethostname().split(".")[0] or "qgis"
        qgis_ver = str(getattr(Qgis, "QGIS_VERSION", "")).split("-")[0]
        stamp = datetime.date.today().isoformat()
        default = os.path.join(self._last_dir(), f"qgis-backup_{host}_{qgis_ver}_{stamp}.json")
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Salvar backup"), default, self.tr("Backup JSON (*.json)"))
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        file_pwd: Optional[str] = None
        if secrets_mode == "authcfg":
            file_pwd = self._ask_password(self.tr("Proteger senhas"), confirm=True)
            if file_pwd is None:
                self.notify(self.tr("Exportação cancelada: a senha do arquivo de autenticação é necessária para "
                                    "incluir as senhas."), "Warning")
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            omitted: List[str] = []
            fallbacks: List[str] = []

            def keep(keys: List[str]) -> List[str]:
                if include_secrets:
                    return keys
                out = []
                for key in keys:
                    (omitted.append(key) if is_sensitive_key(key) else out.append(key))
                return out

            def keep_out(values: Dict[str, Any]) -> List[str]:
                out = [k for k in values if is_sensitive_key(k)]
                omitted.extend(out)
                return out

            plugin_settings = {pk: read_values(keep(index.plugin_keys(pk)), fallbacks=fallbacks) for pk in config_keys}
            providers = []
            credentials: List[Credential] = []
            for cid in conn_ids:
                conn = conns_by_id[cid]
                if secrets_mode == "authcfg":
                    full = read_values(conn["keys"], fallbacks=fallbacks)
                    creds = find_credentials(f"{conn['provider_type']}: {conn['name']}", full)
                    credentials += creds
                    values = full
                else:
                    creds, values = [], read_values(keep(conn["keys"]), fallbacks=fallbacks)
                providers.append({
                    "provider_type": conn["provider_type"], "name": conn["name"], "details": conn["details"],
                    "keys": values, "_creds": creds,
                })
            converted: List[str] = []
            n_auth = 0
            if secrets_mode == "authcfg":
                ids, n_auth = export_protected(authcfg_path_for(path), file_pwd or "", credentials, self.log)
                for prov in providers:
                    values, removed = apply_authcfg(prov["keys"], prov["_creds"], ids)
                    converted += removed
                    # tokens e outras chaves sensíveis sem equivalente em authcfg continuam fora
                    prov["keys"] = {k: v for k, v in values.items() if k not in keep_out(values)}
            for prov in providers:
                prov.pop("_creds", None)
            repos = read_values(keep(self.scan.repository_keys), fallbacks=fallbacks) \
                if self.chk_exp_repos.isChecked() else {}

            doc = build_backup_document(
                plugin_version=self.version,
                source=self._source_info(),
                repositories=repos,
                plugins=[plugins_by_key[k] for k in plugin_keys],
                plugin_settings=plugin_settings,
                providers=providers,
                omitted_sensitive_keys=omitted,
                fallback_keys=fallbacks,
            )
            if secrets_mode == "authcfg":
                doc["authcfg_file"] = os.path.basename(authcfg_path_for(path)) if n_auth else ""
                doc["converted_to_authcfg"] = sorted(converted)
            write_json_atomic(path, doc)
            zipped = self._export_plugin_zip(path, [plugins_by_key[k] for k in plugin_keys]) \
                if self.chk_exp_zip.isChecked() and plugin_keys else []
        except AuthTransferError as exc:
            QApplication.restoreOverrideCursor()
            self.log(self.tr("Senhas não protegidas; nada foi gravado: {0}").format(exc), "Critical")
            QMessageBox.critical(self, self.tr("Erro"), self.tr(
                "Não foi possível proteger as senhas com authcfg; o backup não foi gravado.\n{0}").format(exc))
            return
        except (OSError, TypeError, ValueError) as exc:
            QApplication.restoreOverrideCursor()
            self.log(self.tr("Falha ao gravar o backup: {0}").format(exc), "Critical")
            QMessageBox.critical(self, self.tr("Erro"), self.tr("Falha ao gravar o backup:\n{0}").format(exc))
            return
        QApplication.restoreOverrideCursor()

        self._remember_dir(path)
        n_settings = len(doc["settings"])
        self.log(self.tr("Backup gravado em {0}: {1} plugins, {2} chaves de configuração, {3} conexões, "
                         "{4} chaves de repositórios.").format(path, len(plugin_keys), n_settings, len(providers),
                                                               len(repos)), "Success")
        if converted:
            self.log(self.tr("{0} senhas de conexões guardadas como authcfg em {1} (protegido por senha); o JSON "
                             "não contém senhas.").format(len(converted), authcfg_path_for(path)), "Success")
        if omitted:
            self.log(self.tr("{0} chaves de senha/token não foram exportadas.").format(len(set(omitted))))
        for key in fallbacks:
            self.log(self.tr("Valor de tipo desconhecido gravado como texto: {0}").format(key), "Warning")
        if zipped:
            self.log(self.tr("ZIP de plugins gravado ({0}): {1}").format(len(zipped), ", ".join(zipped)), "Success")
        self.notify(self.tr("Backup gravado: {0} plugins, {1} configurações, {2} conexões.").format(
            len(plugin_keys), n_settings, len(providers)), "Success")
        add_config_history({"path": path, "kind": "manual", "plugins": len(plugin_keys),
                            "connections": len(providers), "secrets": secrets_mode})
        self.history_page.refresh()

    def _export_plugin_zip(self, backup_path: str, plugins: List[Dict[str, Any]]) -> List[str]:
        """Grava o ZIP dos plugins que não estão em repositórios; retorna as chaves incluídas."""
        available = installer.repository_plugins(refresh=False, log=self.log)
        if available:
            outside = [p for p in plugins if not available.get(p["key"], {}).get("zip_repository")]
        else:
            self.log(self.tr("Lista dos repositórios não carregada; todos os plugins selecionados vão no ZIP."))
            outside = list(plugins)
        if not outside:
            return []
        return write_plugins_archive(archive_path_for(backup_path), [(p["key"], p["path"]) for p in outside],
                                     self.log)

    def _ask_password(self, title: str, confirm: bool) -> Optional[str]:
        """Pede uma senha (duas vezes se ``confirm``); ``None`` se cancelado."""
        echo = enum_value(QLineEdit, "EchoMode", "Password")
        while True:
            pwd, ok = QInputDialog.getText(self, title, self.tr("Senha do arquivo de autenticação:"), echo)
            if not ok:
                return None
            if not pwd:
                QMessageBox.warning(self, title, self.tr("A senha não pode ser vazia."))
                continue
            if not confirm:
                return pwd
            again, ok = QInputDialog.getText(self, title, self.tr("Repita a senha:"), echo)
            if not ok:
                return None
            if again == pwd:
                return pwd
            QMessageBox.warning(self, title, self.tr("As senhas não conferem."))

    # ------------------------------------------------------------------
    # Importação
    # ------------------------------------------------------------------
    def load_backup_file(self) -> None:
        """Abre um backup, valida e preenche as tabelas de importação."""
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Abrir backup"), self._last_dir(),
                                              self.tr("Backup JSON (*.json)"))
        if path:
            self.load_backup_path(path)

    def load_backup_path(self, path: str) -> bool:
        """Carrega ``path`` na página Importar; retorna ``True`` se o arquivo é válido."""
        try:
            doc, warnings = load_backup(path)
        except (OSError, BackupFormatError) as exc:
            self.log(self.tr("Falha ao abrir {0}: {1}").format(path, exc), "Critical")
            QMessageBox.critical(self, self.tr("Erro"), self.tr("Falha ao abrir o backup:\n{0}").format(exc))
            return False
        self._remember_dir(path)
        self.backup_doc, self.backup_path = doc, path
        for warning in warnings:
            self.log(warning, "Warning")

        src = doc.get("source", {})
        origin = ", ".join(x for x in (src.get("hostname"), src.get("os"),
                                        ("QGIS " + src["qgis_version"]) if src.get("qgis_version") else "",
                                        (self.tr("perfil ") + src["profile"]) if src.get("profile") else "") if x)
        self.imp_info.setText(self.tr("<b>{0}</b> — gerado em {1}<br>{2}").format(
            os.path.basename(path), self._format_date(doc.get("created_at", "")), origin))

        notes = list(warnings)
        archive = archive_path_for(path)
        self.backup_archive, self.backup_archive_keys = None, []
        if os.path.isfile(archive):
            try:
                self.backup_archive_keys = archive_keys(archive)
                self.backup_archive = archive
                notes.append(self.tr("ZIP de plugins encontrado ({0}): usado para os que não estiverem nos "
                                     "repositórios.").format(", ".join(self.backup_archive_keys)))
            except PluginArchiveError as exc:
                notes.append(str(exc))
        self.backup_authcfg = authcfg_path_for(path) if os.path.isfile(authcfg_path_for(path)) else None
        if self.backup_authcfg:
            notes.append(self.tr("Arquivo de autenticação (authcfg) encontrado; será oferecida a importação."))
        if doc.get("omitted_sensitive_keys"):
            notes.append(self.tr("O backup não contém {0} senhas/tokens; será preciso digitá-los nas conexões "
                                 "restauradas.").format(len(doc["omitted_sensitive_keys"])))
        self.imp_notes.setText("<br>".join("• " + n for n in notes))
        self.imp_notes.setVisible(bool(notes))
        self.log(self.tr("Backup carregado: {0}").format(path))
        self._fill_import_tables()
        self._update_import_buttons()
        return True

    @staticmethod
    def _format_date(value: str) -> str:
        """Converte ISO 8601 em data/hora local legível; devolve o texto original se não reconhecer."""
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%d/%m/%Y %H:%M")

    def _local_plugins(self) -> Dict[str, Dict[str, Any]]:
        return {p["key"]: p for p in self.scan.plugins} if self.scan else {}

    def _fill_import_tables(self) -> None:
        doc = self.backup_doc
        local = self._local_plugins()
        keys_by_plugin = doc.get("plugin_settings_keys", {})
        backup_plugins = {p["key"]: p for p in doc.get("plugins", [])}

        # Plugins listados no backup + plugins que só têm configurações no backup.
        all_keys = list(backup_plugins) + [k for k in keys_by_plugin if k not in backup_plugins]
        rows, ids = [], []
        for key in all_keys:
            bp = backup_plugins.get(key)
            lp = local.get(key)
            n_cfg = len(keys_by_plugin.get(key, []))
            core = bool((bp or {}).get("core")) or bool((lp or {}).get("core"))
            if bp is None:
                action, icon = self.tr("Somente configurações"), None
            elif core:
                action, icon = self.tr("Núcleo do QGIS"), self.icon_info
            elif lp is None:
                action, icon = self.tr("Instalar"), self.icon_warn
            elif not lp["active"]:
                action, icon = self.tr("Ativar"), self.icon_warn
            elif bp.get("version") and lp.get("version") and str(bp["version"]) != str(lp["version"]):
                action, icon = self.tr("Versão diferente"), self.icon_info
            else:
                action, icon = self.tr("Já instalado"), self.icon_ok
            rows.append([
                check_item(bp is not None and not core and bool(bp.get("active", True)),
                           enabled=bp is not None and not core),
                check_item(n_cfg > 0, enabled=n_cfg > 0, text=self.tr("{0} chaves").format(n_cfg)),
                text_item((bp or {}).get("name") or (lp or {}).get("name") or key),
                text_item(key),
                text_item((bp or {}).get("version", "")),
                text_item((lp or {}).get("version", "") if lp else self.tr("ausente")),
                text_item(action, icon),
            ])
            ids.append(key)
        self.imp_plugins.set_rows(rows, ids)

        existing = set(self.scan.index.keys) if self.scan else set()
        rows, ids = [], []
        for prov in doc.get("providers", []):
            exists = any(k in existing for k in prov.get("keys", []))
            rows.append([
                check_item(True), text_item(prov.get("provider_type", "")), text_item(prov.get("name", "")),
                text_item(prov.get("details", "")),
                text_item(self.tr("Já existe (será sobrescrita)") if exists else self.tr("Nova"),
                          self.icon_warn if exists else None),
            ])
            ids.append(prov["id"])
        self.imp_conns.set_rows(rows, ids)
        self._update_import_counter()

    def _update_import_counter(self) -> None:
        if not self.backup_doc:
            self.imp_counter.setText("")
            return
        self.imp_counter.setText(self.tr("Selecionados: {0} plugins, configurações de {1} plugins, {2} conexões.")
                                 .format(len(self.imp_plugins.checked_ids(IMP_PLUGIN)),
                                         len(self.imp_plugins.checked_ids(IMP_CONFIG)),
                                         len(self.imp_conns.checked_ids(CON_CHECK))))

    def _snapshot_dir(self) -> str:
        return snapshot_dir(QgsApplication.qgisSettingsDirPath())

    def _update_import_buttons(self) -> None:
        loaded = self.backup_doc is not None
        self.btn_simulate.setEnabled(loaded)
        self.btn_restore.setEnabled(loaded)
        self.btn_undo.setEnabled(bool(list_snapshots(self._snapshot_dir())))

    def _restore_selection(self) -> Tuple[Dict[str, Any], List[str]]:
        """
        :returns: (plano {chave: valor serializado}, plugins a instalar/ativar).
        """
        plan = build_restore_plan(self.backup_doc, self.imp_plugins.checked_ids(IMP_CONFIG),
                                  self.imp_conns.checked_ids(CON_CHECK), self.chk_imp_repos.isChecked())
        local = self._local_plugins()
        plugins = [k for k in self.imp_plugins.checked_ids(IMP_PLUGIN)
                   if not (k in local and local[k]["active"])]
        # A ativação é feita por install_and_activate; não regravar a chave antes da instalação.
        for key in plugins:
            plan.pop(f"PythonPlugins/{key}", None)
        return plan, plugins

    def _prepared_values(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Desserializa e aplica a troca de prefixo de caminho."""
        old, new = self.remap_from.text().strip(), self.remap_to.text().strip()
        return {k: remap_path_prefix(deserialize_value(v), old, new) for k, v in plan.items()}

    def restore_backup(self, simulate: bool) -> None:
        """Mostra a prévia e, se confirmado, grava as chaves e instala/ativa os plugins."""
        if not self.backup_doc:
            return
        plan, plugins = self._restore_selection()
        if not plan and not plugins:
            self.notify(self.tr("Nada selecionado para restaurar."), "Warning")
            return

        values = self._prepared_values(plan)
        settings = QSettings()
        current_canon: Dict[str, Optional[str]] = {}
        display_current: Dict[str, str] = {}
        plan_canon: Dict[str, Tuple[str, Any]] = {}
        display_new: Dict[str, str] = {}
        for key, value in values.items():
            ser_new = serialize_value(value)
            plan_canon[key] = (canonical(ser_new), value)
            display_new[key] = str(ser_new.get("value"))
            if settings.contains(key):
                ser_cur = serialize_value(settings.value(key))
                current_canon[key] = canonical(ser_cur)
                display_current[key] = str(ser_cur.get("value"))
            else:
                current_canon[key] = None

        notes = []
        local = self._local_plugins()
        to_install = [k for k in plugins if k not in local]
        to_activate = [k for k in plugins if k in local]
        if to_install:
            notes.append(self.tr("Plugins a instalar do repositório: {0}.").format(", ".join(to_install)))
        if to_activate:
            notes.append(self.tr("Plugins a ativar: {0}.").format(", ".join(to_activate)))
        n_authcfg = sum(1 for k, v in values.items() if k.lower().endswith("/authcfg") and v)
        if n_authcfg:
            notes.append(self.tr("{0} conexões usam configuração de autenticação (authcfg); ela precisa existir "
                                 "nesta máquina (Configurações › Opções › Autenticação).").format(n_authcfg))
        notes.append(self.tr("Antes de gravar, o estado atual destas chaves é salvo para permitir desfazer."))

        dialog = RestorePreviewDialog(build_preview_rows(plan_canon, current_canon, display_current, display_new),
                                      notes, confirm=not simulate, parent=self)
        if not dialog.exec() or simulate:
            return

        # Cópia de segurança antes de gravar.
        snapshot = snapshot_keys(list(values) + [f"PythonPlugins/{k}" for k in plugins], settings)
        try:
            snap_path = save_snapshot(self._snapshot_dir(), snapshot)
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Erro"), self.tr(
                "Não foi possível salvar a cópia de segurança; nada foi alterado.\n{0}").format(exc))
            return
        self.log(self.tr("Cópia pré-restauração: {0}").format(snap_path))

        # authcfg antes das chaves: as conexões restauradas já encontram as autenticações.
        if self.backup_authcfg and auth_transfer_available():
            self._import_auth(self.backup_authcfg)

        for key, value in values.items():
            settings.setValue(key, value)
        settings.sync()
        self.log(self.tr("{0} chaves gravadas no QSettings.").format(len(values)), "Success")

        summary = {"installed": [], "activated": [], "failed": [], "restart": []}
        if plugins:
            summary = self._run_plugin_installs(plugins)

        self._update_import_buttons()
        self._show_restore_summary(len(values), summary)
        self.start_scan()

    def _import_auth(self, path: str) -> None:
        answer = QMessageBox.question(
            self, self.tr("Importar autenticação"),
            self.tr("O backup inclui configurações de autenticação (authcfg){0}. Importar agora?\n"
                    "Configurações com o mesmo id já existentes nesta máquina são mantidas.").format(
                self.tr(", com as senhas de {0} conexões").format(len(self.backup_doc.get("converted_to_authcfg", [])))
                if self.backup_doc and self.backup_doc.get("converted_to_authcfg") else ""))
        if answer != enum_value(QMessageBox, "StandardButton", "Yes"):
            return
        pwd = self._ask_password(self.tr("Importar autenticação"), confirm=False)
        if pwd is None:
            return
        try:
            import_authcfg(path, pwd, False, self.log)
            self.log(self.tr("Configurações de autenticação importadas."), "Success")
        except AuthTransferError as exc:
            self.log(str(exc), "Warning")
            self.notify(str(exc), "Warning", 0)

    def _run_plugin_installs(self, keys: List[str]) -> Dict[str, List[str]]:
        self._set_busy(True, self.tr("Instalando e ativando plugins..."), cancellable=True)
        try:
            return installer.install_and_activate(
                keys, self.log, QApplication.processEvents, lambda: self._cancel_requested, self._set_progress,
                self.backup_archive, self.backup_archive_keys)
        finally:
            self._set_busy(False)

    def _show_restore_summary(self, n_values: int, summary: Dict[str, List[str]]) -> None:
        lines = [self.tr("Chaves restauradas: {0}").format(n_values)]
        if summary["installed"]:
            lines.append(self.tr("Plugins instalados: {0}").format(", ".join(summary["installed"])))
        if summary["activated"]:
            lines.append(self.tr("Plugins ativos: {0}").format(", ".join(summary["activated"])))
        if summary["failed"]:
            lines.append(self.tr("Falharam: {0}").format(", ".join(summary["failed"])))
        restart = bool(summary["restart"]) or n_values > 0
        if restart:
            lines.append("")
            lines.append(self.tr("Reinicie o QGIS para que todas as configurações e plugins restaurados "
                                 "tenham efeito."))
        level = QMessageBox.warning if summary["failed"] else QMessageBox.information
        level(self, self.tr("Restauração concluída"), "\n".join(lines))

    def undo_last_restore(self) -> None:
        """Regrava os valores anteriores à última restauração e remove chaves que ela criou."""
        snaps = list_snapshots(self._snapshot_dir())
        if not snaps:
            self._update_import_buttons()
            return
        try:
            to_set, to_remove = load_snapshot(snaps[0])
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, self.tr("Erro"), str(exc))
            return
        answer = QMessageBox.question(self, self.tr("Desfazer restauração"), self.tr(
            "Regravar {0} chaves com o valor anterior e remover {1} chaves criadas pela restauração de {2}?\n"
            "Plugins instalados não são desinstalados.").format(
                len(to_set), len(to_remove), os.path.basename(snaps[0])))
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings = QSettings()
        for key, ser in to_set.items():
            settings.setValue(key, deserialize_value(ser))
        for key in to_remove:
            settings.remove(key)
        settings.sync()
        try:
            os.remove(snaps[0])
        except OSError:
            pass
        self.log(self.tr("Restauração desfeita: {0} chaves regravadas, {1} removidas.").format(
            len(to_set), len(to_remove)), "Success")
        self.notify(self.tr("Restauração desfeita. Reinicie o QGIS para aplicar."), "Success")
        self._update_import_buttons()
        self.start_scan()

    # ------------------------------------------------------------------
    # Pendências
    # ------------------------------------------------------------------
    def check_dependencies(self) -> None:
        """Lista dependências declaradas pelos plugins ativos e indica as ausentes."""
        if not self.scan:
            self.notify(self.tr("Aguarde a leitura dos plugins terminar."), "Warning")
            return
        plugins = self.scan.plugins
        rows, ids = [], []
        missing = 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for plugin in plugins:
                if not plugin["active"]:
                    continue
                deps = read_plugin_dependencies(str(plugin["path"]))
                for warning in deps["warnings"]:
                    self.log(f"{plugin['key']}: {warning}", "Warning")
                for name in deps["qgis"]:
                    ok = resolve_plugin_name(name, plugins) is not None
                    missing += not ok
                    rows.append(self._dep_row(plugin["name"], TYPE_QGIS, name, ok))
                    ids.append(f"{TYPE_QGIS}|{name}")
                for name in deps["python"]:
                    ok = is_distribution_installed(name)
                    missing += not ok
                    rows.append(self._dep_row(plugin["name"], TYPE_PYTHON, name, ok))
                    ids.append(f"{TYPE_PYTHON}|{name}")
        finally:
            QApplication.restoreOverrideCursor()
        self.deps_table.set_rows(rows, ids)
        text = self.tr("{0} dependências verificadas, {1} ausentes.").format(len(rows), missing)
        self.dep_summary.setText(text)
        self.log(text)

    def _dep_row(self, source: str, kind: str, name: str, ok: bool) -> List[Any]:
        return [
            check_item(not ok, enabled=not ok),
            text_item(source), text_item(kind), text_item(name),
            text_item(self.tr("Instalado") if ok else self.tr("Ausente"), self.icon_ok if ok else self.icon_bad),
        ]

    def fix_dependencies(self) -> None:
        """Instala as dependências marcadas, após confirmação explícita."""
        selected = sorted(set(self.deps_table.checked_ids(DEP_CHECK)))
        qgis_deps = [s.split("|", 1)[1] for s in selected if s.startswith(TYPE_QGIS + "|")]
        py_deps = [s.split("|", 1)[1] for s in selected if s.startswith(TYPE_PYTHON + "|")]
        if not qgis_deps and not py_deps:
            self.notify(self.tr("Nenhuma dependência marcada."), "Warning")
            return

        interpreter = None
        if py_deps:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                interpreter = find_python_interpreter()
            finally:
                QApplication.restoreOverrideCursor()
            if not interpreter:
                QMessageBox.warning(self, self.tr("Python não encontrado"), self.tr(
                    "Não foi possível localizar o interpretador Python do QGIS para executar o pip. "
                    "Instale os pacotes manualmente (no Windows, pelo OSGeo4W Shell): {0}").format(", ".join(py_deps)))
                py_deps = []

        lines = []
        if qgis_deps:
            lines.append(self.tr("Plugins QGIS (do repositório): {0}").format(", ".join(qgis_deps)))
        if py_deps:
            lines.append(self.tr("Pacotes Python (pip install --user): {0}").format(", ".join(py_deps)))
            lines.append(self.tr("Interpretador: {0}").format(interpreter))
            lines.append(self.tr("Pacotes do PyPI executam código de terceiros. Confirme que os nomes estão corretos."))
        if not lines:
            return
        answer = QMessageBox.question(self, self.tr("Instalar dependências"), "\n\n".join(lines))
        if answer != QMessageBox.StandardButton.Yes:
            return

        if qgis_deps:
            summary = self._run_plugin_installs(qgis_deps)
            if summary["failed"]:
                self.notify(self.tr("Plugins não instalados: {0}").format(", ".join(summary["failed"])), "Warning")
        if py_deps and interpreter:
            self._set_busy(True, self.tr("Instalando pacotes Python..."), cancellable=True)
            task = PipInstallTask(interpreter, py_deps, self._on_pip_done)
            task.log_message.connect(self.log)
            task.progressChanged.connect(lambda p: self._set_progress(int(p), 100))
            self._keep_task(task)
        elif qgis_deps:
            self.start_scan()

    def _on_pip_done(self, task: PipInstallTask) -> None:
        self._drop_task(task)
        self._set_busy(False)
        if task.error:
            self.log(task.error, "Critical")
        if task.installed:
            self.log(self.tr("Pacotes instalados: {0}").format(", ".join(task.installed)), "Success")
        if task.failed or task.error:
            self.notify(self.tr("Falha ao instalar: {0}").format(", ".join(task.failed) or task.error), "Critical")
        elif task.installed:
            self.notify(self.tr("Pacotes instalados. Reinicie o QGIS se algum plugin ainda não os encontrar."),
                        "Success")
        self.start_scan()
        self.check_dependencies()

