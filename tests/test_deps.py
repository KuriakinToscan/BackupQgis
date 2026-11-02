# -*- coding: utf-8 -*-
from core.deps import (is_distribution_installed, parse_plugin_dependencies, parse_requirement_name,
                       parse_requirements_text, python_interpreter_candidates, read_plugin_dependencies)


def test_parse_requirement_name_variants():
    assert parse_requirement_name("requests>=2.20") == "requests"
    assert parse_requirement_name("rasterio[s3] ~= 1.3 ; python_version >= '3.9'") == "rasterio"
    assert parse_requirement_name("numpy!=1.24.0") == "numpy"
    assert parse_requirement_name("pkg @ https://example.com/pkg.whl") == "pkg"
    assert parse_requirement_name("  # comentário") is None
    assert parse_requirement_name("-r outros.txt") is None
    assert parse_requirement_name("--index-url https://x") is None
    assert parse_requirement_name("git+https://github.com/a/b.git") is None
    assert parse_requirement_name("./local_pkg") is None


def test_parse_requirements_text_dedup():
    assert parse_requirements_text("a==1\nb\n\na>=2\n") == ["a", "b"]


def test_plugin_dependencies_are_names():
    assert parse_plugin_dependencies("QuickOSM==2.2, MetaSearch Catalog Client>=0.3,  ") == [
        "QuickOSM", "MetaSearch Catalog Client"]


def test_read_plugin_dependencies(tmp_path):
    (tmp_path / "metadata.txt").write_text(
        "[general]\nname=X\nplugin_dependencies=Dep Plugin==1.0\npython_dependencies=geopandas>=0.14, shapely\n",
        encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("shapely\nrasterio[s3]\n", encoding="utf-8")
    deps = read_plugin_dependencies(str(tmp_path))
    assert deps["qgis"] == ["Dep Plugin"]
    assert deps["python"] == ["geopandas", "shapely", "rasterio"]
    assert deps["warnings"] == []


def test_is_distribution_installed():
    assert is_distribution_installed("pytest")
    assert not is_distribution_installed("pacote-que-nao-existe-xyz")


def test_interpreter_candidates_skip_qgis_binary_windows():
    cands = python_interpreter_candidates(
        executable="C:\\OSGeo4W\\bin\\qgis-bin.exe",
        prefixes=["C:\\OSGeo4W\\apps\\Python312"], version=(3, 12), windows=True)
    assert "C:\\OSGeo4W\\bin\\qgis-bin.exe" not in cands
    assert cands[0] == "C:\\OSGeo4W\\apps\\Python312\\python.exe"


def test_interpreter_candidates_posix():
    cands = python_interpreter_candidates(executable="/usr/bin/qgis", prefixes=["/usr"], version=(3, 12),
                                          windows=False)
    assert cands[0] == "/usr/bin/python3.12"
    assert "/usr/bin/qgis" not in cands
