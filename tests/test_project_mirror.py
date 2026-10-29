# -*- coding: utf-8 -*-
import json
import os

import pytest

import core.project_mirror as pm
from core.project_inventory import InventoryFile, ProjectInventory, inventory_project, read_project_xml
from core.project_mirror import (MirrorAborted, apply_mirror_retention, bkp_relpath, find_mirrors, is_mirror,
                                 list_states, resolve_bkp_dir, restore_mirror, run_mirror_backup, state_files,
                                 verify_mirror)

QGS = """<qgis version="3.34.0">
 <projectlayers>
  <maplayer><id>a</id><layername>Pontos</layername><provider>delimitedtext</provider>
   <datasource>file:./dados/pontos.csv?type=csv&amp;xField=x</datasource></maplayer>
  <maplayer><id>b</id><layername>Limite</layername><provider>ogr</provider>
   <datasource>./dados/sub/limite.geojson</datasource></maplayer>
  <maplayer><id>c</id><layername>Externo</layername><provider>ogr</provider>
   <datasource>{ext}</datasource></maplayer>
 </projectlayers>
</qgis>"""


def _noop(*_a):
    return None


def _never():
    return False


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "trabalho" / "proj"
    (proj / "dados" / "sub").mkdir(parents=True)
    (proj / "dados" / "pontos.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (proj / "dados" / "sub" / "limite.geojson").write_text('{"type":"FeatureCollection","features":[]}',
                                                          encoding="utf-8")
    ext_dir = tmp_path / "base_comum"
    ext_dir.mkdir()
    ext = ext_dir / "municipios.geojson"
    ext.write_text('{"type":"FeatureCollection","features":[1]}', encoding="utf-8")
    qgs = proj / "mapa.qgs"
    qgs.write_text(QGS.format(ext=str(ext)), encoding="utf-8")
    dest = tmp_path / "destino"
    dest.mkdir()
    return proj, str(qgs), str(ext), str(dest)


@pytest.fixture
def stamps(monkeypatch):
    seq = iter(f"2026-10-29_10000{i}" for i in range(10))
    monkeypatch.setattr(pm, "_new_stamp", lambda _bkp: next(seq))


def _backup(qgs, dest, keep=0):
    return run_mirror_backup(inventory_project(qgs), dest, {"host": "teste"}, keep, _noop, _never, _noop)


def _bump(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 10))


def test_bkp_relpath(tmp_path):
    base = str(tmp_path / "proj")
    assert bkp_relpath(os.path.join(base, "dados", "a.shp"), base) == "dados/a.shp"
    ext = bkp_relpath(str(tmp_path / "outra" / "b.tif"), base)
    assert ext.startswith("_externos/") and ext.endswith("outra/b.tif") and "arquivos" not in ext
    # nome reservado dentro do projeto não pode se misturar às pastas de controle
    assert bkp_relpath(os.path.join(base, "_historico", "c.csv"), base).startswith("_externos/")
    assert bkp_relpath(os.path.join(base + "_x", "d.csv"), base).startswith("_externos/")


def test_resolve_bkp_dir_collision(tmp_path):
    dest = tmp_path / "d"
    (dest / "mapa_bkp").mkdir(parents=True)  # pasta alheia, sem marcador
    a = resolve_bkp_dir(str(dest), str(tmp_path / "x" / "mapa.qgz"))
    b = resolve_bkp_dir(str(dest), str(tmp_path / "y" / "mapa.qgz"))
    assert os.path.basename(a) == "mapa_bkp_2" and os.path.basename(b) == "mapa_bkp_3"
    assert resolve_bkp_dir(str(dest), str(tmp_path / "x" / "mapa.qgz")) == a
    assert resolve_bkp_dir(str(dest), str(tmp_path / "z" / "mapa.qgz"), create=False) is None
    assert is_mirror(a) and not is_mirror(str(dest / "mapa_bkp"))
    assert find_mirrors(str(dest)) == [a, b]


def test_first_backup_layout(project, stamps):
    proj, qgs, ext, dest = project
    bkp, res = _backup(qgs, dest)
    assert os.path.basename(bkp) == "mapa_bkp"
    assert sorted(os.listdir(dest)) == ["mapa_bkp"]
    assert os.path.isfile(os.path.join(bkp, "mapa.qgs"))
    assert os.path.isfile(os.path.join(bkp, "dados", "pontos.csv"))
    assert os.path.isfile(os.path.join(bkp, "dados", "sub", "limite.geojson"))
    ext_rel = bkp_relpath(ext, str(proj))
    assert os.path.isfile(os.path.join(bkp, *ext_rel.split("/")))
    assert res["copied"] == 4 and res["updated"] == 0 and not res["errors"]
    assert not os.path.exists(os.path.join(bkp, "_historico"))
    manifest = json.load(open(os.path.join(bkp, "_backupqgis", "manifesto.json"), encoding="utf-8"))
    assert manifest["complete"] and manifest["project_rel"] == "mapa.qgs"
    assert [s["stamp"] for s in list_states(bkp)] == ["2026-10-29_100000"]
    assert verify_mirror(bkp) == []


def test_incremental_history_and_restore_date(project, stamps, tmp_path):
    proj, qgs, _ext, dest = project
    bkp, _ = _backup(qgs, dest)
    csv = proj / "dados" / "pontos.csv"
    _bump(csv, "x,y\n1,2\n3,4\n")
    (proj / "dados" / "sub" / "limite.geojson").unlink()

    bkp2, res = _backup(qgs, dest)
    assert bkp2 == bkp
    assert res["updated"] == 1 and res["files_updated"] == ["dados/pontos.csv"]
    assert res["removed"] == 1 and res["files_removed"] == ["dados/sub/limite.geojson"]
    assert res["unchanged"] == 2 and res["copied"] == 0
    hist = os.path.join(bkp, "_historico", "2026-10-29_100001")
    assert open(os.path.join(hist, "dados", "pontos.csv"), encoding="utf-8").read() == "x,y\n1,2\n"
    assert os.path.isfile(os.path.join(hist, "dados", "sub", "limite.geojson"))
    assert open(os.path.join(bkp, "dados", "pontos.csv"), encoding="utf-8").read() == "x,y\n1,2\n3,4\n"
    assert not os.path.exists(os.path.join(bkp, "dados", "sub", "limite.geojson"))

    # terceiro backup sem mudanças: nada copiado, nenhum histórico novo
    _b, res3 = _backup(qgs, dest)
    assert res3["copied"] == res3["updated"] == res3["removed"] == 0
    assert sorted(os.listdir(os.path.join(bkp, "_historico"))) == ["2026-10-29_100001"]

    # estado da primeira data reconstruído a partir do histórico
    _state, files = state_files(bkp, "2026-10-29_100000")
    stored = {e["path"]: p for e, p in files}
    assert stored["dados/pontos.csv"].startswith(hist)
    assert stored["dados/sub/limite.geojson"].startswith(hist)
    assert verify_mirror(bkp, "2026-10-29_100000") == []
    assert verify_mirror(bkp) == []

    target = tmp_path / "restaurado"
    target.mkdir()
    stats = restore_mirror(bkp, "2026-10-29_100000", str(target), False, _noop, _never, _noop)
    assert stats["failed"] == 0 and stats["restored"] == 4 and stats["projects"] == 1
    assert (target / "dados" / "pontos.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"
    assert (target / "dados" / "sub" / "limite.geojson").is_file()
    xml = read_project_xml(str(target / "mapa.qgs")).decode("utf-8")
    assert "_externos" in xml.split("Externo")[1].split("</datasource>")[0]


def test_restore_in_place(project, stamps):
    proj, qgs, _ext, dest = project
    bkp, _ = _backup(qgs, dest)
    csv = proj / "dados" / "pontos.csv"
    csv.write_text("estragado", encoding="utf-8")
    stats = restore_mirror(bkp, None, None, False, _noop, _never, _noop)
    assert stats["kept"] == 1 and csv.read_text(encoding="utf-8") == "estragado"
    stats = restore_mirror(bkp, None, None, True, _noop, _never, _noop)
    assert stats["restored"] == 1 and stats["skipped"] == 3
    assert csv.read_text(encoding="utf-8") == "x,y\n1,2\n"
    assert any(n.startswith("pontos.antes-restauracao-") for n in os.listdir(proj / "dados"))


def test_retention_prunes_history(project, stamps):
    proj, qgs, _ext, dest = project
    csv = proj / "dados" / "pontos.csv"
    bkp, _ = _backup(qgs, dest)
    for i in range(3):
        _bump(csv, "x,y\n" + "9,9\n" * (i + 1))
        _backup(qgs, dest)
    assert len(list_states(bkp)) == 4
    assert len(os.listdir(os.path.join(bkp, "_historico"))) == 3
    removed = apply_mirror_retention(bkp, 2, _noop)
    assert removed == ["2026-10-29_100000", "2026-10-29_100001"]
    assert [s["stamp"] for s in list_states(bkp)] == ["2026-10-29_100002", "2026-10-29_100003"]
    # só sobra o histórico necessário para o estado mais antigo mantido
    assert os.listdir(os.path.join(bkp, "_historico")) == ["2026-10-29_100003"]
    assert verify_mirror(bkp, "2026-10-29_100002") == []
    with pytest.raises(MirrorAborted):
        state_files(bkp, "2026-10-29_100000")


def test_keep_applied_on_backup(project, stamps):
    proj, qgs, _ext, dest = project
    for i in range(3):
        _bump(proj / "dados" / "pontos.csv", "x\n" * (i + 2))
        bkp, res = _backup(qgs, dest, keep=2)
    assert res["pruned"] == ["2026-10-29_100000"]
    assert len(list_states(bkp)) == 2


def test_missing_destination_and_dest_inside_sources(project, tmp_path):
    proj, qgs, _ext, _dest = project
    with pytest.raises(MirrorAborted):
        _backup(qgs, str(tmp_path / "desconectado"))
    with pytest.raises(MirrorAborted):
        _backup(qgs, str(proj))


def test_cancel_keeps_previous_state(project, stamps):
    proj, qgs, _ext, dest = project
    bkp, _ = _backup(qgs, dest)
    _bump(proj / "dados" / "pontos.csv", "novo conteúdo")
    with pytest.raises(MirrorAborted):
        run_mirror_backup(inventory_project(qgs), dest, {}, 0, _noop, lambda: True, _noop)
    assert len(list_states(bkp)) == 1
    manifest = json.load(open(os.path.join(bkp, "_backupqgis", "manifesto.json"), encoding="utf-8"))
    assert manifest["complete"] is False
    assert open(os.path.join(bkp, "dados", "pontos.csv"), encoding="utf-8").read() == "x,y\n1,2\n"
    assert verify_mirror(bkp) == []


def test_copy_error_keeps_old_copy(project, stamps, monkeypatch):
    proj, qgs, _ext, dest = project
    bkp, _ = _backup(qgs, dest)
    csv = proj / "dados" / "pontos.csv"
    _bump(csv, "alterado")
    real = pm.copy_file

    def failing(src, dst, *a, **k):
        if src.endswith("pontos.csv"):
            raise OSError("disco cheio")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(pm, "copy_file", failing)
    _b, res = _backup(qgs, dest)
    assert res["errors"] and "disco cheio" in res["errors"][0]
    assert open(os.path.join(bkp, "dados", "pontos.csv"), encoding="utf-8").read() == "x,y\n1,2\n"
    assert verify_mirror(bkp) == []


def test_manual_inventory_unreadable_source(tmp_path, stamps):
    proj = tmp_path / "p"
    proj.mkdir()
    qgs = proj / "m.qgs"
    qgs.write_text("<qgis/>", encoding="utf-8")
    inv = ProjectInventory(str(qgs), files=[InventoryFile(str(qgs), "projeto"),
                                            InventoryFile(str(proj / "sumiu.csv"), "dados")])
    dest = tmp_path / "d"
    dest.mkdir()
    bkp, res = run_mirror_backup(inv, str(dest), {}, 0, _noop, _never, _noop)
    assert res["copied"] == 1 and len(res["errors"]) == 1
    assert [e["path"] for e, _p in state_files(bkp)[1]] == ["m.qgs"]


def test_backup_projects_report(project, stamps, tmp_path):
    from core.project_mirror import backup_projects
    from core.report import ERROR
    _proj, qgs, _ext, dest = project
    bad = tmp_path / "quebrado.qgs"  # não existe
    report, invs = backup_projects([qgs, str(bad)], dest, {}, 0, _noop, _never, _noop)
    names = [(r.name, r.status) for r in report.records]
    assert ("mapa.qgs: inventário", "ok") in names and ("mapa.qgs: cópia", "ok") in names
    assert ("quebrado.qgs: inventário", ERROR) in names
    assert len(invs) == 1 and report.status == ERROR
    assert "1 com erro" in report.summary()


def test_backup_projects_dest_missing_stops(project, tmp_path):
    from core.project_mirror import backup_projects
    _proj, qgs, _ext, _dest = project
    report, _ = backup_projects([qgs, qgs], str(tmp_path / "off"), {}, 0, _noop, _never, _noop)
    assert [r.name for r in report.records].count("mapa.qgs: cópia") == 1
