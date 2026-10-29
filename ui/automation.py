# -*- coding: utf-8 -*-
"""
Automações do BackupQgis, ativas enquanto o plugin estiver carregado.

São duas, independentes (cada uma tem preferências, temporizador e tarefas
próprios; a falha de uma não afeta a outra):

- :class:`ConfigAutomation` (backup das configurações do QGIS): exportação de
  plugins, configurações e conexões, sem senhas, ao fechar o QGIS e/ou a cada
  N dias;
- :class:`ProjectsAutomation` (backup de projetos e dados): versão do arquivo
  de projeto a cada salvamento, snapshot agendado da lista de projetos e
  sincronização periódica dos pares aditivo/espelho.

Falhas são registradas no painel de mensagens (aba BackupQgis); só problemas
que exigem ação aparecem na barra de mensagens do QGIS.
"""

import json
import time
from typing import Any, Callable, Dict, List

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import QObject, QSettings, QTimer

from ..core.auto_export import export_all
from ..core.compat import enum_value
from ..core.project_backup import apply_retention, run_project_backup, version_project_file
from ..core.project_inventory import inventory_project
from ..core.sync import SyncAborted, parse_excludes, run_one_way
from ..tasks.function_task import FunctionTask
from .common import CONFIG_GROUP, PROJECTS_GROUP, SYNC_PAIRS_KEY, add_config_history, as_bool, as_int, source_info

LOG_TAG = "BackupQgis"
TICK_MS = 60 * 1000


def _log(message: str, level: str = "Info") -> None:
    QgsMessageLog.logMessage(message, LOG_TAG, enum_value(Qgis, "MessageLevel", level))


class _Base(QObject):
    """Temporizador, tarefas em segundo plano e avisos comuns às duas automações."""

    def __init__(self, iface: Any) -> None:
        super().__init__(iface.mainWindow())
        self.iface = iface
        self._tasks: List[FunctionTask] = []
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self) -> None:  # pragma: no cover - sobrescrito
        pass

    def _disconnect(self) -> None:  # pragma: no cover - sobrescrito
        pass

    def stop(self) -> None:
        """Desconecta sinais, para o temporizador e cancela tarefas (no unload do plugin)."""
        self.timer.stop()
        self._disconnect()
        for task in list(self._tasks):
            try:
                task.cancel()
            except RuntimeError:
                pass
        self._tasks.clear()

    def _start(self, description: str, work: Callable[[FunctionTask], Any],
               on_done: Callable[[FunctionTask], None]) -> None:
        task = FunctionTask(description, work, lambda t: self._done(t, on_done))
        task.log_message.connect(_log)
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _done(self, task: FunctionTask, on_done: Callable[[FunctionTask], None]) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        on_done(task)

    def _warn(self, text: str) -> None:
        _log(text, "Warning")
        try:
            self.iface.messageBar().pushMessage("BackupQgis", text, enum_value(Qgis, "MessageLevel", "Warning"), 10)
        except Exception:
            pass

    @staticmethod
    def _disconnected(error: str) -> bool:
        """Destino desconectado é situação normal (notebook fora da rede): só registra."""
        return "conectado" in error or "não encontrad" in error.lower()


class ConfigAutomation(_Base):
    """Exportação automática das configurações do QGIS (sem senhas)."""

    def __init__(self, iface: Any, version: str) -> None:
        super().__init__(iface)
        self.version = version
        self._running = False
        app = QgsApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_quit)

    def _disconnect(self) -> None:
        app = QgsApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(self._on_quit)
            except (TypeError, RuntimeError):
                pass

    @staticmethod
    def _options() -> Dict[str, Any]:
        s = QSettings()
        return {"on_quit": as_bool(s.value(f"{CONFIG_GROUP}/exportOnQuit", False)),
                "days": as_int(s.value(f"{CONFIG_GROUP}/exportEveryDays", 0), 0),
                "dir": str(s.value(f"{CONFIG_GROUP}/exportDir", "")).strip(),
                "keep": as_int(s.value(f"{CONFIG_GROUP}/exportKeep", 10), 10),
                "last": float(s.value(f"{CONFIG_GROUP}/lastAutoExport", 0) or 0)}

    def _record(self, path: str) -> None:
        QSettings().setValue(f"{CONFIG_GROUP}/lastAutoExport", time.time())
        add_config_history({"path": path, "kind": "automático", "secrets": "none"})
        _log(f"Configurações exportadas automaticamente: {path}")

    def _tick(self) -> None:
        opts = self._options()
        if opts["days"] <= 0 or not opts["dir"] or self._running:
            return
        if time.time() - opts["last"] < opts["days"] * 86400:
            return
        self._running = True
        info = source_info(self.iface)

        def work(task: FunctionTask) -> str:
            return export_all(opts["dir"], opts["keep"], self.version, info, task.log)

        def done(task: FunctionTask) -> None:
            self._running = False
            if task.error:
                if self._disconnected(task.error):
                    _log(f"Exportação agendada das configurações não executada: {task.error}")
                else:
                    self._warn(f"Exportação agendada das configurações falhou: {task.error}")
            elif task.value:
                self._record(task.value)

        self._start("BackupQgis: exportação agendada das configurações", work, done)

    def _on_quit(self) -> None:
        opts = self._options()
        if not opts["on_quit"] or not opts["dir"]:
            return
        try:
            self._record(export_all(opts["dir"], opts["keep"], self.version, source_info(self.iface), _log))
        except (OSError, SyncAborted, ValueError) as exc:
            _log(f"Exportação automática ao sair falhou: {exc}", "Warning")


class ProjectsAutomation(_Base):
    """Versões ao salvar, snapshot agendado e sincronização periódica."""

    def __init__(self, iface: Any) -> None:
        super().__init__(iface)
        self._last_sync: Dict[str, float] = {}
        self._running_pairs: set = set()
        self._snapshot_running = False
        QgsProject.instance().projectSaved.connect(self._on_project_saved)

    def _disconnect(self) -> None:
        try:
            QgsProject.instance().projectSaved.disconnect(self._on_project_saved)
        except (TypeError, RuntimeError):
            pass

    def _tick(self) -> None:
        self._tick_snapshot()
        self._tick_sync()

    # ------------------------------------------------------------------
    def _on_project_saved(self) -> None:
        settings = QSettings()
        if not as_bool(settings.value(f"{PROJECTS_GROUP}/versionOnSave", False)):
            return
        path = QgsProject.instance().fileName()
        dest = str(settings.value(f"{PROJECTS_GROUP}/dest", "")).strip()
        if not path or not path.lower().endswith((".qgz", ".qgs")) or not dest:
            return
        keep = as_int(settings.value(f"{PROJECTS_GROUP}/versionKeep", 20), 20)

        def work(task: FunctionTask) -> Any:
            return version_project_file(path, dest, keep, task.log)

        def done(task: FunctionTask) -> None:
            if task.error:
                self._warn(f"Versão do projeto não gravada: {task.error}")
            elif task.value:
                _log(f"Versão do projeto gravada: {task.value}")

        self._start("BackupQgis: versão do projeto", work, done)

    # ------------------------------------------------------------------
    def _tick_snapshot(self) -> None:
        s = QSettings()
        if self._snapshot_running or not as_bool(s.value(f"{PROJECTS_GROUP}/autoSnapshot", False)):
            return
        hours = max(1, as_int(s.value(f"{PROJECTS_GROUP}/autoSnapshotHours", 24), 24))
        last = float(s.value(f"{PROJECTS_GROUP}/lastAutoSnapshot", 0) or 0)
        if time.time() - last < hours * 3600:
            return
        dest = str(s.value(f"{PROJECTS_GROUP}/dest", "")).strip()
        try:
            paths = [p for p in json.loads(str(s.value(f"{PROJECTS_GROUP}/list", "[]"))) if isinstance(p, str)]
        except ValueError:
            paths = []
        if not dest or not paths:
            return
        keep = as_int(s.value(f"{PROJECTS_GROUP}/keep", 10), 10)
        info = dict(source_info(self.iface), trigger="agendado")
        self._snapshot_running = True

        def work(task: FunctionTask) -> Any:
            inventories = [inventory_project(p) for p in paths]
            snap, manifest = run_project_backup(inventories, dest, info, task.log, task.isCanceled, task.report)
            apply_retention(dest, keep, task.log)
            return snap, manifest

        def done(task: FunctionTask) -> None:
            self._snapshot_running = False
            if task.error:
                if self._disconnected(task.error):
                    _log(f"Snapshot agendado não executado: {task.error}")
                else:
                    self._warn(f"Snapshot agendado falhou: {task.error}")
                return
            if not task.value:
                return
            QSettings().setValue(f"{PROJECTS_GROUP}/lastAutoSnapshot", time.time())
            snap, manifest = task.value
            text = f"Snapshot agendado: {snap} ({len(manifest['files'])} arquivos)."
            (self._warn if manifest["errors"] else _log)(
                text + (f" {len(manifest['errors'])} falhas de cópia." if manifest["errors"] else ""))

        self._start("BackupQgis: snapshot agendado de projetos", work, done)

    # ------------------------------------------------------------------
    def _tick_sync(self) -> None:
        try:
            pairs = json.loads(str(QSettings().value(SYNC_PAIRS_KEY, "[]")))
        except ValueError:
            return
        now = time.time()
        for pair in pairs:
            if not isinstance(pair, dict) or not pair.get("auto") or pair.get("mode") not in ("additive", "mirror"):
                continue
            pid = pair.get("id", "")
            interval = max(5, as_int(pair.get("interval", 60), 60)) * 60
            last = self._last_sync.get(pid)
            if last is None:
                self._last_sync[pid] = now  # primeira execução após um intervalo inteiro
                continue
            if pid in self._running_pairs or now - last < interval:
                continue
            self._last_sync[pid] = now
            self._run_pair(pair)

    def _run_pair(self, pair: Dict[str, Any]) -> None:
        pid = pair["id"]
        self._running_pairs.add(pid)
        name = pair.get("name") or pair.get("source", "")

        def work(task: FunctionTask) -> Any:
            return run_one_way(pair.get("source", ""), pair.get("dest", ""), pair.get("target_uuid", ""),
                               pair["mode"], parse_excludes(pair.get("excludes", "")), task.log,
                               task.isCanceled, task.report)

        def done(task: FunctionTask) -> None:
            self._running_pairs.discard(pid)
            if task.error:
                msg = f"Sincronização automática '{name}' não executada: {task.error}"
                (_log if self._disconnected(task.error) else self._warn)(msg)
                return
            stats = task.value or {}
            text = (f"Sincronização automática '{name}': {stats.get('copied', 0)} copiados, "
                    f"{stats.get('updated', 0)} atualizados, {stats.get('trashed', 0)} na lixeira, "
                    f"{stats.get('failed', 0)} falhas.")
            (self._warn if stats.get("failed") else _log)(text)

        self._start(f"BackupQgis: sincronização automática ({name})", work, done)
