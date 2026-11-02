# -*- coding: utf-8 -*-
"""
Página "Histórico" da janela de configurações: backups de configuração
gravados (manuais e automáticos), com atalho para restaurar.
"""

import datetime as _dt
import glob
import os
from typing import Any, Dict, List

from qgis.PyQt.QtCore import QSettings, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.auto_export import PREFIX as AUTO_PREFIX
from .auth_io import authcfg_path_for
from .common import CONFIG_GROUP, load_config_history

SECRETS_LABELS = {"none": "não incluídas", "authcfg": "protegidas (authcfg)", "plain": "texto puro"}
COLUMNS = ("Data", "Tipo", "Arquivo", "Plugins", "Conexões", "Senhas", "Situação")


def _format_date(value: str) -> str:
    try:
        return _dt.datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return value or ""


class ConfigHistoryPage(QWidget):
    """Lista dos backups de configuração; o host é a janela de configurações."""

    def __init__(self, host: Any) -> None:
        super().__init__(host)
        self.host = host
        self.entries: List[Dict[str, Any]] = []
        layout = QVBoxLayout(self)
        intro = QLabel(self.tr("Backups de configuração feitos nesta máquina (manuais e automáticos). Os arquivos "
                               "automáticos encontrados na pasta da exportação automática também aparecem."), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([self.tr(c) for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self.restore_selected())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.btn_refresh = QPushButton(self.tr("Atualizar"), self)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_folder = QPushButton(self.tr("Abrir pasta"), self)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_restore = QPushButton(self.tr("Restaurar este backup..."), self)
        self.btn_restore.clicked.connect(self.restore_selected)
        buttons.addWidget(self.btn_refresh)
        buttons.addStretch()
        buttons.addWidget(self.btn_folder)
        buttons.addWidget(self.btn_restore)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    # ------------------------------------------------------------------
    def _collect(self) -> List[Dict[str, Any]]:
        entries = load_config_history()
        known = {os.path.normcase(e["path"]) for e in entries}
        folder = str(QSettings().value(f"{CONFIG_GROUP}/exportDir", "")).strip()
        if folder and os.path.isdir(folder):
            for path in glob.glob(os.path.join(folder, f"{AUTO_PREFIX}*.json")):
                if os.path.normcase(path) not in known:
                    stamp = _dt.datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0).isoformat()
                    entries.append({"path": path, "kind": "automático", "created_at": stamp, "secrets": "none"})
        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return entries

    def refresh(self) -> None:
        """Relê o histórico e a pasta da exportação automática."""
        self.entries = self._collect()
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            exists = os.path.isfile(entry["path"])
            secrets = entry.get("secrets", "none")
            if secrets == "authcfg" and exists and not os.path.isfile(authcfg_path_for(entry["path"])):
                status = self.tr("sem o .authcfg.xml")
                icon = self.host.icon_warn
            elif exists:
                status, icon = self.tr("disponível"), self.host.icon_ok
            else:
                status, icon = self.tr("arquivo não encontrado"), self.host.icon_bad
            values = [_format_date(entry.get("created_at", "")), self.tr(entry.get("kind", "")),
                      entry["path"], str(entry.get("plugins", "")), str(entry.get("connections", "")),
                      self.tr(SECRETS_LABELS.get(secrets, secrets)), status]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2:
                    item.setToolTip(value)
                if col == 6:
                    item.setIcon(icon)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._update_buttons()

    def _selected(self) -> Dict[str, Any]:
        rows = {i.row() for i in self.table.selectedItems()}
        return self.entries[rows.pop()] if rows else {}

    def _update_buttons(self) -> None:
        entry = self._selected()
        self.btn_restore.setEnabled(bool(entry) and os.path.isfile(entry.get("path", "")))
        self.btn_folder.setEnabled(bool(entry))

    def open_folder(self) -> None:
        entry = self._selected()
        if entry:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(entry["path"])))

    def restore_selected(self) -> None:
        """Carrega o backup selecionado na página Importar."""
        entry = self._selected()
        if entry and os.path.isfile(entry["path"]) and self.host.load_backup_path(entry["path"]):
            self.host.show_page(self.host.import_page)
