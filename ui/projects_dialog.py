# -*- coding: utf-8 -*-
"""
Janela "Projetos e dados": backup de projetos com os dados vinculados,
sincronização de pastas e histórico de snapshots.

É independente da janela de configurações (ui/dialog.py): preferências em
``BackupQgis/projects`` e ``BackupQgis/sync``, log, tarefas e automação
próprios.
"""

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStyle,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.project_backup import MANIFEST, list_own_snapshots, verify_snapshot
from ..tasks.function_task import FunctionTask
from .projects_page import ProjectsPage, human_size
from .shell import ShellDialog
from .sync_page import SyncPage

COLUMNS = ("Data", "Origem", "Projetos", "Arquivos", "Tamanho", "Falhas", "Pasta")


def _format_date(value: str) -> str:
    try:
        return _dt.datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _count_versions(dest: str) -> int:
    folder = os.path.join(dest, "versoes")
    return sum(len(files) for _r, _d, files in os.walk(folder)) if os.path.isdir(folder) else 0


class SnapshotHistoryPage(QWidget):
    """Snapshots do destino de projetos, do mais recente para o mais antigo."""

    def __init__(self, host: Any) -> None:
        super().__init__(host)
        self.host = host
        self.snapshots: List[Dict[str, Any]] = []
        layout = QVBoxLayout(self)
        self.info = QLabel("", self)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([self.tr(c) for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.btn_refresh = QPushButton(self.tr("Atualizar"), self)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_folder = QPushButton(self.tr("Abrir pasta"), self)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_verify = QPushButton(self.tr("Verificar integridade"), self)
        self.btn_verify.setToolTip(self.tr("Recalcula o SHA-256 de cada arquivo e compara com o manifesto."))
        self.btn_verify.clicked.connect(self.verify_selected)
        self.btn_restore = QPushButton(self.tr("Restaurar..."), self)
        self.btn_restore.clicked.connect(self.restore_selected)
        buttons.addWidget(self.btn_refresh)
        buttons.addStretch()
        buttons.addWidget(self.btn_folder)
        buttons.addWidget(self.btn_verify)
        buttons.addWidget(self.btn_restore)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    # ------------------------------------------------------------------
    def _dest(self) -> str:
        return self.host.projects_page.dest.text().strip()

    def refresh(self) -> None:
        """Relê os snapshots do destino configurado na página Projetos."""
        dest = self._dest()
        self.snapshots = []
        for path in reversed(list_own_snapshots(dest) if dest else []):
            try:
                with open(os.path.join(path, MANIFEST), "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except (OSError, ValueError):
                continue
            self.snapshots.append({"path": path, "manifest": manifest})
        if not dest:
            self.info.setText(self.tr("Defina a pasta de destino na página Projetos."))
        elif not os.path.isdir(dest):
            self.info.setText(self.tr("Destino não encontrado: {0} (HD/NAS desconectado?)").format(dest))
        else:
            self.info.setText(self.tr("{0} snapshots em {1}; {2} arquivos de versões de projeto em {3}.").format(
                len(self.snapshots), dest, _count_versions(dest), os.path.join(dest, "versoes")))
        self.table.setRowCount(len(self.snapshots))
        for row, snap in enumerate(self.snapshots):
            m = snap["manifest"]
            trigger = (m.get("source") or {}).get("trigger", "manual")
            errors = len(m.get("errors", []))
            values = [_format_date(m.get("created_at", "")), self.tr(trigger), str(len(m.get("projects", []))),
                      str(len(m.get("files", []))), human_size(m.get("total_bytes", 0)), str(errors),
                      os.path.basename(snap["path"])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 5:
                    item.setIcon(self.host.icon_warn if errors else self.host.icon_ok)
                if col == 6:
                    item.setToolTip(snap["path"])
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self._update_buttons()

    def _selected(self) -> Optional[Dict[str, Any]]:
        rows = {i.row() for i in self.table.selectedItems()}
        return self.snapshots[rows.pop()] if rows else None

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        for btn in (self.btn_folder, self.btn_verify, self.btn_restore):
            btn.setEnabled(has)

    def open_folder(self) -> None:
        snap = self._selected()
        if snap:
            QDesktopServices.openUrl(QUrl.fromLocalFile(snap["path"]))

    def restore_selected(self) -> None:
        snap = self._selected()
        if snap:
            self.host.projects_page.run_restore(snap["path"])

    def verify_selected(self) -> None:
        snap = self._selected()
        if not snap or self.host._busy:
            return
        path = snap["path"]

        def work(task: FunctionTask) -> List[str]:
            return verify_snapshot(path, task.isCanceled)

        def done(task: FunctionTask) -> None:
            self.host._drop_task(task)
            self.host._set_busy(False)
            if task.error:
                self.host.notify(task.error, "Critical", 0)
            elif task.value:
                for problem in task.value:
                    self.host.log(problem, "Warning")
                self.host.notify(self.tr("{0} problemas no snapshot; veja o log.").format(len(task.value)),
                                 "Warning", 0)
            elif not task.isCanceled():
                self.host.notify(self.tr("Snapshot íntegro: todos os arquivos conferem com o manifesto."), "Success")

        self.host._set_busy(True, self.tr("Verificando snapshot..."), cancellable=True)
        task = FunctionTask("BackupQgis: verificar snapshot", work, done)
        task.log_message.connect(self.host.log)
        self.host._keep_task(task)


class ProjectsDialog(ShellDialog):
    """Janela "Projetos e dados"."""

    def __init__(self, iface: Any, plugin_dir: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(iface, plugin_dir,
                         QCoreApplication.translate("ProjectsDialog",
                                                    "BackupQgis — projetos e dados (backup e sincronização)"),
                         "/mIconQgsProjectFile.svg", parent)
        sp = QStyle.StandardPixmap
        self.projects_page = ProjectsPage(self)
        self._add_page(self.projects_page, self.tr("Projetos"), self._icon("/mIconQgsProjectFile.svg", sp.SP_FileIcon))
        self.sync_page = SyncPage(self)
        self._add_page(self.sync_page, self.tr("Sincronizar"), self._icon("/mActionRefresh.svg", sp.SP_BrowserReload))
        self.history_page = SnapshotHistoryPage(self)
        self._add_page(self.history_page, self.tr("Histórico"),
                       self._icon("/mActionHistory.svg", sp.SP_FileDialogDetailedView))
        self.projects_page.dest.editingFinished.connect(self.history_page.refresh)
        self.nav.setCurrentRow(0)
