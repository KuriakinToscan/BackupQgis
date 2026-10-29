# -*- coding: utf-8 -*-
"""
Moldura comum às janelas do BackupQgis: barra de mensagens do QGIS no topo,
navegação lateral, progresso, log recolhível e o "host" usado pelas páginas
(log, mensagens, estado ocupado e tarefas em segundo plano).

Cada janela (configurações, projetos) tem a sua instância, com log, progresso
e tarefas próprios: uma operação numa janela não bloqueia a outra.
"""

import datetime as _dt
import os
from typing import Any, Dict, List, Optional

from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.gui import QgsCollapsibleGroupBox, QgsMessageBar
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QFontDatabase, QGuiApplication, QIcon
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                                 QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                                 QStackedWidget, QStyle, QVBoxLayout, QWidget)

from ..core.compat import enum_value
from ..core.report import ERROR, OK, RunReport
from .common import source_info

LOG_TAG = "BackupQgis"
REPORT_LEVELS = {OK: "Success", ERROR: "Critical"}


def level(name: str) -> Any:
    """Nível de mensagem do QGIS a partir do nome ("Info", "Warning"...)."""
    return enum_value(Qgis, "MessageLevel", name)


class ShellDialog(QDialog):
    """Janela com navegação lateral; as subclasses adicionam páginas com :meth:`_add_page`."""

    def __init__(self, iface: Any, plugin_dir: str, title: str, icon_path: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent if parent is not None else iface.mainWindow())
        self.iface = iface
        self.plugin_dir = plugin_dir
        self._tasks: List[Any] = []
        self._busy = False
        self._cancel_requested = False
        self._build_shell(title, icon_path)

    # ------------------------------------------------------------------
    def _icon(self, theme_name: str, fallback: Optional[Any] = None) -> QIcon:
        icon = QgsApplication.getThemeIcon(theme_name)
        if icon.isNull() and fallback is not None:
            icon = self.style().standardIcon(fallback)
        return icon

    def _build_shell(self, title: str, icon_path: str) -> None:
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(icon_path) if os.path.isfile(icon_path) else QgsApplication.getThemeIcon(icon_path))
        self.resize(980, 680)
        self.setMinimumSize(760, 520)

        sp = QStyle.StandardPixmap
        self.icon_ok = self.style().standardIcon(sp.SP_DialogApplyButton)
        self.icon_warn = self.style().standardIcon(sp.SP_MessageBoxWarning)
        self.icon_bad = self.style().standardIcon(sp.SP_MessageBoxCritical)
        self.icon_info = self.style().standardIcon(sp.SP_MessageBoxInformation)

        root = QVBoxLayout(self)
        self.message_bar = QgsMessageBar(self)
        self.message_bar.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        root.addWidget(self.message_bar)

        body = QHBoxLayout()
        self.nav = QListWidget(self)
        self.nav.setIconSize(QSize(24, 24))
        self.nav.setFixedWidth(170)
        self.stack = QStackedWidget(self)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        self.progress_label = QLabel("", self)
        self.btn_cancel = QPushButton(self.tr("Cancelar"), self)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._request_cancel)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.btn_cancel)
        root.addLayout(progress_row)

        self.log_box = QgsCollapsibleGroupBox(self.tr("Log"), self)
        self.log_box.setCollapsed(True)
        log_layout = QVBoxLayout(self.log_box)
        self.log_view = QPlainTextEdit(self.log_box)
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMaximumHeight(180)
        log_layout.addWidget(self.log_view)
        log_buttons = QHBoxLayout()
        btn_copy = QPushButton(self.tr("Copiar"), self.log_box)
        btn_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log_view.toPlainText()))
        btn_save_log = QPushButton(self.tr("Salvar..."), self.log_box)
        btn_save_log.clicked.connect(self._save_log)
        log_buttons.addStretch()
        log_buttons.addWidget(btn_copy)
        log_buttons.addWidget(btn_save_log)
        log_layout.addLayout(log_buttons)
        self.log_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        root.addWidget(self.log_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

    def _add_page(self, widget: QWidget, label: str, icon: QIcon) -> None:
        self.stack.addWidget(widget)
        QListWidgetItem(icon, label, self.nav)
        if self.nav.currentRow() < 0:
            self.nav.setCurrentRow(0)

    def show_page(self, widget: QWidget) -> None:
        """Seleciona a página que contém ``widget``."""
        index = self.stack.indexOf(widget)
        if index >= 0:
            self.nav.setCurrentRow(index)

    # ------------------------------------------------------------------
    # Host das páginas: log, mensagens, estado ocupado e tarefas
    # ------------------------------------------------------------------
    def log(self, message: str, level_name: str = "Info") -> None:
        """Registra no log da janela (com hora) e no painel de mensagens do QGIS (aba BackupQgis)."""
        self.log_view.appendPlainText(f"[{_dt.datetime.now():%H:%M:%S}] {message}")
        QgsMessageLog.logMessage(message, LOG_TAG, level(level_name))

    def log_report(self, report: RunReport, notify: bool = True) -> None:
        """
        Escreve no log todas as tarefas da operação e o resumo, abre o log da
        janela e mostra o resumo na barra de mensagens (fixo se houver erro).
        """
        report.finish()
        stamp = f"[{_dt.datetime.now():%H:%M:%S}]"
        lines = report.lines()
        self.log_view.appendPlainText("\n".join(f"{stamp} {line}" for line in lines))
        level_name = REPORT_LEVELS.get(report.status, "Warning")
        QgsMessageLog.logMessage("\n".join(lines), LOG_TAG, level(level_name))
        self.log_box.setCollapsed(False)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        if notify:
            text = f"{report.title}. {report.summary()}"
            if report.status != OK:
                text += " " + self.tr("Veja o log.")
            self.notify(text, level_name, 0 if report.status == ERROR else 10)

    def notify(self, text: str, level_name: str = "Info", duration: int = 8) -> None:
        """Mensagem não modal na barra da janela."""
        title = {"Success": self.tr("Concluído"), "Warning": self.tr("Atenção"),
                 "Critical": self.tr("Erro")}.get(level_name, "")
        self.message_bar.pushMessage(title, text, level(level_name), duration)

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Salvar log"), "backupqgis.log",
                                              self.tr("Texto (*.log *.txt)"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.log_view.toPlainText())
        except OSError as exc:
            self.notify(str(exc), "Critical")

    def _set_busy(self, busy: bool, label: str = "", cancellable: bool = False) -> None:
        self._busy = busy
        self._cancel_requested = False
        self.nav.setEnabled(not busy)
        self.stack.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.progress_label.setText(label if busy else "")
        self.btn_cancel.setVisible(busy and cancellable)
        if busy:
            self.progress.setRange(0, 0)

    def _set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        for task in list(self._tasks):
            try:
                task.cancel()
            except RuntimeError:
                pass
        self.log(self.tr("Cancelamento solicitado."))

    def _keep_task(self, task: Any) -> None:
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _drop_task(self, task: Any) -> None:
        if task in self._tasks:
            self._tasks.remove(task)

    def _source_info(self) -> Dict[str, str]:
        return source_info(self.iface)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def closeEvent(self, event: Any) -> None:
        if self._busy and not self._tasks:
            # Trabalho na thread principal em andamento (instalação de plugins).
            self._request_cancel()
            self.notify(self.tr("Aguarde o fim da operação em andamento; o restante foi cancelado."), "Warning")
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Chamado ao descarregar o plugin: cancela tarefas e fecha a janela."""
        self._cancel_requested = True
        for task in list(self._tasks):
            try:
                task.cancel()
            except RuntimeError:
                pass
        self._tasks.clear()
        self.close()
