# -*- coding: utf-8 -*-
"""
Página "Projetos": inventário e backup de projetos QGIS com os dados vinculados.
"""

import json
import os
from typing import Any, List, Optional, Tuple

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                                 QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core.compat import enum_value
from ..core.project_backup import apply_retention, run_project_backup
from ..core.project_inventory import find_projects, inventory_project, summarize
from ..core.project_package import build_package, read_manifest, restore_snapshot
from ..tasks.function_task import FunctionTask
from .common import PROJECTS_GROUP, as_bool, as_int
from .widgets import CheckTable, text_item

SETTINGS_KEY = PROJECTS_GROUP
MAX_ROWS = 5000
ROLE_LABELS = {"projeto": "Projeto", "dados": "Dados", "auxiliar": "Auxiliar", "recurso": "Recurso"}


def human_size(n: float) -> str:
    """Tamanho em unidades binárias (KiB, MiB, GiB)."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TiB"


class ProjectsPage(QWidget):
    """Lista de projetos, destino, inventário e backup em snapshots."""

    def __init__(self, host: Any) -> None:
        """:param host: diálogo principal (log, mensagens, progresso, tarefas)."""
        super().__init__(host)
        self.host = host
        layout = QVBoxLayout(self)

        intro = QLabel(self.tr("Copia projetos (.qgz/.qgs) e os arquivos de dados que eles usam para um snapshot "
                               "datado, com manifesto SHA-256. Camadas de banco e serviços são apenas listadas."), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.projects = QListWidget(self)
        self.projects.setMaximumHeight(110)
        buttons = QVBoxLayout()
        for label, slot in ((self.tr("Projeto atual"), self.add_current),
                            (self.tr("Adicionar..."), self.add_files),
                            (self.tr("Procurar em pasta..."), self.add_folder),
                            (self.tr("Remover"), self.remove_selected)):
            btn = QPushButton(label, self)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch()
        row.addWidget(self.projects, 1)
        row.addLayout(buttons)
        layout.addLayout(row)

        dest_row = QHBoxLayout()
        self.dest = QLineEdit(self)
        self.dest.setPlaceholderText(self.tr("Pasta de destino (ex.: E:/Backups/QGIS ou //nas/backup/qgis)"))
        browse = QToolButton(self)
        browse.setText("...")
        browse.clicked.connect(self._browse_dest)
        self.keep = QSpinBox(self)
        self.keep.setRange(0, 999)
        self.keep.setValue(10)
        self.keep.setSpecialValueText(self.tr("todos"))
        self.keep.setToolTip(self.tr("Quantidade de snapshots mantidos no destino (0 = não apagar nenhum)"))
        dest_row.addWidget(QLabel(self.tr("Destino:"), self))
        dest_row.addWidget(self.dest, 1)
        dest_row.addWidget(browse)
        dest_row.addWidget(QLabel(self.tr("Manter:"), self))
        dest_row.addWidget(self.keep)
        layout.addLayout(dest_row)

        extra_row = QHBoxLayout()
        self.chk_version = QCheckBox(self.tr("Guardar versão do projeto a cada salvamento"), self)
        self.chk_version.setToolTip(self.tr("Ao salvar qualquer projeto no QGIS, copia o .qgz/.qgs (sem os dados) "
                                            "para <destino>/versoes/<nome>/ com data e hora."))
        self.version_keep = QSpinBox(self)
        self.version_keep.setRange(0, 999)
        self.version_keep.setValue(20)
        self.version_keep.setSpecialValueText(self.tr("todas"))
        self.version_keep.setToolTip(self.tr("Versões mantidas por projeto (0 = todas)"))
        self.btn_package = QPushButton(self.tr("Empacotar..."), self)
        self.btn_package.setToolTip(self.tr("Copia o projeto e os dados para uma pasta (ou ZIP) com caminhos "
                                            "relativos, que abre em qualquer computador."))
        self.btn_package.clicked.connect(self.run_package)
        self.btn_restore = QPushButton(self.tr("Restaurar snapshot..."), self)
        self.btn_restore.clicked.connect(lambda: self.run_restore())
        self.chk_schedule = QCheckBox(self.tr("Snapshot automático a cada"), self)
        self.chk_schedule.setToolTip(self.tr("Faz o backup da lista de projetos para o destino em segundo plano "
                                             "enquanto o QGIS estiver aberto. Independe do backup de configurações."))
        self.schedule_hours = QSpinBox(self)
        self.schedule_hours.setRange(1, 720)
        self.schedule_hours.setValue(24)
        self.schedule_hours.setSuffix(self.tr(" h"))
        extra_row.addWidget(self.chk_version)
        extra_row.addWidget(self.version_keep)
        extra_row.addSpacing(12)
        extra_row.addWidget(self.chk_schedule)
        extra_row.addWidget(self.schedule_hours)
        extra_row.addStretch()
        extra_row.addWidget(self.btn_package)
        extra_row.addWidget(self.btn_restore)
        layout.addLayout(extra_row)

        self.table = CheckTable([self.tr("Projeto"), self.tr("Camada/Item"), self.tr("Tipo"), self.tr("Caminho"),
                                 self.tr("Tamanho")], 3, 0, self)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.btn_inventory = QPushButton(self.tr("Inventariar"), self)
        self.btn_inventory.clicked.connect(self.run_inventory)
        self.btn_backup = QPushButton(self.tr("Fazer backup"), self)
        self.btn_backup.clicked.connect(self.run_backup)
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.btn_inventory)
        bottom.addWidget(self.btn_backup)
        layout.addLayout(bottom)

        self._load_settings()
        self.projects.model().rowsInserted.connect(self._save_settings)
        self.projects.model().rowsRemoved.connect(self._save_settings)
        self.dest.editingFinished.connect(self._save_settings)
        self.keep.valueChanged.connect(self._save_settings)
        self.chk_version.toggled.connect(self._save_settings)
        self.version_keep.valueChanged.connect(self._save_settings)
        self.chk_schedule.toggled.connect(self._save_settings)
        self.schedule_hours.valueChanged.connect(self._save_settings)

    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        settings = QSettings()
        try:
            paths = json.loads(str(settings.value(f"{SETTINGS_KEY}/list", "[]")))
        except ValueError:
            paths = []
        for path in paths:
            self.projects.addItem(path)
        self.dest.setText(str(settings.value(f"{SETTINGS_KEY}/dest", "")))
        self.keep.setValue(as_int(settings.value(f"{SETTINGS_KEY}/keep", 10), 10))
        self.version_keep.setValue(as_int(settings.value(f"{SETTINGS_KEY}/versionKeep", 20), 20))
        self.chk_version.setChecked(as_bool(settings.value(f"{SETTINGS_KEY}/versionOnSave", False)))
        self.chk_schedule.setChecked(as_bool(settings.value(f"{SETTINGS_KEY}/autoSnapshot", False)))
        self.schedule_hours.setValue(as_int(settings.value(f"{SETTINGS_KEY}/autoSnapshotHours", 24), 24))

    def _save_settings(self, *args: Any) -> None:
        settings = QSettings()
        settings.setValue(f"{SETTINGS_KEY}/list", json.dumps(self.project_paths(), ensure_ascii=False))
        settings.setValue(f"{SETTINGS_KEY}/dest", self.dest.text().strip())
        settings.setValue(f"{SETTINGS_KEY}/keep", self.keep.value())
        settings.setValue(f"{SETTINGS_KEY}/versionOnSave", self.chk_version.isChecked())
        settings.setValue(f"{SETTINGS_KEY}/versionKeep", self.version_keep.value())
        settings.setValue(f"{SETTINGS_KEY}/autoSnapshot", self.chk_schedule.isChecked())
        settings.setValue(f"{SETTINGS_KEY}/autoSnapshotHours", self.schedule_hours.value())

    def project_paths(self) -> List[str]:
        return [self.projects.item(i).text() for i in range(self.projects.count())]

    def _add_paths(self, paths: List[str]) -> None:
        existing = {os.path.normcase(p) for p in self.project_paths()}
        for path in paths:
            if os.path.normcase(path) not in existing:
                existing.add(os.path.normcase(path))
                self.projects.addItem(path)

    def add_current(self) -> None:
        project = QgsProject.instance()
        path = project.fileName()
        if not path or not path.lower().endswith((".qgz", ".qgs")):
            self.host.notify(self.tr("O projeto atual ainda não foi salvo em arquivo .qgz/.qgs."), "Warning")
            return
        if project.isDirty():
            self.host.notify(self.tr("O projeto atual tem alterações não salvas; o backup usa a versão gravada em disco."),
                             "Warning")
        self._add_paths([os.path.abspath(path)])

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Adicionar projetos"), "",
                                                self.tr("Projetos QGIS (*.qgz *.qgs)"))
        self._add_paths([os.path.abspath(p) for p in paths])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Procurar projetos em"))
        if not folder:
            return
        found = find_projects(folder)
        self.host.log(self.tr("{0} projetos encontrados em {1}.").format(len(found), folder))
        self._add_paths(found)

    def remove_selected(self) -> None:
        for item in self.projects.selectedItems():
            self.projects.takeItem(self.projects.row(item))

    def _browse_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Pasta de destino"), self.dest.text())
        if folder:
            self.dest.setText(folder)
            self._save_settings()

    # ------------------------------------------------------------------
    def _fill_table(self, inventories: List[Any]) -> None:
        rows = []
        for inv in inventories:
            pname = os.path.basename(inv.project_path)
            for f in inv.files:
                rows.append([text_item(pname, tooltip=inv.project_path), text_item(f.layer),
                             text_item(self.tr(ROLE_LABELS.get(f.role, f.role))),
                             text_item(f.path, tooltip=f.path), text_item(human_size(f.size))])
            for name, provider, uri in inv.remote:
                rows.append([text_item(pname), text_item(name), text_item(self.tr("Remota ({0})").format(provider),
                                                                          self.host.icon_info),
                             text_item(uri, tooltip=uri), text_item("")])
            for name in inv.memory:
                rows.append([text_item(pname), text_item(name), text_item(self.tr("Em memória"), self.host.icon_warn),
                             text_item(self.tr("perdida ao fechar o QGIS")), text_item("")])
            for name, path in inv.missing:
                rows.append([text_item(pname), text_item(name), text_item(self.tr("Ausente"), self.host.icon_bad),
                             text_item(path, tooltip=path), text_item("")])
            for warning in inv.warnings:
                self.host.log(f"{pname}: {warning}", "Warning")
        if len(rows) > MAX_ROWS:
            self.host.log(self.tr("Tabela limitada a {0} linhas de {1}.").format(MAX_ROWS, len(rows)))
            rows = rows[:MAX_ROWS]
        self.table.set_rows(rows, range(len(rows)))
        totals = summarize(inventories)
        self.summary.setText(self.tr("{0} arquivos ({1}); {2} camadas remotas; {3} itens ausentes.").format(
            totals["files"], human_size(totals["bytes"]), totals["remote"], totals["missing"]))

    def run_inventory(self) -> None:
        paths = self.project_paths()
        if not paths:
            self.host.notify(self.tr("Adicione ao menos um projeto."), "Warning")
            return

        def work(task: FunctionTask) -> List[Any]:
            result = []
            for i, path in enumerate(paths):
                if task.isCanceled():
                    break
                result.append(inventory_project(path))
                task.report(i + 1, len(paths))
            return result

        self._start(self.tr("Inventariando projetos..."), work, self._on_inventory_done)

    def _on_inventory_done(self, task: FunctionTask) -> None:
        if self._finish(task):
            self._fill_table(task.value)

    def run_backup(self) -> None:
        paths = self.project_paths()
        dest = self.dest.text().strip()
        if not paths:
            self.host.notify(self.tr("Adicione ao menos um projeto."), "Warning")
            return
        if not dest:
            self.host.notify(self.tr("Escolha a pasta de destino."), "Warning")
            return
        self._save_settings()
        keep = self.keep.value()
        source = self.host._source_info()

        def work(task: FunctionTask) -> Any:
            inventories = [inventory_project(p) for p in paths]
            snap, manifest = run_project_backup(inventories, dest, source, task.log, task.isCanceled, task.report)
            removed = apply_retention(dest, keep, task.log)
            return inventories, snap, manifest, removed

        self._start(self.tr("Copiando projetos e dados..."), work, self._on_backup_done)

    def _on_backup_done(self, task: FunctionTask) -> None:
        if not self._finish(task):
            return
        inventories, snap, manifest, removed = task.value
        history = getattr(self.host, "history_page", None)
        if history is not None:
            history.refresh()
        self._fill_table(inventories)
        n_missing = sum(len(inv.missing) for inv in inventories)
        msg = self.tr("Snapshot {0}: {1} arquivos, {2}.").format(
            snap, len(manifest["files"]), human_size(manifest["total_bytes"]))
        self.host.log(msg, "Success")
        for err in manifest["errors"]:
            self.host.log(err, "Warning")
        if manifest["errors"] or n_missing:
            self.host.notify(msg + " " + self.tr("{0} falhas de cópia e {1} itens ausentes; veja o log.").format(
                len(manifest["errors"]), n_missing), "Warning", 0)
        else:
            self.host.notify(msg, "Success")
        if removed:
            self.host.log(self.tr("{0} snapshots antigos removidos.").format(len(removed)))

    # ------------------------------------------------------------------
    def _ask(self, title: str, text: str, buttons: List[Tuple[str, str]]) -> Optional[str]:
        """Pergunta com botões nomeados; retorna a chave do botão escolhido ou ``None`` se cancelado."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        mapping = {}
        for key, label in buttons:
            mapping[box.addButton(label, enum_value(QMessageBox, "ButtonRole", "AcceptRole"))] = key
        box.addButton(enum_value(QMessageBox, "StandardButton", "Cancel"))
        box.exec()
        return mapping.get(box.clickedButton())

    def run_package(self) -> None:
        """Empacota os projetos selecionados na lista (ou todos, se nenhum estiver selecionado)."""
        selected = [i.text() for i in self.projects.selectedItems()] or self.project_paths()
        if not selected:
            self.host.notify(self.tr("Adicione ao menos um projeto."), "Warning")
            return
        out_dir = QFileDialog.getExistingDirectory(self, self.tr("Pasta onde o pacote será criado"))
        if not out_dir:
            return
        fmt = self._ask(self.tr("Empacotar projeto"),
                        self.tr("{0} projeto(s) serão copiados com os dados para {1}. Gerar:").format(
                            len(selected), out_dir),
                        [("folder", self.tr("Pasta")), ("zip", self.tr("Arquivo ZIP"))])
        if fmt is None:
            return

        def work(task: FunctionTask) -> List[Any]:
            results = []
            for i, path in enumerate(selected):
                if task.isCanceled():
                    break
                inv = inventory_project(path)
                out, report = build_package(inv, out_dir, fmt == "zip", task.log, task.isCanceled,
                                            lambda d, t, i=i: task.report(i * 1000 + (1000 * d // max(t, 1)),
                                                                          len(selected) * 1000))
                results.append((path, out, report, inv))
            return results

        self._start(self.tr("Empacotando projetos..."), work, self._on_package_done)

    def _on_package_done(self, task: FunctionTask) -> None:
        if not self._finish(task):
            return
        problems = 0
        for path, out, report, inv in task.value:
            self.host.log(self.tr("Pacote de {0}: {1} ({2} fontes reescritas).").format(
                os.path.basename(path), out, report.rewritten), "Success")
            for item in report.unresolved:
                self.host.log(self.tr("Fonte não reescrita: {0}").format(item), "Warning")
            problems += len(report.unresolved) + len(inv.missing) + len(inv.memory)
        msg = self.tr("{0} pacote(s) criados.").format(len(task.value))
        if problems:
            self.host.notify(msg + " " + self.tr("{0} itens não incluídos ou não reescritos; veja o log.").format(
                problems), "Warning", 0)
        else:
            self.host.notify(msg, "Success")

    def run_restore(self, snapshot: str = "") -> None:
        """Restaura um snapshot (pergunta qual, se não informado) para os caminhos originais ou uma pasta nova."""
        snapshot = snapshot or QFileDialog.getExistingDirectory(self, self.tr("Snapshot a restaurar"),
                                                                self.dest.text())
        if not snapshot:
            return
        try:
            manifest = read_manifest(snapshot)
        except Exception as exc:  # PackageAborted: pasta errada escolhida
            self.host.notify(str(exc), "Warning")
            return
        n_files = len(manifest.get("files", []))
        mode = self._ask(
            self.tr("Restaurar snapshot"),
            self.tr("Snapshot de {0} com {1} arquivos e {2} projeto(s).\n\nRestaurar para:").format(
                manifest.get("created_at", "?"), n_files, len(manifest.get("projects", []))),
            [("new", self.tr("Pasta nova (recomendado)")), ("original", self.tr("Caminhos originais"))])
        if mode is None:
            return
        target: Optional[str] = None
        overwrite = False
        if mode == "new":
            target = QFileDialog.getExistingDirectory(self, self.tr("Pasta de destino da restauração"))
            if not target:
                return
        else:
            choice = self._ask(
                self.tr("Arquivos existentes"),
                self.tr("Arquivos idênticos são mantidos. O que fazer com arquivos que existem e são diferentes?\n"
                        "Ao substituir, a versão atual é renomeada para *.antes-restauracao-<data>."),
                [("keep", self.tr("Manter os atuais")), ("overwrite", self.tr("Substituir"))])
            if choice is None:
                return
            overwrite = choice == "overwrite"

        def work(task: FunctionTask) -> Any:
            return restore_snapshot(snapshot, target, overwrite, task.log, task.isCanceled, task.report)

        self._start(self.tr("Restaurando snapshot..."), work, self._on_restore_done)

    def _on_restore_done(self, task: FunctionTask) -> None:
        if not self._finish(task):
            return
        stats = task.value
        msg = self.tr("Restauração: {0} arquivos restaurados, {1} já idênticos, {2} mantidos, {3} falhas; "
                      "{4} projeto(s) reescritos.").format(stats["restored"], stats["skipped"], stats["kept"],
                                                         stats["failed"], stats["projects"])
        self.host.log(msg, "Success" if not stats["failed"] else "Warning")
        self.host.notify(msg, "Warning" if stats["failed"] or stats["kept"] else "Success",
                         0 if stats["failed"] else 8)

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
