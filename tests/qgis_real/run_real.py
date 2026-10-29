# -*- coding: utf-8 -*-
"""
Teste de integração com QGIS real (PyQGIS headless), sem stubs.

Uso (HOME temporário para não tocar no perfil do usuário):

    HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen python3 tests/qgis_real/run_real.py

Requer ``python3-qgis`` (testado com QGIS 3.34 LTR). Cobre: inventário,
pacote e restauração de projetos reais com reabertura no QGIS; versão ao
salvar (sinal projectSaved); exportação ao fechar (aboutToQuit);
exportação/importação de authcfg com senha mestra; detecção de conexões
criadas pela API do QGIS; varredura e exportação pelo diálogo; instalação
de plugin a partir do ZIP do backup.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BASE = tempfile.mkdtemp(prefix="bq-real-")
PKGROOT = os.path.join(BASE, "pkgroot")
os.makedirs(PKGROOT)
os.symlink(REPO, os.path.join(PKGROOT, "BackupQgis"))
sys.path.insert(0, PKGROOT)
for extra in ("/usr/share/qgis/python", "/usr/share/qgis/python/plugins"):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.append(extra)

from qgis.core import (Qgis, QgsApplication, QgsAuthMethodConfig, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsProviderRegistry,
                       QgsRasterLayer, QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QSettings, QTimer  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

FAILURES = []


def check(cond, label, detail=""):
    print(("OK   " if cond else "FAIL ") + label + (f" -> {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(f"{label}: {detail}")


def wait(cond, timeout=30.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    return cond()


class FakeBar:
    pushed = []

    def pushMessage(self, *a):
        FakeBar.pushed.append(a)


class FakeIface:
    def mainWindow(self):
        return None

    def messageBar(self):
        return FakeBar()

    menu, toolbar = [], []

    def addPluginToMenu(self, name, action):
        FakeIface.menu.append(action)

    def removePluginMenu(self, name, action):
        FakeIface.menu.remove(action)

    def addToolBarIcon(self, action):
        FakeIface.toolbar.append(action)

    def removeToolBarIcon(self, action):
        FakeIface.toolbar.remove(action)


def as_bool_val(v):
    return v is True or str(v).lower() in ("true", "1")


def log(msg, *a):
    print("   log:", msg)


print("QGIS", Qgis.QGIS_VERSION, "perfil", QgsApplication.qgisSettingsDirPath())

# ---------------------------------------------------------------------------
# Dados e projeto reais
# ---------------------------------------------------------------------------
proj_dir = os.path.join(BASE, "trabalho", "projeto")
data_dir = os.path.join(BASE, "trabalho", "dados")
ext_dir = os.path.join(BASE, "outro_disco", "rasters")
for d in (proj_dir, data_dir, ext_dir):
    os.makedirs(d)
crs = QgsCoordinateReferenceSystem("EPSG:31982")

mem = QgsVectorLayer("Point?crs=EPSG:31982&field=nome:string", "pts", "memory")
feats = []
for i in range(3):
    f = QgsFeature(mem.fields())
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500000 + i * 10, 6600000)))
    f.setAttributes([f"p{i}"])
    feats.append(f)
mem.dataProvider().addFeatures(feats)

gpkg = os.path.join(data_dir, "base.gpkg")
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = "pontos"
QgsVectorFileWriter.writeAsVectorFormatV3(mem, gpkg, QgsProject.instance().transformContext(), opts)
shp = os.path.join(data_dir, "pontos.shp")
opts2 = QgsVectorFileWriter.SaveVectorOptions()
opts2.driverName = "ESRI Shapefile"
QgsVectorFileWriter.writeAsVectorFormatV3(mem, shp, QgsProject.instance().transformContext(), opts2)

from osgeo import gdal  # noqa: E402
tif = os.path.join(ext_dir, "orto.tif")
ds = gdal.GetDriverByName("GTiff").Create(tif, 20, 20, 1, gdal.GDT_Byte)
ds.SetGeoTransform([500000, 1, 0, 6600020, 0, -1])
ds.SetProjection(crs.toWkt())
ds.GetRasterBand(1).Fill(7)
ds = None

project = QgsProject.instance()
project.setCrs(crs)
lyr_gpkg = QgsVectorLayer(f"{gpkg}|layername=pontos", "Pontos GPKG", "ogr")
lyr_shp = QgsVectorLayer(shp, "Pontos SHP", "ogr")
lyr_tif = QgsRasterLayer(tif, "Orto", "gdal")
lyr_pg = QgsVectorLayer("dbname='sig' host=db.invalid port=5432 user='ana' password='segredo' "
                        "table=\"public\".\"t\" (geom)", "Banco", "postgres")
lyr_mem = QgsVectorLayer("Point?crs=EPSG:31982", "Temporária", "memory")
check(all(lyr.isValid() for lyr in (lyr_gpkg, lyr_shp, lyr_tif)), "camadas de arquivo válidas")
project.addMapLayers([lyr_gpkg, lyr_shp, lyr_tif, lyr_mem])
project.addMapLayer(lyr_pg)  # inválida, mas fica no projeto
qgz = os.path.join(proj_dir, "mapa.qgz")
check(project.write(qgz), "projeto .qgz gravado pelo QGIS")
qgs = os.path.join(proj_dir, "mapa2.qgs")
check(project.write(qgs), "projeto .qgs gravado pelo QGIS")
project.setFileName(qgz)

# ---------------------------------------------------------------------------
# Inventário, backup, pacote, restauração
# ---------------------------------------------------------------------------
try:
    from BackupQgis.core.project_backup import run_project_backup, verify_snapshot
    from BackupQgis.core.project_inventory import inventory_project
    from BackupQgis.core.project_package import build_package, restore_snapshot

    for path in (qgz, qgs):
        inv = inventory_project(path)
        paths = {os.path.normcase(os.path.realpath(f.path)) for f in inv.files}
        want = [gpkg, shp, shp[:-4] + ".dbf", shp[:-4] + ".shx", shp[:-4] + ".prj", tif]
        missing = [w for w in want if os.path.normcase(os.path.realpath(w)) not in paths]
        check(not missing, f"inventário {os.path.basename(path)} encontra dados", missing)
        check(len(inv.remote) == 1 and "segredo" not in json.dumps(inv.remote),
              f"inventário {os.path.basename(path)}: camada remota sem senha", inv.remote)
        check(inv.memory == ["Temporária"], f"inventário {os.path.basename(path)}: camada em memória", inv.memory)
        check(not inv.missing, f"inventário {os.path.basename(path)}: nada ausente", inv.missing)

    def reopen_check(project_file, label, forbidden_root, inside_root=None):
        p = QgsProject()
        ok = p.read(project_file)
        check(ok, f"{label}: QGIS abre o projeto")
        layers = {lyr.name(): lyr for lyr in p.mapLayers().values()}
        for name in ("Pontos GPKG", "Pontos SHP", "Orto"):
            lyr = layers.get(name)
            src = lyr.source() if lyr else ""
            check(lyr is not None and lyr.isValid(), f"{label}: camada '{name}' válida", src)
            path = src.split("|")[0]
            ok_place = path.startswith(inside_root) if inside_root else not path.startswith(forbidden_root)
            check(ok_place, f"{label}: '{name}' aponta para a cópia, não para o original", src)
        if "Pontos GPKG" in layers:
            check(layers["Pontos GPKG"].featureCount() == 3, f"{label}: GPKG com 3 feições")
        p.clear()

    hd = os.path.join(BASE, "hd")
    os.makedirs(hd)
    invs = [inventory_project(qgz), inventory_project(qgs)]
    snap, manifest = run_project_backup(invs, hd, {"host": "teste"}, log, lambda: False, lambda d, t: None)
    check(not manifest["errors"], "backup sem erros", manifest["errors"])
    check(not verify_snapshot(snap), "snapshot verificado (SHA-256)")

    pk = os.path.join(BASE, "pacotes")
    os.makedirs(pk)
    out, report = build_package(inventory_project(qgz), pk, False, log, lambda: False, lambda d, t: None)
    check(not report.unresolved, "pacote: todas as fontes reescritas", report.unresolved)
    shutil.move(os.path.join(BASE, "trabalho"), os.path.join(BASE, "trabalho_off"))  # simula outra máquina
    shutil.move(os.path.join(BASE, "outro_disco"), os.path.join(BASE, "outro_disco_off"))
    pkg_proj = [os.path.join(r, f) for r, _d, fs in os.walk(out) for f in fs if f == "mapa.qgz"][0]
    # move o pacote inteiro para outro lugar: caminhos precisam ser relativos
    moved = os.path.join(BASE, "pendrive")
    shutil.move(out, moved)
    pkg_proj = pkg_proj.replace(out, moved)
    reopen_check(pkg_proj, "pacote movido", os.path.join(BASE, "trabalho"))

    out_zip, _r = build_package(inventory_project(pkg_proj), pk, True, log, lambda: False, lambda d, t: None)
    check(zipfile.is_zipfile(out_zip), "pacote ZIP gerado a partir do pacote")

    restored = os.path.join(BASE, "restaurado")
    os.makedirs(restored)
    stats = restore_snapshot(snap, restored, False, log, lambda: False, lambda d, t: None)
    check(stats["failed"] == 0 and stats["projects"] == 2, "restauração para pasta nova", stats)
    for name in ("mapa.qgz", "mapa2.qgs"):
        found = [os.path.join(r, f) for r, _d, fs in os.walk(restored) for f in fs if f == name]
        if found:
            reopen_check(found[0], f"restaurado {name}", os.path.join(BASE, "trabalho"), restored)
        else:
            check(False, f"restaurado {name} existe")

    shutil.move(os.path.join(BASE, "trabalho_off"), os.path.join(BASE, "trabalho"))
    shutil.move(os.path.join(BASE, "outro_disco_off"), os.path.join(BASE, "outro_disco"))
    os.remove(shp)
    stats = restore_snapshot(snap, None, False, log, lambda: False, lambda d, t: None)
    check(os.path.isfile(shp) and stats["restored"] >= 1 and stats["failed"] == 0,
          "restauração nos caminhos originais repõe arquivo apagado", stats)
except Exception:
    check(False, "bloco projetos", traceback.format_exc())

# ---------------------------------------------------------------------------
# Backup em <projeto>_bkp: estrutura, histórico incremental, restauração de data
# ---------------------------------------------------------------------------
try:
    from BackupQgis.core.project_backup import version_project_file
    from BackupQgis.core.project_mirror import (backup_projects, find_mirrors, list_states, restore_mirror,
                                                verify_mirror)

    hd2 = os.path.join(BASE, "hd_bkp")
    os.makedirs(hd2)
    report, _invs = backup_projects([qgz, qgs], hd2, {"trigger": "manual"}, 0, log, lambda: False,
                                    lambda d, t: None)
    log("\n".join(report.lines()))
    check(report.count("erro") == 0, "_bkp: backup sem erros", report.lines())
    check(sorted(os.path.basename(m) for m in find_mirrors(hd2)) == ["mapa2_bkp", "mapa_bkp"],
          "_bkp: uma pasta por projeto", os.listdir(hd2))
    check(sorted(os.listdir(hd2)) == ["mapa2_bkp", "mapa_bkp"], "_bkp: nenhuma outra pasta no destino",
          os.listdir(hd2))
    bkp = os.path.join(hd2, "mapa_bkp")
    top = sorted(os.listdir(bkp))
    check(top == ["_backupqgis", "_externos", "mapa.qgz"], "_bkp: estrutura da pasta do projeto", top)
    ext_files = [os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(bkp, "_externos")) for f in fs]
    check(any(f.endswith(os.path.join("trabalho", "dados", "base.gpkg")) for f in ext_files)
          and any(f.endswith(os.path.join("outro_disco", "rasters", "orto.tif")) for f in ext_files),
          "_bkp: dados de fora da pasta do projeto em _externos", ext_files)

    # altera o GPKG pelo próprio QGIS e faz novo backup
    edit = QgsVectorLayer(f"{gpkg}|layername=pontos", "edição", "ogr")
    nf = QgsFeature(edit.fields())
    nf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500100, 6600000)))
    nf.setAttribute("nome", "p3")
    check(edit.dataProvider().addFeatures([nf])[0], "GPKG alterado pelo QGIS")
    del edit
    st = os.stat(gpkg)
    os.utime(gpkg, (st.st_atime, st.st_mtime + 5))
    report2, _i = backup_projects([qgz], hd2, {"trigger": "manual"}, 0, log, lambda: False, lambda d, t: None)
    log("\n".join(report2.lines()))
    states = list_states(bkp)
    check(len(states) == 2 and states[-1]["changes"]["updated"] >= 1, "_bkp: segundo backup só com o que mudou",
          [s.get("changes") for s in states])
    hist = os.path.join(bkp, "_historico", states[-1]["stamp"])
    hist_files = [f for _r, _d, fs in os.walk(hist) for f in fs]
    check("base.gpkg" in hist_files, "_bkp: versão anterior do GPKG em _historico", hist_files)
    check(not verify_mirror(bkp, states[0]["stamp"]) and not verify_mirror(bkp), "_bkp: integridade das duas datas")

    restored_old = os.path.join(BASE, "restaurado_bkp_data1")
    os.makedirs(restored_old)
    stats = restore_mirror(bkp, states[0]["stamp"], restored_old, False, log, lambda: False, lambda d, t: None)
    check(stats["failed"] == 0 and stats["projects"] == 1, "_bkp: restauração da 1ª data", stats)
    reopen_check(os.path.join(restored_old, "mapa.qgz"), "_bkp 1ª data", os.path.join(BASE, "trabalho"),
                 restored_old)  # confere 3 feições (antes da edição)

    restored_now = os.path.join(BASE, "restaurado_bkp_atual")
    os.makedirs(restored_now)
    stats = restore_mirror(bkp, None, restored_now, False, log, lambda: False, lambda d, t: None)
    p_now = QgsProject()
    check(p_now.read(os.path.join(restored_now, "mapa.qgz")), "_bkp atual: QGIS abre o projeto")
    lyr_now = [lyr for lyr in p_now.mapLayers().values() if lyr.name() == "Pontos GPKG"]
    check(lyr_now and lyr_now[0].isValid() and lyr_now[0].featureCount() == 4
          and lyr_now[0].source().startswith(restored_now), "_bkp atual: GPKG com 4 feições, na cópia",
          lyr_now[0].source() if lyr_now else "")
    p_now.clear()

    ver = version_project_file(qgz, hd2, 5, log)
    check(bool(ver) and os.path.dirname(ver) == os.path.join(bkp, "_versoes"), "_bkp: versão ao salvar em _versoes",
          ver)
except Exception:
    check(False, "bloco _bkp", traceback.format_exc())

# ---------------------------------------------------------------------------
# Conexões criadas pela API do QGIS e varredura do diálogo
# ---------------------------------------------------------------------------
try:
    reg = QgsProviderRegistry.instance()
    pg = reg.providerMetadata("postgres").createConnection(
        "dbname='sig' host=db.ibama port=5432 user='ana' password='segredo' sslmode=disable", {})
    pg.store("IBAMA PG")
    wms_md = reg.providerMetadata("wms")
    if hasattr(wms_md, "createConnection"):
        try:
            from qgis.core import QgsXyzConnection, QgsXyzConnectionUtils
            xyz = QgsXyzConnection()
            xyz.name = "OSM teste"
            xyz.url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            QgsXyzConnectionUtils.addConnection(xyz)
        except Exception as exc:
            print("   (XYZ pela API indisponível:", exc, ")")
    gpkg_md = reg.providerMetadata("ogr")
    try:
        gconn = gpkg_md.createConnection(gpkg, {})
        gconn.store("Base GPKG")
    except Exception as exc:
        print("   (conexão GPKG pela API indisponível:", exc, ")")
    QSettings().sync()
    keys = [k for k in QSettings().allKeys() if "IBAMA PG" in k or "OSM teste" in k or "Base GPKG" in k]
    print("   chaves gravadas pelo QGIS:", keys)

    # Árvore nova (QGIS >= 3.30): grava pelos próprios settings entries do QGIS
    from qgis.core import QgsOwsConnection, QgsSettingsTree

    def all_entries(node):
        out = list(node.childrenSettings())
        for child in node.childrenNodes():
            out += all_entries(child)
        return out

    tree = {e.definitionKey(): e for e in all_entries(QgsSettingsTree.node("connections"))}
    tree["/connections/ows/items/%1/connections/items/%2/url"].setVariantValue("https://wms.ibge", ["wms", "IBGE WMS"])
    tree["/connections/ows/items/%1/connections/items/%2/password"].setVariantValue("segredo", ["wms", "IBGE WMS"])
    tree["/connections/xyz/items/%1/url"].setVariantValue("https://t/{z}/{x}/{y}.png", ["OSM teste"])
    tree["/connections/xyz/items/%1/http-header"].setVariantValue({"referer": "ibama"}, ["OSM teste"])
    QSettings().sync()
    keys += [k for k in QSettings().allKeys() if "IBGE WMS" in k or "OSM teste" in k]

    from BackupQgis.core.keys import group_connection_keys
    groups = group_connection_keys(QSettings().allKeys())
    names = {name for (_label, name) in groups}
    for want in ("IBAMA PG", "OSM teste", "Base GPKG", "IBGE WMS"):
        if any(want in k for k in keys):
            check(want in names, f"conexão '{want}' detectada", sorted(groups))

    from BackupQgis.ui import dialog as D
    QSettings().setValue("PythonPlugins/processing", True)
    dlg = D.BackupPluginDialog(FakeIface(), REPO)
    dlg.show()
    check(wait(lambda: dlg.scan is not None), "varredura (QgsTask real) terminou")
    if dlg.scan is not None:
        print("   plugins:", dlg.exp_plugins.row_count(), "conexões:", dlg.exp_conns.row_count())
        check(dlg.exp_conns.row_count() >= 1, "diálogo lista conexões")
        out_json = os.path.join(BASE, "backup.json")
        from qgis.PyQt.QtWidgets import QFileDialog
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out_json, ""))
        dlg.exp_conns.set_visible_checks(D.CON_CHECK, True)
        dlg.export_backup()
        doc = json.loads(open(out_json, encoding="utf-8").read())
        check("segredo" not in json.dumps(doc), "exportação sem senha")
        check(any("IBAMA PG" in k for k in doc["server_connections"]), "exportação contém conexão PG",
              list(doc["server_connections"])[:5])
        check(any("IBGE WMS" in k for k in doc["server_connections"]), "exportação contém WMS (árvore 3.30+)")
        tree_keys = [k for k in QSettings().allKeys() if "IBGE WMS" in k or "OSM teste" in k]
        for k in tree_keys:
            QSettings().remove(k)
        check(QgsOwsConnection.connectionList("wms") == [], "WMS removido antes de restaurar")
        # restaura em outro "computador": remove conexão e reimporta
        pg_keys = [k for k in QSettings().allKeys() if "connections/IBAMA PG/" in k]
        print("   exportado:", {k: v for k, v in doc["server_connections"].items() if "IBAMA PG" in k})
        s = QSettings()
        for k in pg_keys:
            s.remove(k)
        s.sync()
        check("IBAMA PG" not in reg.providerMetadata("postgres").connections(False), "conexão removida antes de restaurar")
        from qgis.PyQt.QtWidgets import QMessageBox
        from BackupQgis.ui import preview_dialog as P
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (out_json, ""))
        P.RestorePreviewDialog.exec = lambda self: 1
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        dlg.load_backup_file()
        dlg.restore_backup(simulate=False)
        print("   após restaurar:", {k: QSettings().value(k) for k in QSettings().allKeys()
                                         if "connections/IBAMA PG/" in k})
        conns = reg.providerMetadata("postgres").connections(False)
        check("IBAMA PG" in conns, "conexão PG reconhecida pelo QGIS após restaurar", list(conns))
        check("IBGE WMS" in QgsOwsConnection.connectionList("wms"), "WMS (árvore 3.30+) restaurado")
        check(tree["/connections/xyz/items/%1/http-header"].valueAsVariant(["OSM teste"]) == {"referer": "ibama"},
              "cabeçalho HTTP do XYZ restaurado como mapa")
        if "IBAMA PG" in conns:
            check("db.ibama" in conns["IBAMA PG"].uri(), "URI da conexão restaurada", conns["IBAMA PG"].uri())
    dlg.shutdown()
except Exception:
    check(False, "bloco conexões/diálogo", traceback.format_exc())

# ---------------------------------------------------------------------------
# authcfg com senha mestra
# ---------------------------------------------------------------------------
try:
    from BackupQgis.ui.auth_io import auth_transfer_available, export_authcfg, import_authcfg
    am = QgsApplication.authManager()
    check(auth_transfer_available(), "authcfg disponível no QGIS")
    check(am.setMasterPassword("mestra123", True), "senha mestra definida")
    cfg = QgsAuthMethodConfig("Basic")
    cfg.setName("geoserver")
    cfg.setConfig("username", "ana")
    cfg.setConfig("password", "xyz")
    stored = am.storeAuthenticationConfig(cfg)
    check((stored[0] if isinstance(stored, tuple) else stored) and bool(cfg.id()), "authcfg criado", stored)
    ids_before = list(am.configIds())
    xml = os.path.join(BASE, "backup.authcfg.xml")
    n = export_authcfg(xml, "senhaArquivo", log)
    check(n == len(ids_before) and n >= 1 and os.path.isfile(xml), "authcfg exportado", n)
    check("xyz" not in open(xml, encoding="utf-8", errors="ignore").read(), "XML exportado não tem senha em claro")
    for cid in ids_before:
        am.removeAuthenticationConfig(cid)
    check(not list(am.configIds()), "authcfg removidos")
    try:
        import_authcfg(xml, "errada", False, log)
        check(not list(am.configIds()), "senha errada não importa nada", list(am.configIds()))
    except Exception as exc:
        print("   senha errada recusada:", exc)
    import_authcfg(xml, "senhaArquivo", False, log)
    after = list(am.configIds())
    check(sorted(after) == sorted(ids_before), "authcfg importado com os mesmos ids", (ids_before, after))
    if after:
        got = QgsAuthMethodConfig()
        am.loadAuthenticationConfig(after[0], got, True)
        check(got.config("password") == "xyz", "senha do authcfg preservada")
except Exception:
    check(False, "bloco authcfg", traceback.format_exc())

# ---------------------------------------------------------------------------
# Senhas protegidas: conexões com senha viram authcfg no backup
# ---------------------------------------------------------------------------
try:
    from qgis.core import QgsDataSourceUri, QgsOwsConnection, QgsSettingsTree
    from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox
    from BackupQgis.ui import dialog as D
    from BackupQgis.ui import preview_dialog as P

    am = QgsApplication.authManager()
    reg = QgsProviderRegistry.instance()
    pgmd = reg.providerMetadata("postgres")
    pgmd.createConnection("dbname='geo' host=pg.senha port=5432 user='bia' password='segredo2'", {}).store("PG senha")
    tree = {e.definitionKey(): e for e in all_entries(QgsSettingsTree.node("connections"))}
    wms = ["wms", "WMS senha"]
    tree["/connections/ows/items/%1/connections/items/%2/url"].setVariantValue("https://wms.senha", wms)
    tree["/connections/ows/items/%1/connections/items/%2/username"].setVariantValue("caio", wms)
    tree["/connections/ows/items/%1/connections/items/%2/password"].setVariantValue("segredo3", wms)
    QSettings().sync()
    ids_before = sorted(am.configIds())

    dlg2 = D.BackupPluginDialog(FakeIface(), REPO)
    dlg2.show()
    check(wait(lambda: dlg2.scan is not None), "varredura para exportação protegida")
    idx = dlg2.cmb_secrets.findData("authcfg")
    check(idx >= 0, "opção 'protegidas por authcfg' disponível")
    dlg2.cmb_secrets.setCurrentIndex(idx)
    dlg2.exp_filter.setText("")
    dlg2.exp_conns.set_visible_checks(D.CON_CHECK, True)
    out_p = os.path.join(BASE, "protegido.json")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out_p, ""))
    QInputDialog.getText = staticmethod(lambda *a, **k: ("senhaDoArquivo", True))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    dlg2.export_backup()
    raw = open(out_p, encoding="utf-8").read()
    doc = json.loads(raw)
    xml_p = os.path.join(BASE, "protegido.authcfg.xml")
    check("segredo2" not in raw and "segredo3" not in raw and "segredo" not in raw, "JSON protegido sem senhas")
    conv = doc.get("converted_to_authcfg", [])
    check("PostgreSQL/connections/PG senha/password" in conv
          and "connections/ows/items/wms/connections/items/WMS senha/password" in conv,
          "senhas de PG e WMS convertidas em authcfg", conv)
    pg_id = (doc["server_connections"].get("PostgreSQL/connections/PG senha/authcfg") or {}).get("value", "")
    wms_id = (doc["server_connections"].get(
        "connections/ows/items/wms/connections/items/WMS senha/authcfg") or {}).get("value", "")
    check(len(pg_id) == 7 and len(wms_id) == 7, "conexões referenciam o authcfg criado", (pg_id, wms_id))
    check(os.path.isfile(xml_p) and doc.get("authcfg_file") == "protegido.authcfg.xml", "XML de autenticação gravado")
    xml_txt = open(xml_p, encoding="utf-8", errors="ignore").read()
    check("segredo2" not in xml_txt and "segredo3" not in xml_txt, "XML criptografado (senhas não legíveis)")
    check(sorted(am.configIds()) == ids_before, "banco local sem as configurações temporárias",
          (ids_before, sorted(am.configIds())))
    dlg2.shutdown()

    # "Outra máquina": sem autenticações e sem as conexões
    for cid in list(am.configIds()):
        am.removeAuthenticationConfig(cid)
    st = QSettings()
    for k in st.allKeys():
        if "PG senha" in k or "WMS senha" in k:
            st.remove(k)
    st.sync()
    check("PG senha" not in pgmd.connections(False) and "WMS senha" not in QgsOwsConnection.connectionList("wms"),
          "conexões e autenticações removidas")

    dlg3 = D.BackupPluginDialog(FakeIface(), REPO)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (out_p, ""))
    P.RestorePreviewDialog.exec = lambda self: 1
    dlg3.load_backup_file()
    check(dlg3.backup_authcfg == xml_p, "restauração encontra o XML de autenticação")
    dlg3.restore_backup(simulate=False)
    check(pg_id in am.configIds() and wms_id in am.configIds(), "authcfg importado na restauração",
          sorted(am.configIds()))
    conns = pgmd.connections(False)
    check("PG senha" in conns, "conexão PG restaurada")
    if "PG senha" in conns:
        uri = QgsDataSourceUri(conns["PG senha"].uri())
        check(uri.authConfigId() == pg_id, "conexão PG usa o authcfg", conns["PG senha"].uri())
        expanded = uri.connectionInfo(True)
        check("segredo2" in expanded and "bia" in expanded, "QGIS resolve usuário e senha pelo authcfg", expanded)
    check("WMS senha" in QgsOwsConnection.connectionList("wms"), "conexão WMS restaurada")
    got = tree["/connections/ows/items/%1/connections/items/%2/authcfg"].valueAsVariant(wms)
    check(got == wms_id, "conexão WMS usa o authcfg", got)
    cfg = QgsAuthMethodConfig()
    am.loadAuthenticationConfig(wms_id, cfg, True)
    check(cfg.config("username") == "caio" and cfg.config("password") == "segredo3",
          "authcfg do WMS com usuário e senha")
    check(QSettings().value("PostgreSQL/connections/PG senha/password") in (None, ""),
          "senha não gravada em texto na conexão restaurada")
    dlg3.shutdown()
except Exception:
    check(False, "bloco senhas protegidas", traceback.format_exc())

# ---------------------------------------------------------------------------
# Modo protegido sem senhas nas conexões e gerenciador desabilitado (caso do Windows)
# ---------------------------------------------------------------------------
try:
    from BackupQgis.ui import auth_io as A
    tree["/connections/xyz/items/%1/url"].setVariantValue("https://livre/{z}/{x}/{y}.png", ["XYZ livre"])
    QSettings().sync()
    prompts = []
    QInputDialog.getText = staticmethod(lambda *a, **k: (prompts.append(a) or ("senhaDoArquivo", True)))
    QMessageBox.critical = staticmethod(lambda *a, **k: None)

    def only(dlg_, name):
        dlg_.exp_filter.setText("")
        dlg_.exp_conns.set_visible_checks(D.CON_CHECK, False)
        dlg_.exp_filter.setText(name)
        dlg_.exp_conns.set_visible_checks(D.CON_CHECK, True)

    dlg4 = D.BackupPluginDialog(FakeIface(), REPO)
    dlg4.show()
    wait(lambda: dlg4.scan is not None)
    dlg4.cmb_secrets.setCurrentIndex(dlg4.cmb_secrets.findData("authcfg"))
    only(dlg4, "XYZ livre")
    out_x = os.path.join(BASE, "so_xyz.json")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out_x, ""))
    dlg4.export_backup()
    check(os.path.isfile(out_x), "XYZ sem senha: backup gravado no modo protegido")
    check(not prompts, "XYZ sem senha: nenhuma senha pedida", prompts)
    check(not os.path.exists(os.path.join(BASE, "so_xyz.authcfg.xml")), "XYZ sem senha: sem arquivo de autenticação")

    class DisabledManager:
        def isDisabled(self):
            return True

        def exportAuthenticationConfigsToXml(self, *a):
            raise AssertionError("não deveria exportar")

        def setMasterPassword(self, *a):
            raise AssertionError("não deveria pedir a senha mestra")

    real_manager = A._manager
    A._manager = lambda: DisabledManager()
    D.auth_disabled = A.auth_disabled
    try:
        only(dlg4, "PG senha")
        out_d = os.path.join(BASE, "desabilitado.json")
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out_d, ""))
        dlg4.log_view.clear()
        dlg4.export_backup()
        check(not os.path.exists(out_d), "gerenciador desabilitado: backup com senha não gravado")
        check(not prompts, "gerenciador desabilitado: nenhuma senha pedida", prompts)
        check("desabilitado" in dlg4.log_view.toPlainText() and "abra o QGIS" in dlg4.log_view.toPlainText(),
              "gerenciador desabilitado: mensagem orienta reiniciar", dlg4.log_view.toPlainText())
    finally:
        A._manager = real_manager
    dlg4.shutdown()
except Exception:
    check(False, "bloco senha mestra", traceback.format_exc())

# ---------------------------------------------------------------------------
# ZIP de plugins: extração e carga real
# ---------------------------------------------------------------------------
try:
    import qgis.utils
    from BackupQgis.core import installer
    from BackupQgis.core.plugin_archive import write_plugins_archive
    src_plugins = os.path.join(BASE, "src_plugins", "plugin_zip_teste")
    os.makedirs(src_plugins)
    open(os.path.join(src_plugins, "metadata.txt"), "w").write(
        "[general]\nname=Plugin ZIP teste\nqgisMinimumVersion=3.0\ndescription=t\nversion=1.0\nauthor=t\n"
        "email=t@t\nabout=t\nrepository=x\n")
    open(os.path.join(src_plugins, "__init__.py"), "w").write(
        "def classFactory(iface):\n    class P:\n        def initGui(self): pass\n"
        "        def unload(self): pass\n    return P()\n")
    zp = os.path.join(BASE, "backup.plugins.zip")
    write_plugins_archive(zp, [("plugin_zip_teste", src_plugins)], log)
    home = installer.home_plugin_path()
    print("   pasta de plugins:", home)
    if home not in sys.path:
        sys.path.append(home)
    if home not in qgis.utils.plugin_paths:
        qgis.utils.plugin_paths.append(home)
    summary = installer.install_and_activate(["plugin_zip_teste"], log, app.processEvents, lambda: False,
                                             lambda d, t: None, zp, ["plugin_zip_teste"])
    print("   resumo:", summary)
    check("plugin_zip_teste" in summary["installed"], "plugin instalado do ZIP", summary)
    check(os.path.isfile(os.path.join(home, "plugin_zip_teste", "metadata.txt")),
          "plugin extraído na pasta do perfil")
    check("plugin_zip_teste" in summary["activated"] or "plugin_zip_teste" in summary["restart"],
          "plugin ativado (ou marcado para reiniciar)", summary)
except Exception:
    check(False, "bloco ZIP de plugins", traceback.format_exc())

# ---------------------------------------------------------------------------
# Automação: projectSaved e aboutToQuit reais
# ---------------------------------------------------------------------------
try:
    from BackupQgis import classFactory
    vers_dest = os.path.join(BASE, "versoes_dest")
    exp_dest = os.path.join(BASE, "auto_export")
    os.makedirs(vers_dest)
    os.makedirs(exp_dest)
    s = QSettings()
    s.setValue("BackupQgis/projects/dest", vers_dest)
    # preferências no formato da 0.3.0: o initGui migra para os grupos separados
    s.setValue("BackupQgis/auto/versionOnSave", True)
    s.setValue("BackupQgis/auto/versionKeep", 2)
    s.setValue("BackupQgis/auto/exportOnQuit", True)
    s.setValue("BackupQgis/auto/exportDir", exp_dest)
    iface = FakeIface()
    plugin = classFactory(iface)
    plugin.initGui()
    check(len(FakeIface.menu) == 2 and len(FakeIface.toolbar) == 2, "duas entradas no menu e na barra",
          [a.text() for a in FakeIface.menu])
    check(len(plugin.automations) == 2, "duas automações independentes", plugin.automations)
    check(QSettings().value("BackupQgis/config/exportDir") == exp_dest
          and as_bool_val(QSettings().value("BackupQgis/projects/versionOnSave"))
          and not QSettings().contains("BackupQgis/auto/exportDir"), "preferências da 0.3.0 migradas")
    FakeIface.menu[0].trigger()
    FakeIface.menu[1].trigger()
    win_cfg, win_proj = plugin.windows.get("config"), plugin.windows.get("projects")
    check(win_cfg is not None and win_proj is not None and win_cfg is not win_proj, "duas janelas separadas")
    if win_cfg is not None and win_proj is not None:
        check(win_cfg.nav.count() == 4 and win_proj.nav.count() == 3, "páginas de cada janela",
              (win_cfg.nav.count(), win_proj.nav.count()))
        wait(lambda: win_cfg.scan is not None)
    auto = plugin.automations[1]
    project = QgsProject.instance()
    project.read(qgz)
    folder = os.path.join(vers_dest, "mapa_bkp", "_versoes")
    for i in range(3):
        project.write()
        check(wait(lambda: os.path.isdir(folder) and len(os.listdir(folder)) >= min(i + 1, 2)),
              f"versão gravada após salvamento {i + 1}")
        time.sleep(1.1)
    wait(lambda: not auto._tasks)
    check(len(os.listdir(folder)) == 2, "retenção de versões (2)", os.listdir(folder))

    # snapshot agendado (relógio zerado) sem depender da janela de configurações
    s.setValue("BackupQgis/projects/list", json.dumps([qgz]))
    s.setValue("BackupQgis/projects/autoSnapshot", True)
    s.setValue("BackupQgis/projects/lastAutoSnapshot", 0)
    auto._tick()
    from BackupQgis.core.project_mirror import list_states as _ls
    vbkp = os.path.join(vers_dest, "mapa_bkp")
    check(wait(lambda: not auto._tasks and len(_ls(vbkp)) == 1), "backup agendado gravado em mapa_bkp")
    states_auto = _ls(vbkp)
    if states_auto:
        check(states_auto[0]["source"].get("trigger") == "agendado" and not states_auto[0]["errors"],
              "backup agendado sem erros")
    if win_proj is not None:
        win_proj.history_page.refresh()
        check(win_proj.history_page.table.rowCount() == len(states_auto), "histórico na janela de projetos")

    QTimer.singleShot(200, app.quit)
    app.exec_()
    files = os.listdir(exp_dest)
    check(len(files) == 1 and files[0].startswith("qgis-config_"), "exportação no aboutToQuit real", files)
    if files:
        txt = open(os.path.join(exp_dest, files[0]), encoding="utf-8").read()
        check("segredo" not in txt, "exportação automática sem senha")
    from BackupQgis.ui.common import load_config_history
    check(any(e["kind"] == "automático" for e in load_config_history()), "exportação automática no histórico")
    plugin.unload()
    check(not FakeIface.menu and not FakeIface.toolbar and not plugin.windows, "unload remove entradas e janelas")
except Exception:
    check(False, "bloco automação", traceback.format_exc())

print()
print("FALHAS:", len(FAILURES))
for f in FAILURES:
    print(" -", f.splitlines()[0] if len(f) > 300 else f)
app.exitQgis()
print("REAL OK" if not FAILURES else "REAL FALHOU")
sys.exit(1 if FAILURES else 0)
