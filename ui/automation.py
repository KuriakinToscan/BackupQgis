# -*- coding: utf-8 -*-
"""
Automação do BackupQgis, ativa enquanto o plugin estiver carregado:

- versão do arquivo de projeto a cada salvamento;
- sincronização periódica dos pares marcados como automáticos (apenas modos
  aditivo e espelho, sem forçar a trava de segurança);
- exportação de plugins/configurações/conexões ao fechar o QGIS.

Falhas são registradas no painel de mensagens (aba BackupQgis); só
problemas que exigem ação aparecem na barra de mensagens do QGIS.
"""

import json
import time
from typing import Any, Dict, List

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import QObject, QSettings, QTimer

from ..core.auto_export import export_all
from ..core.compat import enum_value
from ..core.project_backup import version_project_file
from ..core.sync import SyncAborted, parse_excludes, run_one_way
from ..tasks.function_task import FunctionTask
from .common import AUTO_GROUP, source_info

LOG_TAG = "BackupQgis"
PROJECTS_KEY = "BackupQgis/projects"
PAIRS_KEY = "BackupQgis/sync/pairs"
TICK_MS = 60 * 1000


def _bool(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).lower() in ("true", "1")


def _log(message: str, level: str = "Info") -> None:
    QgsMessageLog.logMessage(message, LOG_TAG, enum_value(Qgis, "MessageLevel", level))


class Automation(QObject):
    """Conecta sinais do QGIS e um temporizador às rotinas automáticas."""

    def __init__(self, iface: Any, version: str) -> None:
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.version = version
        self._tasks: List[FunctionTask] = []
        self._last_sync: Dict[str, float] = {}
        self._running_pairs: set = set()

        QgsProject.instance().projectSaved.connect(self._on_project_saved)
        app = QgsApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_quit)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def stop(self) -> None:
        """Desconecta sinais, para o temporizador e cancela tarefas (no unload do plugin)."""
        self.timer.stop()
        for signal, slot in ((QgsProject.instance().projectSaved, self._on_project_saved),):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        app = QgsApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(self._on_quit)
            except (TypeError, RuntimeError):
                pass
        for task in list(self._tasks):
            try:
                task.cancel()
            except RuntimeError:
                pass
        self._tasks.clear()

    # ------------------------------------------------------------------
    def _start(self, description: str, work: Any, on_done: Any) -> None:
        task = FunctionTask(description, work, lambda t: self._done(t, on_done))
        task.log_message.connect(_log)
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _done(self, task: FunctionTask, on_done: Any) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        on_done(task)

    def _warn(self, text: str) -> None:
        _log(text, "Warning")
        try:
            self.iface.messageBar().pushMessage("BackupQgis", text, enum_value(Qgis, "MessageLevel", "Warning"), 10)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _on_project_saved(self) -> None:
        settings = QSettings()
        if not _bool(settings.value(f"{AUTO_GROUP}/versionOnSave", False)):
            return
        path = QgsProject.instance().fileName()
        dest = str(settings.value(f"{PROJECTS_KEY}/dest", "")).strip()
        if not path or not path.lower().endswith((".qgz", ".qgs")) or not dest:
            return
        try:
            keep = int(settings.value(f"{AUTO_GROUP}/versionKeep", 20))
        except (TypeError, ValueError):
            keep = 20

        def work(task: FunctionTask) -> Any:
            return version_project_file(path, dest, keep, task.log)

        def done(task: FunctionTask) -> None:
            if task.error:
                self._warn(f"Versão do projeto não gravada: {task.error}")
            elif task.value:
                _log(f"Versão do projeto gravada: {task.value}")

        self._start("BackupQgis: versão do projeto", work, done)

    def _tick(self) -> None:
        try:
            pairs = json.loads(str(QSettings().value(PAIRS_KEY, "[]")))
        except ValueError:
            return
        now = time.time()
        for pair in pairs:
            if not isinstance(pair, dict) or not pair.get("auto") or pair.get("mode") not in ("additive", "mirror"):
                continue
            pid = pair.get("id", "")
            interval = max(5, int(pair.get("interval", 60) or 60)) * 60
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
                # Destino desconectado é situação normal (notebook fora da rede): só registra.
                level_warn = "conectado" not in task.error and "não encontrado" not in task.error.lower()
                (self._warn if level_warn else _log)(f"Sincronização automática '{name}' não executada: {task.error}")
                return
            stats = task.value or {}
            text = (f"Sincronização automática '{name}': {stats.get('copied', 0)} copiados, "
                    f"{stats.get('updated', 0)} atualizados, {stats.get('trashed', 0)} na lixeira, "
                    f"{stats.get('failed', 0)} falhas.")
            (self._warn if stats.get("failed") else _log)(text)

        self._start(f"BackupQgis: sincronização automática ({name})", work, done)

    def _on_quit(self) -> None:
        settings = QSettings()
        if not _bool(settings.value(f"{AUTO_GROUP}/exportOnQuit", False)):
            return
        dest = str(settings.value(f"{AUTO_GROUP}/exportDir", "")).strip()
        if not dest:
            return
        try:
            keep = int(settings.value(f"{AUTO_GROUP}/exportKeep", 10))
        except (TypeError, ValueError):
            keep = 10
        try:
            path = export_all(dest, keep, self.version, source_info(self.iface), _log)
            _log(f"Configurações exportadas ao sair: {path}")
        except (OSError, SyncAborted, ValueError) as exc:
            _log(f"Exportação automática ao sair falhou: {exc}", "Warning")
