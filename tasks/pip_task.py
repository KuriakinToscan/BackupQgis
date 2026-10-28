# -*- coding: utf-8 -*-
"""
Instalação de pacotes Python com pip em segundo plano.
"""

import subprocess
from typing import Callable, List, Optional

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.compat import enum_value
from ..core.deps import subprocess_flags


class PipInstallTask(QgsTask):
    """
    Executa ``<python> -m pip install --user <pacote>`` para cada pacote.

    O interpretador deve ser obtido antes com
    :func:`core.deps.find_python_interpreter` (nunca ``sys.executable``, que
    dentro do QGIS pode ser o próprio executável do QGIS).
    """

    log_message = pyqtSignal(str)

    def __init__(self, interpreter: str, packages: List[str],
                 on_done: Callable[["PipInstallTask"], None]) -> None:
        """
        :param interpreter: caminho do Python validado.
        :param packages: nomes de distribuições já confirmados pelo usuário.
        :param on_done: chamado na thread principal ao terminar.
        """
        super().__init__("BackupQgis: instalando pacotes Python", enum_value(QgsTask, "Flag", "CanCancel"))
        self.interpreter = interpreter
        self.packages = list(packages)
        self.installed: List[str] = []
        self.failed: List[str] = []
        self.error: Optional[str] = None
        self._on_done = on_done
        self._process: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        super().cancel()

    def run(self) -> bool:
        total = len(self.packages)
        try:
            for index, package in enumerate(self.packages):
                if self.isCanceled():
                    return False
                self.log_message.emit(f"pip install --user {package}")
                cmd = [self.interpreter, "-m", "pip", "install", "--user", "--disable-pip-version-check",
                       "--no-input", package]
                self._process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=subprocess_flags(),
                )
                assert self._process.stdout is not None
                for line in self._process.stdout:
                    line = line.rstrip()
                    if line:
                        self.log_message.emit(f"[pip] {line}")
                    if self.isCanceled():
                        break
                rc = self._process.wait()
                self._process = None
                if self.isCanceled():
                    return False
                if rc == 0:
                    self.installed.append(package)
                else:
                    self.failed.append(package)
                    self.log_message.emit(f"pip terminou com código {rc} para '{package}'.")
                self.setProgress(100.0 * (index + 1) / total)
            return not self.failed
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def finished(self, result: bool) -> None:  # thread principal
        self._on_done(self)
