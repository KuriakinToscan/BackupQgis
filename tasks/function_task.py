# -*- coding: utf-8 -*-
"""
QgsTask genérica para operações de arquivos (inventário, backup de projetos,
simulação e execução de sincronização).
"""

from typing import Any, Callable, Optional

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.compat import enum_value


class FunctionTask(QgsTask):
    """
    Executa ``work(task)`` fora da thread principal.

    Dentro de ``work`` use ``task.log(msg)``, ``task.report(feitos, total)`` e
    ``task.isCanceled()``; o valor retornado fica em ``task.value`` e uma
    exceção fica em ``task.error`` (texto). ``on_done(task)`` é chamado na
    thread principal ao terminar.
    """

    log_message = pyqtSignal(str)

    def __init__(self, description: str, work: Callable[["FunctionTask"], Any],
                 on_done: Callable[["FunctionTask"], None]) -> None:
        super().__init__(description, enum_value(QgsTask, "Flag", "CanCancel"))
        self._work = work
        self._on_done = on_done
        self.value: Any = None
        self.error: Optional[str] = None

    def log(self, message: str) -> None:
        """Envia uma linha ao log da interface (thread-safe via sinal)."""
        self.log_message.emit(message)

    def report(self, done: int, total: int) -> None:
        """Atualiza o progresso em porcentagem."""
        self.setProgress(100.0 * done / total if total else 0.0)

    def run(self) -> bool:
        try:
            self.value = self._work(self)
            return not self.isCanceled()
        except Exception as exc:  # erro inesperado não pode derrubar o QGIS
            self.error = str(exc) or type(exc).__name__
            return False

    def finished(self, result: bool) -> None:  # thread principal
        self._on_done(self)
