# -*- coding: utf-8 -*-
from core.keys import (SettingsIndex, group_connection_keys, is_sensitive_key, parse_connection_key,
                       plugin_key_matcher, remap_path_prefix)


def test_ows_legacy_connections():
    assert parse_connection_key("qgis/connections-wms/IBGE/url") == ("wms", "IBGE")
    assert parse_connection_key("qgis/connections-xyz/Google Satellite/url") == ("xyz", "Google Satellite")
    assert parse_connection_key("qgis/connections-arcgisfeatureserver/SIGEF/url") == ("arcgisfeatureserver", "SIGEF")


def test_group_level_values_are_not_connections():
    assert parse_connection_key("qgis/connections-wms/selected") is None
    assert parse_connection_key("PostgreSQL/connections/selected") is None


def test_database_providers():
    assert parse_connection_key("PostgreSQL/connections/ibama/host") == ("postgresql", "ibama")
    assert parse_connection_key("SpatiaLite/connections/base.sqlite/sqlitepath") == ("spatialite", "base.sqlite")
    assert parse_connection_key("MSSQL/connections/srv/database") == ("mssql", "srv")
    assert parse_connection_key("providers/ogr/GPKG/connections/dados/path") == ("gpkg", "dados")


def test_new_settings_tree():
    assert parse_connection_key("connections/ows/items/wms/connections/items/IBGE/url") == ("wms", "IBGE")
    assert parse_connection_key("connections/xyz/items/OSM/url") == ("xyz", "OSM")
    assert parse_connection_key("connections/xyz/items/OSM") is None


def test_unrelated_keys_are_ignored():
    assert parse_connection_key("MyPlugin/connections/x/y") is None
    assert parse_connection_key("stacking/whatever/value") is None
    assert parse_connection_key("Processing/Configuration/FOLDER") is None


def test_legacy_wms_credentials_merge_with_connection():
    groups = group_connection_keys([
        "qgis/connections-wms/IBGE/url",
        "qgis/WMS/IBGE/username",
        "qgis/WMS/IBGE/password",
        "PostgreSQL/connections/ibama/host",
    ])
    assert groups[("WMS / WMTS", "IBGE")] == [
        "qgis/WMS/IBGE/password", "qgis/WMS/IBGE/username", "qgis/connections-wms/IBGE/url"]
    assert ("PostGIS", "ibama") in groups


def test_sensitive_keys():
    assert is_sensitive_key("PostgreSQL/connections/ibama/password")
    assert is_sensitive_key("qgis/WMS/IBGE/Password")
    assert not is_sensitive_key("qgis/WMS/IBGE/username")
    assert not is_sensitive_key("PostgreSQL/connections/ibama/savePassword")


def test_plugin_matcher_is_segment_based_and_case_insensitive():
    match = plugin_key_matcher("QuickOSM")
    assert match("quickosm/lastQuery")
    assert match("PythonPlugins/QuickOSM")
    assert match("plugins/QuickOSM/x")
    assert match("plugin_quickosm/x")
    assert not match("PythonPlugins/QuickOSMExtra")
    assert not match("QuickOSMExtra/x")
    assert not match("QuickOSM")  # valor solto no topo não é configuração do plugin


def test_settings_index_plugin_and_repositories():
    index = SettingsIndex([
        "QuickOSM/a", "quickosm/b", "PythonPlugins/QuickOSM", "PythonPlugins/Other",
        "app/plugin_repositories/Oficial/url", "app/plugin_repositories/Oficial/enabled", "app/other",
    ])
    assert index.plugin_keys("QuickOSM") == ["PythonPlugins/QuickOSM", "QuickOSM/a", "quickosm/b"]
    assert index.repository_keys() == ["app/plugin_repositories/Oficial/enabled", "app/plugin_repositories/Oficial/url"]


def test_remap_path_prefix():
    assert remap_path_prefix("C:/Users/ana/sig/x.gpkg", "C:/Users/ana", "/home/ana") == "/home/ana/sig/x.gpkg"
    assert remap_path_prefix("C:\\Users\\ana\\x", "C:/Users/ana", "/home/ana") == "/home/ana\\x"
    assert remap_path_prefix(["C:/Users/ana/a", 3], "C:/Users/ana", "/h") == ["/h/a", 3]
    assert remap_path_prefix("D:/x", "C:/Users/ana", "/h") == "D:/x"
    assert remap_path_prefix(5, "a", "b") == 5
