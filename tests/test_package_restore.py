# -*- coding: utf-8 -*-
import os
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest

from core.project_backup import run_project_backup
from core.project_inventory import inventory_project, read_project_xml
from core.project_package import PackageAborted, build_package, restore_snapshot, rewrite_project_xml

from test_projects_sync import _make_project


def _sources(xml_bytes):
    root = ET.fromstring(xml_bytes)
    return {l.findtext("layername"): l.findtext("datasource") for l in root.iter("maplayer")}


def _absolute_project(tmp_path):
    """Projeto com caminhos absolutos e dados fora da pasta do projeto."""
    proj, path = _make_project(tmp_path, qgz=True)
    other = tmp_path / "outros"
    other.mkdir()
    (other / "limite.geojson").write_text("{}", encoding="utf-8")
    xml = read_project_xml(path).decode("utf-8").replace(
        "</projectlayers>",
        f"<maplayer><layername>Limite</layername><provider>ogr</provider><datasource>{other / 'limite.geojson'}"
        "</datasource></maplayer></projectlayers>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mapa.qgs", xml)
        z.writestr("mapa.qgd", b"aux")
    return proj, path


@pytest.mark.parametrize("make_zip", [False, True])
def test_build_package_rewrites_paths(tmp_path, make_zip):
    _proj, path = _absolute_project(tmp_path)
    out = tmp_path / "pacotes"
    out.mkdir()
    result, report = build_package(inventory_project(path), str(out), make_zip, print, lambda: False,
                                   lambda d, t: None)
    if make_zip:
        assert result.endswith(".zip")
        shutil.unpack_archive(result, str(out / "x"))
        root = out / "x" / "mapa"
    else:
        root = out / "mapa"
    projects = list(root.rglob("mapa.qgz"))
    assert len(projects) == 1
    with zipfile.ZipFile(projects[0]) as z:
        assert "mapa.qgd" in z.namelist()
    # Move o pacote para outro lugar: os caminhos relativos precisam continuar válidos.
    moved = tmp_path / "outra_maquina"
    shutil.move(str(root), str(moved))
    new_project = next(moved.rglob("mapa.qgz"))
    inv = inventory_project(str(new_project))
    assert [m[0] for m in inv.missing] == ["Sumiu"]  # já faltava na origem
    srcs = _sources(read_project_xml(str(new_project)))
    assert srcs["Limite"].startswith("../") or srcs["Limite"].startswith("./")
    assert srcs["Áreas"].endswith("|layername=areas|subset=\"x\"=1")
    assert srcs["CSV"].startswith("file:")
    assert report.rewritten >= 5 and report.resources == 1


def test_package_refuses_existing(tmp_path):
    _proj, path = _make_project(tmp_path)
    out = tmp_path / "pacotes"
    (out / "mapa").mkdir(parents=True)
    with pytest.raises(PackageAborted):
        build_package(inventory_project(path), str(out), False, print, lambda: False, lambda d, t: None)


def test_rewrite_keeps_remote_and_memory(tmp_path):
    proj, path = _make_project(tmp_path)
    xml, report = rewrite_project_xml(read_project_xml(path), str(proj), str(proj), {})
    srcs = _sources(xml)
    assert "password='segredo'" in srcs["Banco"]  # remota intocada (o manifesto é que omite a senha)
    assert srcs["Temp"].startswith("Point?")
    assert "Rios" in report.unresolved


def _snapshot(tmp_path):
    proj, path = _absolute_project(tmp_path)
    hd = tmp_path / "hd"
    hd.mkdir()
    snap, _manifest = run_project_backup([inventory_project(path)], str(hd), {}, print, lambda: False,
                                         lambda d, t: None)
    return proj, path, snap


def test_restore_to_new_folder(tmp_path):
    proj, path, snap = _snapshot(tmp_path)
    target = tmp_path / "restaurado"
    target.mkdir()
    stats = restore_snapshot(snap, str(target), False, print, lambda: False, lambda d, t: None)
    assert stats["failed"] == 0 and stats["projects"] == 1 and stats["restored"] > 5
    shutil.rmtree(proj)  # a origem some: o projeto restaurado precisa abrir sozinho
    shutil.rmtree(tmp_path / "outros")
    restored = next(target.rglob("mapa.qgz"))
    inv = inventory_project(str(restored))
    assert [m[0] for m in inv.missing] == ["Sumiu"]


def test_restore_to_original_paths(tmp_path):
    proj, path, snap = _snapshot(tmp_path)
    (proj / "dados" / "pontos.csv").write_text("alterado", encoding="utf-8")
    os.remove(proj / "dados" / "rios.dbf")
    stats = restore_snapshot(snap, None, False, print, lambda: False, lambda d, t: None)
    assert stats["restored"] == 1 and stats["kept"] == 1  # dbf voltou; csv alterado mantido
    assert (proj / "dados" / "pontos.csv").read_text(encoding="utf-8") == "alterado"
    stats = restore_snapshot(snap, None, True, print, lambda: False, lambda d, t: None)
    assert stats["restored"] == 1
    assert (proj / "dados" / "pontos.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"
    assert list((proj / "dados").glob("pontos.antes-restauracao-*.csv"))


def test_restore_rejects_foreign_folder(tmp_path):
    with pytest.raises(PackageAborted):
        restore_snapshot(str(tmp_path), None, False, print, lambda: False, lambda d, t: None)
