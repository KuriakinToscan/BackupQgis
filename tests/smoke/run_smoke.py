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
os.environ.update(SMOKE_HOME=home, SMOKE_PKG=pkg, SMOKE_SETTINGS=sett, QT_QPA_PLATFORM="offscreen",
                  HOME=os.path.join(base, "userhome"), XDG_DOCUMENTS_DIR=os.path.join(base, "userhome", "Documents"))
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
assert dlg.cmb_secrets.findData("authcfg") == -1  # stub sem QgsAuthManager
plugins_zip = out[:-5] + ".plugins.zip"
import zipfile as _zf
assert os.path.isfile(plugins_zip) and "QuickOSM/metadata.txt" in _zf.ZipFile(plugins_zip).namelist()

# altera e restaura
s.setValue("QuickOSM/lastQuery", "mudou"); s.remove("qgis/connections-wms/IBGE/url"); s.sync()
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (out, ""))
dlg.load_backup_file()
assert dlg.backup_archive == plugins_zip and dlg.backup_archive_keys == ["QuickOSM"]
assert "ZIP de plugins" in dlg.imp_notes.text()
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

# ---- Projetos ----
import sqlite3, zipfile
proj = os.path.join(base, "proj"); os.makedirs(os.path.join(proj, "dados"))
con = sqlite3.connect(os.path.join(proj, "dados", "base.gpkg")); con.execute("create table t(a)"); con.commit(); con.close()
qgs = ('<qgis><projectlayers><maplayer><layername>A</layername><provider>ogr</provider>'
       '<datasource>./dados/base.gpkg|layername=t</datasource></maplayer>'
       '<maplayer><layername>PG</layername><provider>postgres</provider><datasource>dbname=x password=segredo</datasource></maplayer>'
       '</projectlayers></qgis>')
qgz = os.path.join(proj, "mapa.qgz")
with zipfile.ZipFile(qgz, "w") as z:
    z.writestr("mapa.qgs", qgs)
from qgis.core import QgsProject
QgsProject.instance()._file = qgz
hd = os.path.join(base, "hd"); os.makedirs(hd)
from BackupQgis.ui.projects_dialog import ProjectsDialog
pd = ProjectsDialog(FakeIface(), repo)
pd.show(); app.processEvents()
assert pd.nav.count() == 3 and dlg.nav.count() == 4  # janelas separadas
pp = pd.projects_page
pp.add_current()
assert pp.project_paths() == [qgz]
pp.dest.setText(hd)
pp.run_inventory()
assert "2 arquivos" in pp.summary.text() and "1 camadas remotas" in pp.summary.text(), pp.summary.text()
pp.run_backup()
snaps = [d for d in os.listdir(hd) if not d.startswith(".")]
assert snaps == ["mapa_bkp"], snaps
bkp_dir = os.path.join(hd, "mapa_bkp")
assert os.path.isfile(os.path.join(bkp_dir, "mapa.qgz")) and os.path.isfile(os.path.join(bkp_dir, "dados", "base.gpkg"))
manifest = json.load(open(os.path.join(bkp_dir, "_backupqgis", "manifesto.json"), encoding="utf-8"))
assert len(manifest["files"]) == 2 and "segredo" not in json.dumps(manifest)
log_text = pd.log_view.toPlainText()
assert "══ Backup de 1 projeto(s)" in log_text and "✔ mapa.qgz: cópia: 2 novos" in log_text, log_text
assert "Resumo: " in log_text and pd.log_box.collapsed is False
# a pasta da exportação automática das configurações não pode ser a dos projetos
from qgis.gui import QgsMessageBar
from qgis.core import QgsMessageLog
n_msgs = len(QgsMessageBar.pushed)
dlg.auto_export_dir.setText(os.path.join(hd, "sub"))
dlg._save_auto_export()
assert QSettings().value("BackupQgis/config/exportDir") != os.path.join(hd, "sub")
assert dlg.auto_export_dir.text() != os.path.join(hd, "sub")
assert any("backup de projetos" in t for _l, _ti, t in QgsMessageBar.pushed[n_msgs:])
print("projects OK:", pp.summary.text())

# ---- Sincronizar ----
src = os.path.join(base, "sig"); os.makedirs(os.path.join(src, "sub"))
open(os.path.join(src, "a.txt"), "w").write("a")
open(os.path.join(src, "sub", "b.txt"), "w").write("b")
nas = os.path.join(base, "nas"); os.makedirs(nas)
sp = pd.sync_page
sp.name.setText("SIG"); sp.source.setText(src); sp.dest.setText(nas)
sp.simulate()
assert os.path.exists(os.path.join(nas, ".backupqgis-target.json"))
assert sp.btn_sync.isEnabled() and "2 novos" in sp.summary.text(), sp.summary.text()
sp.synchronize()
assert open(os.path.join(nas, "sub", "b.txt")).read() == "b"
sp.simulate()
assert not sp.btn_sync.isEnabled() and "0 novos" in sp.summary.text(), sp.summary.text()
# destino desconectado
import shutil
shutil.move(nas, nas + "_off")
from qgis.gui import QgsMessageBar
n_before = len(QgsMessageBar.pushed)
sp.simulate()
assert any("conectado" in t for _l, _ti, t in QgsMessageBar.pushed[n_before:]), QgsMessageBar.pushed[n_before:]
assert not os.path.exists(nas)
print("sync OK")
shutil.move(nas + "_off", nas)

# ---- Fase 3: pacote, restauração, versões, bidirecional, automação ----
pkg_out = os.path.join(base, "pacotes"); os.makedirs(pkg_out)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: pkg_out)
pp._ask = lambda title, text, buttons: "zip"
pp.run_package()
assert os.path.isfile(os.path.join(pkg_out, "mapa.zip")), os.listdir(pkg_out)
with zipfile.ZipFile(os.path.join(pkg_out, "mapa.zip")) as z:
    names = z.namelist()
assert any(n.endswith("base.gpkg") for n in names) and any(n.endswith("mapa.qgz") for n in names), names

restored = os.path.join(base, "restaurado"); os.makedirs(restored)
answers = iter([bkp_dir, restored])
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: next(answers))
pp._ask = lambda title, text, buttons: "new"
from qgis.PyQt.QtWidgets import QInputDialog
QInputDialog.getItem = staticmethod(lambda parent, title, label, items, *a: (items[0], True))
pp.run_restore()
assert os.path.isfile(os.path.join(restored, "mapa.qgz")) and os.path.isfile(os.path.join(restored, "dados", "base.gpkg"))
assert "✔ Arquivos restaurados: 2" in pd.log_view.toPlainText()
# snapshot do formato anterior continua listado e restaurável
from BackupQgis.core.project_backup import run_project_backup
from BackupQgis.core.project_inventory import inventory_project
legacy, _m = run_project_backup([inventory_project(qgz)], hd, {"trigger": "manual"}, lambda m: None, lambda: False,
                                lambda d, t: None)
restored_old = os.path.join(base, "restaurado_antigo"); os.makedirs(restored_old)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: restored_old)
pp.run_restore(legacy)
found = [os.path.join(r, f) for r, _d, fs in os.walk(restored_old) for f in fs]
assert any(f.endswith("mapa.qgz") for f in found) and any(f.endswith("base.gpkg") for f in found), found
print("package/restore OK")

pp.chk_version.setChecked(True)
assert QSettings().value("BackupQgis/projects/versionOnSave") in (True, "true")
from BackupQgis.ui.automation import ConfigAutomation, ProjectsAutomation
from qgis.core import QgsProject
auto = ProjectsAutomation(FakeIface())
cfg_auto = ConfigAutomation(FakeIface(), "0.4.0")
QgsProject.instance().projectSaved.emit()
versions = os.listdir(os.path.join(bkp_dir, "_versoes"))
assert len(versions) == 1 and versions[0].startswith("mapa_") and versions[0].endswith(".qgz"), versions

exp_dir = os.path.join(base, "auto_export"); os.makedirs(exp_dir)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: exp_dir)
dlg._browse_auto_export()
dlg.chk_auto_export.setChecked(True)
cfg_auto._on_quit()
exported = os.listdir(exp_dir)
assert len(exported) == 1 and exported[0].startswith("qgis-config_"), exported
assert "segredo" not in open(os.path.join(exp_dir, exported[0]), encoding="utf-8").read()

# sincronização automática de par aditivo (forçando o relógio)
sp.auto.setChecked(True); sp.save_pair()
open(os.path.join(src, "novo.txt"), "w").write("n")
auto._tick()
for k in auto._last_sync:
    auto._last_sync[k] -= 10 ** 6
auto._tick()
assert os.path.isfile(os.path.join(nas, "novo.txt"))
# backup agendado e exportação agendada (relógio zerado)
from BackupQgis.core.project_mirror import list_states
n_states = len(list_states(bkp_dir))
pp.chk_schedule.setChecked(True)
QSettings().setValue("BackupQgis/projects/lastAutoSnapshot", 0)
auto._tick()
states = list_states(bkp_dir)
assert len(states) == n_states + 1, states
assert states[-1]["source"]["trigger"] == "agendado" and states[-1]["changes"]["unchanged"] == 2
assert any("══ Backup agendado" in m for _l, m in QgsMessageLog.msgs)
dlg.auto_export_days.setValue(1)
QSettings().setValue("BackupQgis/config/lastAutoExport", 0)
cfg_auto._tick()
assert len(os.listdir(exp_dir)) == 2, os.listdir(exp_dir)
auto.stop(); cfg_auto.stop()
print("automation OK")

# históricos separados
pd.history_page.refresh()
assert pd.history_page.table.rowCount() == len(states) + 1, pd.history_page.table.rowCount()  # + snapshot antigo
assert [e["kind"] for e in pd.history_page.entries].count("legacy") == 1
row = [i for i, e in enumerate(pd.history_page.entries) if e["kind"] == "mirror"][0]
assert pd.history_page.table.item(row, 2).text() == "agendado"
pd.history_page.table.selectRow(row)
pd.history_page.verify_selected()
assert "✔ Conferência SHA-256" in pd.log_view.toPlainText()
dlg.history_page.refresh()
kinds = sorted(e.get("kind") for e in dlg.history_page.entries)
assert kinds.count("automático") == 2 and kinds.count("manual") == 1, kinds
manual_row = [i for i, e in enumerate(dlg.history_page.entries) if e["kind"] == "manual"][0]
dlg.history_page.table.selectRow(manual_row)
dlg.history_page.restore_selected()
assert dlg.stack.currentWidget() is dlg.import_page and dlg.backup_path == out
print("history OK")

# migração das preferências da 0.3.0
from BackupQgis.ui.common import migrate_settings
QSettings().setValue("BackupQgis/auto/exportKeep", 7)
QSettings().remove("BackupQgis/config/exportKeep")
QSettings().setValue("BackupQgis/auto/versionKeep", 5)
QSettings().remove("BackupQgis/projects/versionKeep")
moved = migrate_settings()
assert int(QSettings().value("BackupQgis/config/exportKeep")) == 7
assert int(QSettings().value("BackupQgis/projects/versionKeep")) == 5
assert not QSettings().contains("BackupQgis/auto/exportKeep") and migrate_settings() == []
print("migration OK")

# bidirecional
other = os.path.join(base, "outro_pc"); os.makedirs(other)
open(os.path.join(other, "z.txt"), "w").write("z")
sp.new_pair()
sp.name.setText("Bi"); sp.source.setText(src); sp.dest.setText(other)
sp.mode.setCurrentIndex(sp.mode.findData("bidirectional"))
assert not sp.auto.isEnabled()
sp.simulate()
assert sp.btn_sync.isEnabled(), sp.summary.text()
sp.synchronize()
assert os.path.isfile(os.path.join(src, "z.txt")) and os.path.isfile(os.path.join(other, "a.txt"))
sp.simulate()
assert not sp.btn_sync.isEnabled(), sp.summary.text()
print("bidirectional OK")

from qgis.core import QgsMessageLog
errs = [m for l, m in QgsMessageLog.msgs if l == 2 and "conectado" not in m]
print("errors:", errs)
dlg.shutdown(); pd.shutdown()
print("SMOKE OK")
