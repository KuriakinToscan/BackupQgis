# -*- coding: utf-8 -*-
"""
Página "Projetos": inventário e backup de projetos QGIS com os dados vinculados.
"""

import json
import os
from typing import Any, List, Optional, Tuple

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                                 QMessageBox, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core.compat import enum_value
from ..core.project_inventory import find_projects, inventory_project, summarize
from ..core.project_mirror import (backup_projects, is_mirror, list_states, read_mirror_manifest,
                                   restore_mirror)
from ..core.project_package import build_package, read_manifest, restore_snapshot
from ..core.report import RunReport, human_size
from ..tasks.function_task import FunctionTask
from .common import PROJECTS_GROUP, as_bool, as_int, default_projects_dir, ensure_dir, projects_conflict
from .widgets import CheckTable, text_item

SETTINGS_KEY = PROJECTS_GROUP
MAX_ROWS = 5000
ROLE_LABELS = {"projeto": "Projeto", "dados": "Dados", "auxiliar": "Auxiliar", "recurso": "Recurso"}


__all__ = ["ProjectsPage", "human_size"]


class ProjectsPage(QWidget):
    """Lista de projetos, destino, inventário e backup em ``<projeto>_bkp``."""

    def __init__(self, host: Any) -> None:
        """:param host: diálogo principal (log, mensagens, progresso, tarefas)."""
        super().__init__(host)
        self.host = host
        layout = QVBoxLayout(self)

        intro = QLabel(self.tr("Cada projeto (.qgz/.qgs) é copiado com os dados que usa para <destino>/<projeto>_bkp, "
                               "com a mesma estrutura de pastas da origem; dados de fora da pasta do projeto vão "
                               "para _externos. A cada backup só o que mudou é copiado e as versões anteriores "
                               "ficam em _historico, para restaurar qualquer data. Camadas de banco e serviços são "
                               "apenas listadas."), self)
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
        self.dest.setPlaceholderText(self.tr("Pasta de destino (ex.: E:/BackupQgis/projetos ou //nas/backup/qgis/projetos)"))
        self.dest.setToolTip(self.tr("Pasta própria dos backups de projetos, separada da pasta das configurações."))
        browse = QToolButton(self)
        browse.setText("...")
        browse.clicked.connect(self._browse_dest)
        self.keep = QSpinBox(self)
        self.keep.setRange(0, 999)
        self.keep.setValue(10)
        self.keep.setSpecialValueText(self.tr("todos"))
        self.keep.setToolTip(self.tr("Datas mantidas no histórico de cada projeto (0 = todas). O histórico que só "
                                     "servia às datas apagadas é removido."))
        dest_row.addWidget(QLabel(self.tr("Destino:"), self))
        dest_row.addWidget(self.dest, 1)
        dest_row.addWidget(browse)
        dest_row.addWidget(QLabel(self.tr("Manter:"), self))
        dest_row.addWidget(self.keep)
        layout.addLayout(dest_row)

        extra_row = QHBoxLayout()
        self.chk_version = QCheckBox(self.tr("Guardar versão do projeto a cada salvamento"), self)
        self.chk_version.setToolTip(self.tr("Ao salvar qualquer projeto no QGIS, copia o .qgz/.qgs (sem os dados) "
                                            "para <destino>/<nome>_bkp/_versoes/ com data e hora."))
        self.version_keep = QSpinBox(self)
        self.version_keep.setRange(0, 999)
        self.version_keep.setValue(20)
        self.version_keep.setSpecialValueText(self.tr("todas"))
        self.version_keep.setToolTip(self.tr("Versões mantidas por projeto (0 = todas)"))
        self.btn_package = QPushButton(self.tr("Empacotar..."), self)
        self.btn_package.setToolTip(self.tr("Copia o projeto e os dados para uma pasta (ou ZIP) com caminhos "
                                            "relativos, que abre em qualquer computador."))
        self.btn_package.clicked.connect(self.run_package)
        self.btn_restore = QPushButton(self.tr("Restaurar..."), self)
        self.btn_restore.setToolTip(self.tr("Restaura uma pasta <projeto>_bkp (atual ou de uma data) ou um snapshot "
                                            "do formato anterior."))
        self.btn_restore.clicked.connect(lambda: self.run_restore())
        self.chk_schedule = QCheckBox(self.tr("Backup automático a cada"), self)
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
        self.dest.editingFinished.connect(self._on_dest_edited)
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
        self.dest.setText(str(settings.value(f"{SETTINGS_KEY}/dest", "") or "") or default_projects_dir())
        self._dest_ok = self.dest.text()
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

    def _accept_dest(self, folder: str) -> bool:
        """Aceita a pasta de destino se não conflitar com a do backup de configurações."""
        problem = projects_conflict(folder)
        if problem:
            self.host.notify(problem, "Warning", 0)
            self.dest.setText(self._dest_ok)
            return False
        self._dest_ok = folder
        self._save_settings()
        return True

    def _on_dest_edited(self) -> None:
        self._accept_dest(self.dest.text().strip())

    def _browse_dest(self) -> None:
        start = self.dest.text() if os.path.isdir(self.dest.text()) else os.path.dirname(self.dest.text())
        folder = QFileDialog.getExistingDirectory(self, self.tr("Pasta de destino"), start)
        if folder:
            self.dest.setText(folder)
            self._accept_dest(folder)

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
        if not self._accept_dest(dest):
            return
        if not ensure_dir(dest):
            self.host.notify(self.tr("Pasta de destino não encontrada: {0} (HD/NAS desconectado?)").format(dest),
                             "Critical", 0)
            return
        keep = self.keep.value()
        source = dict(self.host._source_info(), trigger="manual")
        title = self.tr("Backup de {0} projeto(s) em {1}").format(len(paths), dest)

        def work(task: FunctionTask) -> Any:
            return backup_projects(paths, dest, source, keep, task.log, task.isCanceled, task.report, title)

        self._start(self.tr("Copiando projetos e dados..."), work, self._on_backup_done)

    def _on_backup_done(self, task: FunctionTask) -> None:
        if not self._finish(task, report_cancel=False):
            return
        report, inventories = task.value
        history = getattr(self.host, "history_page", None)
        if history is not None:
            history.refresh()
        if inventories:
            self._fill_table(inventories)
        self.host.log_report(report)

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
        report = RunReport(self.tr("Empacotamento de {0} projeto(s)").format(len(task.value)))
        for path, out, pkg, inv in task.value:
            name = os.path.basename(path)
            if pkg.unresolved:
                report.warn(self.tr("{0}: pacote").format(name),
                            self.tr("{0} ({1} fontes reescritas, {2} não reescritas)").format(
                                out, pkg.rewritten, len(pkg.unresolved)), list(pkg.unresolved))
            else:
                report.ok(self.tr("{0}: pacote").format(name),
                          self.tr("{0} ({1} fontes reescritas)").format(out, pkg.rewritten))
            if inv.missing:
                report.warn(self.tr("{0}: itens ausentes").format(name), str(len(inv.missing)),
                            [f"{n}: {p}" for n, p in inv.missing])
            if inv.memory:
                report.warn(self.tr("{0}: camadas em memória (não incluídas)").format(name), str(len(inv.memory)),
                            list(inv.memory))
        self.host.log_report(report)

    def _choose_state(self, bkp: str) -> Optional[str]:
        """Pergunta qual data restaurar de uma pasta ``_bkp``; ``""`` = mais recente, ``None`` = cancelado."""
        states = list(reversed(list_states(bkp)))
        if not states:
            return ""
        labels = []
        for st in states:
            ch = st.get("changes", {})
            labels.append(self.tr("{0} — {1} arquivos ({2} novos, {3} alterados, {4} removidos)").format(
                st["stamp"], len(st.get("files", [])), ch.get("copied", 0), ch.get("updated", 0),
                ch.get("removed", 0)))
        label, ok = QInputDialog.getItem(self, self.tr("Restaurar"), self.tr("Data a restaurar:"), labels, 0, False)
        if not ok:
            return None
        return states[labels.index(label)]["stamp"]

    def run_restore(self, path: str = "", stamp: Optional[str] = None) -> None:
        """
        Restaura uma pasta ``<projeto>_bkp`` (estado de ``stamp``; pergunta a
        data se não informada) ou um snapshot do formato anterior, para os
        caminhos originais ou para uma pasta nova.
        """
        path = path or QFileDialog.getExistingDirectory(self, self.tr("Pasta <projeto>_bkp ou snapshot a restaurar"),
                                                        self.dest.text())
        if not path:
            return
        mirror = is_mirror(path)
        if mirror:
            if stamp is None:
                stamp = self._choose_state(path)
                if stamp is None:
                    return
            stamp = stamp or None
            state = next((s for s in list_states(path) if s["stamp"] == stamp), None) if stamp else None
            info = state or read_mirror_manifest(path)
            if not info:
                self.host.notify(self.tr("{0} ainda não tem backup concluído.").format(path), "Warning")
                return
            text = self.tr("Projeto {0}, backup de {1}, com {2} arquivos.\n\nRestaurar para:").format(
                os.path.basename(info.get("project", "?")), info.get("stamp", "?"), len(info.get("files", [])))
        else:
            try:
                manifest = read_manifest(path)
            except Exception as exc:  # PackageAborted: pasta errada escolhida
                self.host.notify(str(exc), "Warning")
                return
            text = self.tr("Snapshot (formato anterior) de {0} com {1} arquivos e {2} projeto(s).\n\n"
                           "Restaurar para:").format(manifest.get("created_at", "?"), len(manifest.get("files", [])),
                                                     len(manifest.get("projects", [])))
        mode = self._ask(self.tr("Restaurar"), text,
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
        title = self.tr("Restauração de {0}{1} para {2}").format(
            os.path.basename(path), f" ({stamp})" if mirror and stamp else "",
            target or self.tr("os caminhos originais"))

        def work(task: FunctionTask) -> Any:
            if mirror:
                return restore_mirror(path, stamp, target, overwrite, task.log, task.isCanceled, task.report)
            return restore_snapshot(path, target, overwrite, task.log, task.isCanceled, task.report)

        self._restore_title = title
        self._start(self.tr("Restaurando..."), work, self._on_restore_done)

    def _on_restore_done(self, task: FunctionTask) -> None:
        if not self._finish(task):
            return
        stats = task.value
        report = RunReport(getattr(self, "_restore_title", self.tr("Restauração")))
        report.ok(self.tr("Arquivos restaurados"), str(stats["restored"]))
        if stats["skipped"]:
            report.ok(self.tr("Arquivos já idênticos (mantidos)"), str(stats["skipped"]))
        if stats["kept"]:
            report.warn(self.tr("Arquivos existentes e diferentes, não substituídos"), str(stats["kept"]))
        if stats["projects"]:
            report.ok(self.tr("Projetos com caminhos reescritos"), str(stats["projects"]))
        if stats["failed"]:
            report.error(self.tr("Falhas"), str(stats["failed"]), list(stats.get("errors", [])))
        self.host.log_report(report)

    # ------------------------------------------------------------------
    def _start(self, label: str, work: Any, on_done: Any) -> None:
        if self.host._busy:
            return
        self.host._set_busy(True, label, cancellable=True)
        task = FunctionTask(f"BackupQgis: {label}", work, on_done)
        task.log_message.connect(self.host.log)
        task.progressChanged.connect(lambda p: self.host._set_progress(int(p), 100))
        self.host._keep_task(task)

    def _finish(self, task: FunctionTask, report_cancel: bool = True) -> bool:
        """
        Encerra o estado ocupado; retorna ``True`` se a tarefa terminou com resultado.

        :param report_cancel: avisar o cancelamento (``False`` quando a própria
            tarefa devolve um registro que já o inclui).
        """
        self.host._drop_task(task)
        self.host._set_busy(False)
        if task.isCanceled() and (report_cancel or task.value is None):
            self.host.log(self.tr("Operação cancelada."), "Warning")
            self.host.notify(self.tr("Operação cancelada."), "Warning")
            return False
        if task.error:
            self.host.log(task.error, "Critical")
            self.host.notify(task.error, "Critical", 0)
            return False
        return task.value is not None
