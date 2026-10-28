import os, threading
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QIcon
class Qgis:
    QGIS_VERSION = "3.40.0-Bratislava"
    class MessageLevel:
        Info, Warning, Critical, Success = 0, 1, 2, 3
class QgsMessageLog:
    msgs = []
    @staticmethod
    def logMessage(m, tag, level): QgsMessageLog.msgs.append((level, m))
class _TM:
    def addTask(self, t):
        ok = t.run(); t.finished(ok)
class QgsApplication:
    SETTINGS = os.environ["SMOKE_SETTINGS"]
    @staticmethod
    def getThemeIcon(n): return QIcon()
    @staticmethod
    def pkgDataPath(): return os.environ["SMOKE_PKG"]
    @staticmethod
    def qgisSettingsDirPath(): return QgsApplication.SETTINGS
    _tm = _TM()
    @staticmethod
    def taskManager(): return QgsApplication._tm
class QgsTask(QObject):
    progressChanged = pyqtSignal(float)
    class Flag:
        CanCancel = 1
    def __init__(self, desc, flags):
        super().__init__(); self._c = False
    def setProgress(self, p): self.progressChanged.emit(float(p))
    def isCanceled(self): return self._c
    def cancel(self): self._c = True
