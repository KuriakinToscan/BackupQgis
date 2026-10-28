# -*- coding: utf-8 -*-
"""
Teste de fumaça da interface fora do QGIS.

Usa stubs mínimos de qgis.core/qgis.gui/qgis.utils sobre o PyQt5 e
exercita: varredura, exportação (sem senhas), importação, prévia, restauração,
desfazer, verificação de dependências e filtro/marcação em lote.
Executado em subprocesso por tests/test_dialog_smoke.py.
"""
import json, os, sys, tempfile
base = tempfile.mkdtemp()
home = os.path.join(base, "home"); pkg = os.path.join(base, "pkg"); sett = os.path.join(base, "settings")
for d in (home, os.path.join(pkg, "python", "plugins", "processing"), sett): os.makedirs(d, exist_ok=True)
os.environ.update(SMOKE_HOME=home, SMOKE_PKG=pkg, SMOKE_SETTINGS=sett, QT_QPA_PLATFORM="offscreen")
os.makedirs(os.path.join(home, "QuickOSM"))
open(os.path.join(home, "QuickOSM", "metadata.txt"), "w").write("[general]\nname=QuickOSM\nversion=2.2\nplugin_dependencies=Plugin Inexistente\n")
open(os.path.join(home, "QuickOSM", "requirements.txt"), "w").write("pytest>=7\npacote-inexistente-xyz\n")
open(os.path.join(pkg, "python", "plugins", "processing", "metadata.txt"), "w").write("[general]\nname=Processing\nversion=2.12\n")
here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(os.path.dirname(here))
pkgroot = os.path.join(base, "pkgroot"); os.makedirs(pkgroot)
os.symlink(repo, os.path.join(pkgroot, "BackupQgis"))
sys.path[:0] = [os.path.join(here, "stubs"), pkgroot]

from PyQt5.QtCore import QCoreApplication, QSettings, QSize
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, sett)
app = QApplication([])
QCoreApplication.setOrganizationName("QGIS"); QCoreApplication.setApplicationName("QGIS3")
s = QSettings()
s.setValue("PythonPlugins/QuickOSM", True)
s.setValue("QuickOSM/lastQuery", "amenity=school")
s.setValue("quickosm/size", QSize(3, 4))
s.setValue("Processing/Configuration/FOLDER", "/x")
s.setValue("PostgreSQL/connections/ibama/host", "db.ibama")
s.setValue("PostgreSQL/connections/ibama/password", "segredo")
s.setValue("qgis/connections-wms/IBGE/url", "https://geo.ibge")
s.setValue("qgis/connections-wms/selected", "IBGE")
s.setValue("app/plugin_repositories/Privado/url", "https://repo")
s.sync()

class FakeIface:
    def mainWindow(self): return None
    def userProfileManager(self): raise AttributeError

from BackupQgis.ui import dialog as D
from BackupQgis.ui import preview_dialog as P
dlg = D.BackupPluginDialog(FakeIface(), repo)
dlg.show(); app.processEvents()
assert dlg.scan is not None, "scan"
print("plugins:", dlg.exp_plugins.row_count(), "conns:", dlg.exp_conns.row_count(), dlg.exp_counter.text())
assert dlg.exp_plugins.row_count() == 2 and dlg.exp_conns.row_count() == 2

out = os.path.join(base, "b.json")
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
dlg.export_backup()
doc = json.load(open(out, encoding="utf-8"))
print("exported keys:", sorted(doc["settings"]), sorted(doc["server_connections"]), sorted(doc["repositories"]))
assert "PostgreSQL/connections/ibama/password" not in doc["server_connections"]
assert doc["omitted_sensitive_keys"] == ["PostgreSQL/connections/ibama/password"]
assert [p["key"] for p in doc["plugins"]] == ["QuickOSM"]
assert "Processing/Configuration/FOLDER" not in doc["settings"]

# altera e restaura
s.setValue("QuickOSM/lastQuery", "mudou"); s.remove("qgis/connections-wms/IBGE/url"); s.sync()
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (out, ""))
dlg.load_backup_file()
print("import rows:", dlg.imp_plugins.row_count(), dlg.imp_conns.row_count(), dlg.imp_counter.text())
captured = {}
orig_init = P.RestorePreviewDialog.__init__
def fake_exec(self):
    captured["rows"] = self._rows; return 1
P.RestorePreviewDialog.exec = fake_exec
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
dlg.restore_backup(simulate=False)
s.sync()
print("preview:", [(r[0], r[1]) for r in captured["rows"]])
assert QSettings().value("QuickOSM/lastQuery") == "amenity=school"
assert QSettings().value("qgis/connections-wms/IBGE/url") == "https://geo.ibge"
assert QSettings().value("quickosm/size") == QSize(3, 4)
assert dlg.btn_undo.isEnabled()

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
dlg.undo_last_restore()
assert QSettings().value("QuickOSM/lastQuery") == "mudou", QSettings().value("QuickOSM/lastQuery")
assert not QSettings().contains("qgis/connections-wms/IBGE/url")

dlg.check_dependencies()
print("deps:", dlg.dep_summary.text())
assert "3 dependências verificadas, 2 ausentes" in dlg.dep_summary.text()

# filtro e marcação em lote
dlg.exp_filter.setText("ibge")
dlg.exp_conns.set_visible_checks(D.CON_CHECK, False)
assert dlg.exp_conns.checked_ids(D.CON_CHECK) == ["PostGIS::ibama"]
from qgis.core import QgsMessageLog
errs = [m for l, m in QgsMessageLog.msgs if l == 2]
print("errors:", errs)
dlg.shutdown()
print("SMOKE OK")
