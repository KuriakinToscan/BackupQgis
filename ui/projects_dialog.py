# -*- coding: utf-8 -*-
"""
Janela "Projetos e dados": backup de projetos com os dados vinculados,
sincronização de pastas e histórico dos backups.

É independente da janela de configurações (ui/dialog.py): preferências em
``BackupQgis/projects`` e ``BackupQgis/sync``, log, tarefas e automação
próprios.
"""

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStyle,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.project_backup import MANIFEST, list_own_snapshots, verify_snapshot
from ..core.project_mirror import VERSIONS, find_mirrors, list_states, verify_mirror
from ..core.report import RunReport
from ..tasks.function_task import FunctionTask
from .projects_page import ProjectsPage, human_size
from .shell import ShellDialog
from .sync_page import SyncPage

COLUMNS = ("Data", "Projeto", "Origem", "Arquivos", "Alterações", "Tamanho", "Falhas", "Pasta")


def _format_date(value: str) -> str:
    try:
        return _dt.datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _count_versions(bkp: str) -> int:
    folder = os.path.join(bkp, VERSIONS)
    try:
        return sum(1 for n in os.listdir(folder) if n.lower().endswith((".qgz", ".qgs")))
    except OSError:
        return 0


class SnapshotHistoryPage(QWidget):
    """
    Backups do destino de projetos, do mais recente para o mais antigo: cada
    data de cada pasta ``<projeto>_bkp`` e os snapshots do formato anterior.
    """

    def __init__(self, host: Any) -> None:
        super().__init__(host)
        self.host = host
        self.entries: List[Dict[str, Any]] = []
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
        self.btn_verify.setToolTip(self.tr("Recalcula o SHA-256 de cada arquivo da data selecionada e compara com o "
                                           "manifesto."))
        self.btn_verify.clicked.connect(self.verify_selected)
        self.btn_restore = QPushButton(self.tr("Restaurar..."), self)
        self.btn_restore.setToolTip(self.tr("Restaura os arquivos como estavam na data selecionada."))
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

    def _load(self, dest: str) -> Tuple[int, int]:
        """Lê as datas das pastas ``_bkp`` e os snapshots antigos; retorna (pastas _bkp, versões)."""
        entries: List[Dict[str, Any]] = []
        mirrors = find_mirrors(dest) if dest else []
        versions = 0
        for bkp in mirrors:
            versions += _count_versions(bkp)
            for state in list_states(bkp):
                ch = state.get("changes", {})
                entries.append({
                    "kind": "mirror", "path": bkp, "stamp": state["stamp"], "sort": state["stamp"],
                    "date": state.get("created_at", ""), "project": os.path.basename(state.get("project", "")),
                    "trigger": (state.get("source") or {}).get("trigger", "manual"),
                    "files": len(state.get("files", [])), "bytes": state.get("total_bytes", 0),
                    "errors": len(state.get("errors", [])),
                    "changes": self.tr("+{0} ~{1} −{2}").format(ch.get("copied", 0), ch.get("updated", 0),
                                                              ch.get("removed", 0)),
                    "folder": f"{os.path.basename(bkp)} ({state['stamp']})"})
        for path in list_own_snapshots(dest) if dest else []:
            try:
                with open(os.path.join(path, MANIFEST), "r", encoding="utf-8") as handle:
                    m = json.load(handle)
            except (OSError, ValueError):
                continue
            entries.append({
                "kind": "legacy", "path": path, "stamp": None, "sort": os.path.basename(path),
                "date": m.get("created_at", ""),
                "project": self.tr("{0} projeto(s) — formato anterior").format(len(m.get("projects", []))),
                "trigger": (m.get("source") or {}).get("trigger", "manual"), "files": len(m.get("files", [])),
                "bytes": m.get("total_bytes", 0), "errors": len(m.get("errors", [])), "changes": "",
                "folder": os.path.basename(path)})
        entries.sort(key=lambda e: (e["date"], e["sort"]), reverse=True)
        self.entries = entries
        return len(mirrors), versions

    def refresh(self) -> None:
        """Relê os backups do destino configurado na página Projetos."""
        dest = self._dest()
        n_mirrors, n_versions = self._load(dest)
        if not dest:
            self.info.setText(self.tr("Defina a pasta de destino na página Projetos."))
        elif not os.path.isdir(dest):
            self.info.setText(self.tr("Destino não encontrado: {0} (HD/NAS desconectado?)").format(dest))
        else:
            self.info.setText(self.tr("{0} backups de {1} projeto(s) em {2}; {3} versões de projeto guardadas ao "
                                      "salvar (pastas _versoes).").format(len(self.entries), n_mirrors, dest,
                                                                          n_versions))
        self.table.setRowCount(len(self.entries))
        for row, e in enumerate(self.entries):
            values = [_format_date(e["date"]), e["project"], self.tr(e["trigger"]), str(e["files"]), e["changes"],
                      human_size(e["bytes"]), str(e["errors"]), e["folder"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 6:
                    item.setIcon(self.host.icon_warn if e["errors"] else self.host.icon_ok)
                if col == 7:
                    item.setToolTip(e["path"])
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self._update_buttons()

    def _selected(self) -> Optional[Dict[str, Any]]:
        rows = {i.row() for i in self.table.selectedItems()}
        return self.entries[rows.pop()] if rows else None

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        for btn in (self.btn_folder, self.btn_verify, self.btn_restore):
            btn.setEnabled(has)

    def open_folder(self) -> None:
        entry = self._selected()
        if entry:
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry["path"]))

    def restore_selected(self) -> None:
        entry = self._selected()
        if entry:
            self.host.projects_page.run_restore(entry["path"], entry["stamp"] if entry["kind"] == "mirror" else None)

    def verify_selected(self) -> None:
        entry = self._selected()
        if not entry or self.host._busy:
            return
        path, stamp, mirror = entry["path"], entry["stamp"], entry["kind"] == "mirror"
        title = self.tr("Verificação de {0}").format(entry["folder"])

        def work(task: FunctionTask) -> List[str]:
            return verify_mirror(path, stamp, task.isCanceled) if mirror else verify_snapshot(path, task.isCanceled)

        def done(task: FunctionTask) -> None:
            self.host._drop_task(task)
            self.host._set_busy(False)
            if task.isCanceled():
                self.host.notify(self.tr("Operação cancelada."), "Warning")
                return
            report = RunReport(title)
            if task.error:
                report.error(self.tr("Leitura do manifesto e dos arquivos"), task.error)
            elif task.value:
                report.error(self.tr("Conferência SHA-256"), self.tr("{0} problemas").format(len(task.value)),
                             task.value)
            else:
                report.ok(self.tr("Conferência SHA-256"),
                          self.tr("{0} arquivos conferem com o manifesto").format(entry["files"]))
            self.host.log_report(report)

        self.host._set_busy(True, self.tr("Verificando integridade..."), cancellable=True)
        task = FunctionTask("BackupQgis: verificar backup", work, done)
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
