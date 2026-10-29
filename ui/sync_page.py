# -*- coding: utf-8 -*-
"""
Página "Sincronizar": pares de pastas origem → destino (HD externo, NAS).
"""

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QMessageBox, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core.file_copy import sha256_file
from ..core.sync import (BiPlan, SyncAborted, build_bidirectional_plan, build_plan, build_state, check_target,
                         default_excludes_text, execute_bidirectional, execute_plan, guard_bidirectional, guard_plan,
                         init_marker, load_state, parse_excludes, read_marker, save_state, scan_tree)
from .common import SYNC_PAIRS_KEY, sync_state_path
from ..tasks.function_task import FunctionTask
from .projects_page import human_size
from .widgets import CheckTable, text_item

SETTINGS_KEY = SYNC_PAIRS_KEY
MAX_ROWS = 5000
MODES = (("additive", "Aditivo: copia novos e alterados, nunca apaga no destino"),
         ("mirror", "Espelho: o destino fica igual à origem (removidos vão para a lixeira do destino)"),
         ("bidirectional", "Bidirecional (experimental): leva alterações nos dois sentidos; conflitos preservam "
                           "as duas versões"))


class SyncPage(QWidget):
    """Cadastro de pares, simulação obrigatória e execução da sincronização."""

    def __init__(self, host: Any) -> None:
        """:param host: diálogo principal (log, mensagens, progresso, tarefas)."""
        super().__init__(host)
        self.host = host
        self.pairs: List[Dict[str, Any]] = self._load_pairs()
        self.plan = None
        self.plan_pair_id: Optional[str] = None

        layout = QVBoxLayout(self)
        intro = QLabel(self.tr("Mantém uma cópia de uma pasta em outro disco. A primeira etapa é sempre a simulação; "
                               "nada é gravado até você confirmar."), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top = QHBoxLayout()
        self.pair_list = QListWidget(self)
        self.pair_list.setMaximumWidth(220)
        side = QVBoxLayout()
        side.addWidget(self.pair_list, 1)
        side_buttons = QHBoxLayout()
        btn_new = QPushButton(self.tr("Novo"), self)
        btn_new.clicked.connect(self.new_pair)
        btn_del = QPushButton(self.tr("Remover"), self)
        btn_del.clicked.connect(self.remove_pair)
        side_buttons.addWidget(btn_new)
        side_buttons.addWidget(btn_del)
        side.addLayout(side_buttons)
        top.addLayout(side)

        form_box = QWidget(self)
        form = QFormLayout(form_box)
        self.name = QLineEdit(form_box)
        self.source = QLineEdit(form_box)
        self.dest = QLineEdit(form_box)
        self.mode = QComboBox(form_box)
        for key, label in MODES:
            self.mode.addItem(self.tr(label), key)
        self.excludes = QLineEdit(form_box)
        self.excludes.setToolTip(self.tr("Padrões separados por ponto e vírgula (ex.: *.tmp;__pycache__)"))
        self.target_status = QLabel("", form_box)
        self.target_status.setWordWrap(True)
        form.addRow(self.tr("Nome:"), self.name)
        form.addRow(self.tr("Origem:"), self._with_browse(self.source))
        form.addRow(self.tr("Destino:"), self._with_browse(self.dest))
        form.addRow(self.tr("Modo:"), self.mode)
        form.addRow(self.tr("Excluir:"), self.excludes)
        auto_row = QHBoxLayout()
        self.auto = QCheckBox(self.tr("Sincronizar automaticamente a cada"), form_box)
        self.auto.setToolTip(self.tr("Somente modos aditivo e espelho. Com o destino desconectado, a execução é "
                                     "ignorada e registrada no log."))
        self.interval = QSpinBox(form_box)
        self.interval.setRange(5, 1440)
        self.interval.setValue(60)
        self.interval.setSuffix(self.tr(" min"))
        auto_row.addWidget(self.auto)
        auto_row.addWidget(self.interval)
        auto_row.addStretch()
        form.addRow("", auto_row)
        form.addRow(self.tr("Situação:"), self.target_status)
        btn_save = QPushButton(self.tr("Salvar par"), form_box)
        btn_save.clicked.connect(self.save_pair)
        form.addRow("", btn_save)
        top.addWidget(form_box, 1)
        layout.addLayout(top)

        self.table = CheckTable([self.tr("Ação"), self.tr("Arquivo"), self.tr("Tamanho")], 1, 0, self)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.btn_simulate = QPushButton(self.tr("Simular"), self)
        self.btn_simulate.clicked.connect(self.simulate)
        self.btn_sync = QPushButton(self.tr("Sincronizar"), self)
        self.btn_sync.setEnabled(False)
        self.btn_sync.clicked.connect(self.synchronize)
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.btn_simulate)
        bottom.addWidget(self.btn_sync)
        layout.addLayout(bottom)

        self.pair_list.currentRowChanged.connect(self._show_pair)
        for widget in (self.source, self.dest, self.excludes):
            widget.textEdited.connect(self._invalidate_plan)
        self.mode.currentIndexChanged.connect(self._invalidate_plan)
        self.mode.currentIndexChanged.connect(self._update_auto_enabled)
        self._refresh_list()
        if not self.pairs:
            self.new_pair()

    # ------------------------------------------------------------------
    def _with_browse(self, edit: QLineEdit) -> QWidget:
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        btn = QToolButton(box)
        btn.setText("...")
        btn.clicked.connect(lambda: self._browse(edit))
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return box

    def _browse(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Escolher pasta"), edit.text())
        if folder:
            edit.setText(folder)
            self._invalidate_plan()

    def _load_pairs(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(str(QSettings().value(SETTINGS_KEY, "[]")))
            return [p for p in data if isinstance(p, dict) and p.get("id")]
        except ValueError:
            return []

    def _store_pairs(self) -> None:
        QSettings().setValue(SETTINGS_KEY, json.dumps(self.pairs, ensure_ascii=False))

    def _refresh_list(self, select_id: Optional[str] = None) -> None:
        self.pair_list.blockSignals(True)
        self.pair_list.clear()
        row = 0
        for i, pair in enumerate(self.pairs):
            self.pair_list.addItem(pair.get("name") or pair.get("source", ""))
            if pair["id"] == select_id:
                row = i
        self.pair_list.blockSignals(False)
        if self.pairs:
            self.pair_list.setCurrentRow(row)
            self._show_pair(row)

    def _current(self) -> Optional[Dict[str, Any]]:
        row = self.pair_list.currentRow()
        return self.pairs[row] if 0 <= row < len(self.pairs) else None

    def _show_pair(self, row: int) -> None:
        pair = self.pairs[row] if 0 <= row < len(self.pairs) else None
        if pair is None:
            return
        self.name.setText(pair.get("name", ""))
        self.source.setText(pair.get("source", ""))
        self.dest.setText(pair.get("dest", ""))
        idx = self.mode.findData(pair.get("mode", "additive"))
        self.mode.setCurrentIndex(max(0, idx))
        self.excludes.setText(pair.get("excludes", default_excludes_text()))
        self.auto.setChecked(bool(pair.get("auto", False)))
        self.interval.setValue(int(pair.get("interval", 60) or 60))
        self._update_auto_enabled()
        self._invalidate_plan()
        self._update_target_status(pair)

    def _update_auto_enabled(self, *args: Any) -> None:
        one_way = self.mode.currentData() in ("additive", "mirror")
        self.auto.setEnabled(one_way)
        self.interval.setEnabled(one_way)
        if not one_way:
            self.auto.setChecked(False)

    def _update_target_status(self, pair: Dict[str, Any]) -> None:
        dest = pair.get("dest", "")
        if not dest:
            text = ""
        elif not os.path.isdir(dest):
            text = self.tr("não encontrado (HD/NAS desconectado?)")
        else:
            marker = read_marker(dest)
            if marker is None:
                text = self.tr("conectado, ainda não preparado")
            elif pair.get("target_uuid") and marker["uuid"] != pair["target_uuid"]:
                text = self.tr("conectado, mas pertence a outro par")
            else:
                text = self.tr("conectado e reconhecido")
        self.target_status.setText(text)

    def _invalidate_plan(self, *args: Any) -> None:
        if self.plan is not None:
            self.summary.setText("")
            self.table.set_rows([], [])
        self.plan = None
        self.plan_pair_id = None
        self.btn_sync.setEnabled(False)

    # ------------------------------------------------------------------
    def new_pair(self) -> None:
        pair = {"id": str(uuid.uuid4()), "name": self.tr("Novo par"), "source": "", "dest": "", "mode": "additive",
                "excludes": default_excludes_text(), "target_uuid": ""}
        self.pairs.append(pair)
        self._store_pairs()
        self._refresh_list(pair["id"])

    def remove_pair(self) -> None:
        pair = self._current()
        if pair is None:
            return
        self.pairs = [p for p in self.pairs if p["id"] != pair["id"]]
        self._store_pairs()
        self._refresh_list()
        self._invalidate_plan()

    def save_pair(self) -> bool:
        """Grava o formulário no par atual; prepara o destino se necessário."""
        pair = self._current()
        if pair is None:
            return False
        source = self.source.text().strip()
        dest = self.dest.text().strip()
        if not source or not dest:
            self.host.notify(self.tr("Informe origem e destino."), "Warning")
            return False
        src_n = os.path.normcase(os.path.abspath(source)) + os.sep
        dst_n = os.path.normcase(os.path.abspath(dest)) + os.sep
        if src_n.startswith(dst_n) or dst_n.startswith(src_n):
            self.host.notify(self.tr("Origem e destino não podem estar uma dentro da outra."), "Critical")
            return False
        if pair.get("dest") and os.path.normcase(pair["dest"]) != os.path.normcase(dest):
            pair["target_uuid"] = ""
        pair.update(name=self.name.text().strip() or os.path.basename(source.rstrip("/\\")), source=source, dest=dest,
                    mode=self.mode.currentData(), excludes=self.excludes.text(),
                    auto=self.auto.isChecked() and self.mode.currentData() in ("additive", "mirror"),
                    interval=self.interval.value())
        if os.path.isdir(dest) and not pair.get("target_uuid"):
            marker = read_marker(dest)
            text = (self.tr("O destino já tem um marcador do BackupQgis. Usá-lo para este par?") if marker else
                    self.tr("Preparar o destino {0}?\nSerá criado o arquivo .backupqgis-target.json, usado para "
                            "reconhecer este disco nas próximas sincronizações.").format(dest))
            if QMessageBox.question(self, self.tr("Preparar destino"), text) == QMessageBox.StandardButton.Yes:
                try:
                    pair["target_uuid"] = init_marker(dest, pair["name"])
                except (OSError, SyncAborted) as exc:
                    self.host.notify(str(exc), "Critical")
        self._store_pairs()
        self._refresh_list(pair["id"])
        self.host.log(self.tr("Par salvo: {0} → {1} ({2}).").format(source, dest, pair["mode"]))
        return True

    # ------------------------------------------------------------------
    def simulate(self) -> None:
        if not self.save_pair():
            return
        pair = dict(self._current())
        if not pair.get("target_uuid"):
            self.host.notify(self.tr("Destino não preparado ou não conectado. Conecte o disco e salve o par."), "Warning")
            return
        excludes = parse_excludes(pair.get("excludes", ""))
        self.summary.setText("")
        self.table.set_rows([], [])

        def work(task: FunctionTask) -> Any:
            check_target(pair["dest"], pair["target_uuid"])
            if not os.path.isdir(pair["source"]):
                raise SyncAborted(f"Origem não encontrada: {pair['source']}")
            task.log(f"Lendo origem {pair['source']}...")
            src_tree = scan_tree(pair["source"], excludes, task.isCanceled)
            task.report(50, 100)
            task.log(f"Lendo destino {pair['dest']}...")
            dst_tree = scan_tree(pair["dest"], excludes, task.isCanceled)
            if pair["mode"] == "bidirectional":
                def same(rel: str) -> bool:
                    a = os.path.join(pair["source"], *rel.split("/"))
                    b = os.path.join(pair["dest"], *rel.split("/"))
                    return sha256_file(a, task.isCanceled) == sha256_file(b, task.isCanceled)
                return build_bidirectional_plan(pair["source"], pair["dest"], src_tree, dst_tree,
                                                load_state(sync_state_path(pair["id"])), same_content=same)
            return build_plan(pair["source"], pair["dest"], src_tree, dst_tree, pair["mode"])

        self._start(self.tr("Simulando sincronização..."), work,
                    lambda task: self._on_simulated(task, pair["id"]))

    def _on_simulated(self, task: FunctionTask, pair_id: str) -> None:
        if not self._finish(task):
            return
        plan = task.value
        if isinstance(plan, BiPlan):
            self._show_bidirectional(plan, pair_id)
            return
        rows, ids = [], []
        labels = ((self.tr("Copiar (novo)"), plan.copy_new, None),
                  (self.tr("Atualizar"), plan.update, None),
                  (self.tr("Mover para a lixeira"), plan.delete, self.host.icon_warn),
                  (self.tr("Ignorar (destino mais recente)"), plan.dest_newer, self.host.icon_info))
        for action, items, icon in labels:
            for rel in items:
                if len(rows) >= MAX_ROWS:
                    break
                size = ""
                if action != labels[2][0]:
                    path = os.path.join(plan.source, *rel.split("/"))
                    try:
                        size = human_size(os.path.getsize(path))
                    except OSError:
                        size = ""
                rows.append([text_item(action, icon), text_item(rel), text_item(size)])
                ids.append(rel)
        self.table.set_rows(rows, ids)
        for warning in plan.warnings:
            self.host.log(warning, "Warning")
        text = self.tr("{0} novos, {1} atualizados, {2} para a lixeira, {3} ignorados; {4} a copiar.").format(
            len(plan.copy_new), len(plan.update), len(plan.delete), len(plan.dest_newer), human_size(plan.bytes_to_copy))
        self.summary.setText(text)
        self.host.log(self.tr("Simulação: ") + text)
        self.plan, self.plan_pair_id = plan, pair_id
        self.btn_sync.setEnabled(not plan.is_empty)
        if plan.is_empty:
            self.host.notify(self.tr("Origem e destino já estão sincronizados."), "Success")

    def _show_bidirectional(self, plan: BiPlan, pair_id: str) -> None:
        rows, ids = [], []
        groups = ((self.tr("Origem → destino"), plan.a_to_b, None),
                  (self.tr("Destino → origem"), plan.b_to_a, None),
                  (self.tr("Lixeira da origem"), plan.delete_a, self.host.icon_warn),
                  (self.tr("Lixeira do destino"), plan.delete_b, self.host.icon_warn))
        for action, items, icon in groups:
            for rel in items[:MAX_ROWS]:
                rows.append([text_item(action, icon), text_item(rel), text_item("")])
                ids.append(rel)
        for rel, winner in plan.conflicts[:MAX_ROWS]:
            label = self.tr("Conflito (vence a origem)") if winner == "a" else self.tr("Conflito (vence o destino)")
            rows.append([text_item(label, self.host.icon_bad), text_item(rel), text_item("")])
            ids.append(rel)
        self.table.set_rows(rows, ids)
        text = self.tr("{0} para o destino, {1} para a origem, {2} para as lixeiras, {3} conflitos; {4} a copiar.").format(
            len(plan.a_to_b), len(plan.b_to_a), len(plan.delete_a) + len(plan.delete_b), len(plan.conflicts),
            human_size(plan.bytes_to_copy))
        if plan.first_run:
            text += " " + self.tr("Primeira execução: nada será apagado; arquivos de um lado só são copiados.")
        self.summary.setText(text)
        self.host.log(self.tr("Simulação bidirecional: ") + text)
        self.plan, self.plan_pair_id = plan, pair_id
        self.btn_sync.setEnabled(not plan.is_empty)
        if plan.is_empty:
            self.host.notify(self.tr("Origem e destino já estão sincronizados."), "Success")

    def _synchronize_bidirectional(self, pair: Dict[str, Any], plan: BiPlan) -> None:
        force = False
        try:
            guard_bidirectional(plan)
        except SyncAborted as exc:
            if plan.a_count == 0 or plan.b_count == 0:
                self.host.notify(str(exc), "Critical")
                return
            answer = QMessageBox.warning(
                self, self.tr("Muitos arquivos serão removidos"),
                str(exc) + "\n\n" + self.tr("Os arquivos irão para a lixeira de cada lado. Continuar mesmo assim?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            force = True
        target_uuid = pair.get("target_uuid", "")
        excludes = parse_excludes(pair.get("excludes", ""))
        state_path = sync_state_path(pair["id"])

        def work(task: FunctionTask) -> Any:
            check_target(plan.b, target_uuid)
            guard_bidirectional(plan, force=force)
            stats = execute_bidirectional(plan, task.log, task.isCanceled, task.report)
            save_state(state_path, build_state(scan_tree(plan.a, excludes), scan_tree(plan.b, excludes)))
            return {"copied": stats["a_to_b"] + stats["b_to_a"], "updated": stats["conflicts"],
                    "trashed": stats["trashed"], "failed": stats["failed"]}

        self._start(self.tr("Sincronizando nos dois sentidos..."), work, self._on_synced)

    def synchronize(self) -> None:
        pair = self._current()
        plan = self.plan
        if plan is None or pair is None or pair["id"] != self.plan_pair_id:
            self.host.notify(self.tr("Simule antes de sincronizar."), "Warning")
            return
        if isinstance(plan, BiPlan):
            self._synchronize_bidirectional(pair, plan)
            return
        force = False
        try:
            guard_plan(plan)
        except SyncAborted as exc:
            if plan.source_count == 0:
                self.host.notify(str(exc), "Critical")
                return
            answer = QMessageBox.warning(
                self, self.tr("Muitos arquivos serão removidos"),
                str(exc) + "\n\n" + self.tr("Os arquivos irão para a lixeira do destino. Continuar mesmo assim?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            force = True
        target_uuid = pair.get("target_uuid", "")

        def work(task: FunctionTask) -> Any:
            check_target(plan.dest, target_uuid)  # o disco pode ter sido trocado após a simulação
            guard_plan(plan, force=force)
            return execute_plan(plan, task.log, task.isCanceled, task.report)

        self._start(self.tr("Sincronizando..."), work, self._on_synced)

    def _on_synced(self, task: FunctionTask) -> None:
        self._invalidate_plan()
        if not self._finish(task):
            return
        stats = task.value
        text = self.tr("Sincronização concluída: {0} copiados, {1} atualizados, {2} na lixeira, {3} falhas.").format(
            stats["copied"], stats["updated"], stats["trashed"], stats["failed"])
        self.host.log(text, "Warning" if stats["failed"] else "Success")
        self.host.notify(text, "Warning" if stats["failed"] else "Success")
        self.summary.setText(text)
        self.table.set_rows([], [])

    # ------------------------------------------------------------------
    def _start(self, label: str, work: Any, on_done: Any) -> None:
        if self.host._busy:
            return
        self.host._set_busy(True, label, cancellable=True)
        task = FunctionTask(f"BackupQgis: {label}", work, on_done)
        task.log_message.connect(self.host.log)
        task.progressChanged.connect(lambda p: self.host._set_progress(int(p), 100))
        self.host._keep_task(task)

    def _finish(self, task: FunctionTask) -> bool:
        """Encerra o estado ocupado; retorna ``True`` se a tarefa terminou com resultado."""
        self.host._drop_task(task)
        self.host._set_busy(False)
        if task.isCanceled():
            self.host.notify(self.tr("Operação cancelada."), "Warning")
            return False
        if task.error:
            self.host.log(task.error, "Critical")
            self.host.notify(task.error, "Critical", 0)
            return False
        return task.value is not None
