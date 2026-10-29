# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import time
import zipfile

import pytest

from core.file_copy import copy_file
from core.project_backup import (BackupAborted, apply_retention, list_own_snapshots, mirror_relpath,
                                 run_project_backup, verify_snapshot)
from core.project_inventory import datasource_to_path, find_projects, inventory_project, sanitize_uri
from core.sync import (SyncAborted, build_plan, check_target, execute_plan, guard_plan, init_marker,
                       scan_tree)

QGS = """<qgis version="3.40.0">
 <projectlayers>
  <maplayer><id>a</id><layername>Rios</layername><provider>ogr</provider>
   <datasource>./dados/rios.shp</datasource></maplayer>
  <maplayer><id>b</id><layername>Áreas</layername><provider>ogr</provider>
   <datasource>./dados/base.gpkg|layername=areas|subset="x"=1</datasource></maplayer>
  <maplayer><id>c</id><layername>Orto</layername><provider>gdal</provider>
   <datasource>./raster/orto.vrt</datasource></maplayer>
  <maplayer><id>d</id><layername>Banco</layername><provider>postgres</provider>
   <datasource>dbname='sig' host=db user='ana' password='segredo' table="public"."t"</datasource></maplayer>
  <maplayer><id>e</id><layername>Temp</layername><provider>memory</provider>
   <datasource>Point?crs=EPSG:4674</datasource></maplayer>
  <maplayer><id>f</id><layername>Sumiu</layername><provider>ogr</provider>
   <datasource>./dados/nao_existe.shp</datasource></maplayer>
  <maplayer><id>g</id><layername>CSV</layername><provider>delimitedtext</provider>
   <datasource>file:./dados/pontos.csv?type=csv&amp;xField=x</datasource></maplayer>
 </projectlayers>
 <symbol><layer class="SvgMarker"><Option type="Map"><Option name="name" value="./simbolos/arvore.svg" type="QString"/></Option></layer></symbol>
</qgis>"""


def _make_project(tmp_path, qgz=False):
    proj = tmp_path / "proj"
    (proj / "dados").mkdir(parents=True)
    (proj / "raster").mkdir()
    (proj / "simbolos").mkdir()
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        (proj / "dados" / f"rios{ext}").write_bytes(b"x" * 10)
    con = sqlite3.connect(proj / "dados" / "base.gpkg")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table t(a)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    (proj / "raster" / "orto.tif").write_bytes(b"tif" * 100)
    (proj / "raster" / "orto.tif.ovr").write_bytes(b"ovr")
    (proj / "raster" / "orto.vrt").write_text(
        '<VRTDataset><VRTRasterBand><SimpleSource><SourceFilename relativeToVRT="1">orto.tif</SourceFilename>'
        '</SimpleSource></VRTRasterBand></VRTDataset>', encoding="utf-8")
    (proj / "dados" / "pontos.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (proj / "simbolos" / "arvore.svg").write_text("<svg/>", encoding="utf-8")
    if qgz:
        path = proj / "mapa.qgz"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mapa.qgs", QGS)
            z.writestr("mapa.qgd", b"aux")
    else:
        path = proj / "mapa.qgs"
        path.write_text(QGS, encoding="utf-8")
    return proj, str(path)


@pytest.mark.parametrize("qgz", [False, True])
def test_inventory(tmp_path, qgz):
    proj, path = _make_project(tmp_path, qgz)
    inv = inventory_project(path)
    names = sorted(os.path.relpath(f.path, proj).replace("\\", "/") for f in inv.files)
    assert names == sorted([
        os.path.basename(path), "dados/rios.shp", "dados/rios.shx", "dados/rios.dbf", "dados/rios.prj",
        "dados/rios.cpg", "dados/base.gpkg", "raster/orto.vrt", "raster/orto.tif", "raster/orto.tif.ovr",
        "dados/pontos.csv", "simbolos/arvore.svg"])
    assert inv.remote[0][0] == "Banco" and "segredo" not in inv.remote[0][2]
    assert inv.memory == ["Temp"]
    assert [m[0] for m in inv.missing] == ["Sumiu"]
    assert inv.warnings == []


def test_datasource_variants(tmp_path):
    f = tmp_path / "a.gpkg"
    f.write_bytes(b"")
    base = str(tmp_path)
    assert datasource_to_path("gdal", f"GPKG:{f}:tabela", base) == str(f)
    assert datasource_to_path("ogr", "/vsizip/./dados.zip/x.shp", base) == os.path.join(base, "dados.zip")
    assert datasource_to_path("spatialite", "dbname='./b.sqlite' table=\"t\"", base) == os.path.join(base, "b.sqlite")
    assert datasource_to_path("delimitedtext", "file:///C:/x/p.csv?type=csv", base).replace("\\", "/").endswith("C:/x/p.csv")
    assert datasource_to_path("ogr", "https://x/y.geojson", base) is None
    assert sanitize_uri("host=a password='x y' user=b") == "host=a password=*** user=b"


def test_find_projects(tmp_path):
    _make_project(tmp_path)
    assert [os.path.basename(p) for p in find_projects(str(tmp_path))] == ["mapa.qgs"]


def test_mirror_relpath():
    assert mirror_relpath("C:\\dados\\a.shp") == "arquivos/C/dados/a.shp"
    assert mirror_relpath("\\\\nas\\sig\\a.tif") == "arquivos/UNC/nas/sig/a.tif"
    assert mirror_relpath("/home/ana/a.gpkg") == "arquivos/home/ana/a.gpkg"


def test_project_backup_and_retention(tmp_path):
    proj, path = _make_project(tmp_path, qgz=True)
    dest = tmp_path / "hd"
    dest.mkdir()
    inv = inventory_project(path)
    # GeoPackage aberto com transação pendente no WAL
    con = sqlite3.connect(proj / "dados" / "base.gpkg")
    con.execute("insert into t values (2)")
    con.commit()
    snaps = []
    for _ in range(3):
        snap, manifest = run_project_backup([inv], str(dest), {"os": "teste"}, print, lambda: False, lambda d, t: None)
        snaps.append(snap)
        time.sleep(0.01)
    con.close()
    assert manifest["errors"] == []
    assert verify_snapshot(snap) == []
    gpkg = os.path.join(snap, *mirror_relpath(str(proj / "dados" / "base.gpkg")).split("/"))
    check = sqlite3.connect(gpkg)
    assert check.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert check.execute("select count(*) from t").fetchone()[0] == 2
    check.close()
    assert "segredo" not in json.dumps(manifest)
    (dest / "2000-01-01_000000").mkdir()  # pasta alheia, sem manifesto: não pode ser apagada
    removed = apply_retention(str(dest), 1, print)
    assert len(removed) == 2 and list_own_snapshots(str(dest)) == [snaps[-1]]
    assert (dest / "2000-01-01_000000").exists()


def test_backup_aborts_without_destination(tmp_path):
    _proj, path = _make_project(tmp_path)
    with pytest.raises(BackupAborted):
        run_project_backup([inventory_project(path)], str(tmp_path / "desconectado"), {}, print,
                           lambda: False, lambda d, t: None)
    assert not (tmp_path / "desconectado").exists()


def test_backup_cancel_leaves_nothing(tmp_path):
    _proj, path = _make_project(tmp_path)
    dest = tmp_path / "hd"
    dest.mkdir()
    with pytest.raises(BackupAborted):
        run_project_backup([inventory_project(path)], str(dest), {}, print, lambda: True, lambda d, t: None)
    assert os.listdir(dest) == []


def _write(path, text, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))


def test_sync_additive_and_mirror(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.txt", "a")
    _write(src / "sub" / "b.txt", "b")
    _write(src / "base.gpkg-wal", "wal")
    dst.mkdir()
    target = init_marker(str(dst))
    check_target(str(dst), target)

    plan = build_plan(str(src), str(dst), scan_tree(str(src)), scan_tree(str(dst)), "additive")
    assert plan.copy_new == ["a.txt", "sub/b.txt"]
    guard_plan(plan)
    stats = execute_plan(plan, print, lambda: False, lambda d, t: None)
    assert stats["copied"] == 2
    # idempotência
    plan2 = build_plan(str(src), str(dst), scan_tree(str(src)), scan_tree(str(dst)), "additive")
    assert plan2.is_empty

    # alteração e remoção na origem
    _write(src / "a.txt", "aa", mtime=time.time() + 10)
    os.remove(src / "sub" / "b.txt")
    plan3 = build_plan(str(src), str(dst), scan_tree(str(src)), scan_tree(str(dst)), "mirror")
    assert plan3.update == ["a.txt"] and plan3.delete == ["sub/b.txt"]
    with pytest.raises(SyncAborted):
        guard_plan(plan3)  # 1 de 2 arquivos = 50% > 20%
    guard_plan(plan3, force=True)
    stats = execute_plan(plan3, print, lambda: False, lambda d, t: None)
    assert stats == {"copied": 0, "updated": 1, "trashed": 1, "failed": 0}
    assert (dst / "a.txt").read_text(encoding="utf-8") == "aa"
    trash = list((dst / ".backupqgis_trash").rglob("b.txt"))
    assert len(trash) == 1


def test_sync_guards(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    with pytest.raises(SyncAborted):
        check_target(str(tmp_path / "hd_desligado"), "x")
    dst.mkdir()
    with pytest.raises(SyncAborted):
        check_target(str(dst), "x")  # sem marcador
    init_marker(str(dst))
    with pytest.raises(SyncAborted):
        check_target(str(dst), "outro-uuid")
    plan = build_plan(str(src), str(dst), scan_tree(str(src)), scan_tree(str(dst)), "mirror")
    with pytest.raises(SyncAborted):
        guard_plan(plan)  # origem vazia


def test_dest_newer_is_not_overwritten(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    now = time.time()
    _write(src / "a.txt", "velho", mtime=now - 100)
    _write(dst / "a.txt", "novo", mtime=now)
    plan = build_plan(str(src), str(dst), scan_tree(str(src)), scan_tree(str(dst)), "additive")
    assert plan.dest_newer == ["a.txt"] and plan.is_empty


def test_copy_preserves_mtime_and_cancel(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"0" * 10)
    os.utime(src, (1000000000, 1000000000))
    copy_file(str(src), str(tmp_path / "out" / "a.bin"))
    assert int(os.stat(tmp_path / "out" / "a.bin").st_mtime) == 1000000000
    from core.file_copy import CopyCancelled
    with pytest.raises(CopyCancelled):
        copy_file(str(src), str(tmp_path / "out2" / "a.bin"), is_canceled=lambda: True)
    assert os.listdir(tmp_path / "out2") == []


def test_bidirectional_sync(tmp_path):
    from core.sync import (build_bidirectional_plan, build_state, execute_bidirectional, guard_bidirectional,
                           load_state, save_state)
    a, b = tmp_path / "a", tmp_path / "b"
    now = time.time()
    _write(a / "comum.txt", "igual", mtime=now - 50)
    _write(b / "comum.txt", "igual", mtime=now - 40)  # mesmo conteúdo, mtime diferente
    _write(a / "so_a.txt", "a")
    _write(b / "so_b.txt", "b")

    def same(rel):
        return (a / rel).read_bytes() == (b / rel).read_bytes()

    plan = build_bidirectional_plan(str(a), str(b), scan_tree(str(a)), scan_tree(str(b)), {}, same_content=same)
    assert plan.a_to_b == ["so_a.txt"] and plan.b_to_a == ["so_b.txt"] and not plan.conflicts
    guard_bidirectional(plan)
    execute_bidirectional(plan, print, lambda: False, lambda d, t: None)
    state_path = tmp_path / "estado.json"
    save_state(str(state_path), build_state(scan_tree(str(a)), scan_tree(str(b))))
    state = load_state(str(state_path))
    assert set(state) == {"so_a.txt", "so_b.txt"}  # comum.txt tem mtime diferente: fica fora do estado

    # alteração em B, remoção em A, conflito nos dois lados
    _write(b / "so_a.txt", "a2", mtime=now + 20)
    os.remove(a / "so_b.txt")
    plan = build_bidirectional_plan(str(a), str(b), scan_tree(str(a)), scan_tree(str(b)), state)
    assert plan.b_to_a == ["so_a.txt"] and plan.delete_b == ["so_b.txt"]
    assert plan.conflicts == [("comum.txt", "b")]  # sem estado e diferente: conflito, B mais recente
    guard_bidirectional(plan, force=True)
    stats = execute_bidirectional(plan, print, lambda: False, lambda d, t: None)
    assert stats["b_to_a"] == 1 and stats["trashed"] == 1 and stats["conflicts"] == 1
    assert (a / "so_a.txt").read_text(encoding="utf-8") == "a2"
    assert not (b / "so_b.txt").exists() and list((b / ".backupqgis_trash").rglob("so_b.txt"))
    conflict_copies = list(a.glob("comum (conflito origem *).txt"))
    assert len(conflict_copies) == 1 and list(b.glob("comum (conflito origem *).txt"))


def test_bidirectional_guard_empty_side(tmp_path):
    from core.sync import build_bidirectional_plan, guard_bidirectional
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a / "x.txt", "x")
    b.mkdir()
    state = {"x.txt": [1, 0, 1, 0]}
    plan = build_bidirectional_plan(str(a), str(b), scan_tree(str(a)), scan_tree(str(b)), state)
    with pytest.raises(SyncAborted):
        guard_bidirectional(plan)  # B vazio depois de já ter sincronizado: HD trocado/vazio
