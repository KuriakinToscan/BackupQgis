# -*- coding: utf-8 -*-
import json
import os

import pytest

from core.backup_file import (BackupFormatError, build_backup_document, build_restore_plan, list_snapshots,
                              load_backup, load_snapshot, normalize_document, provider_id, save_snapshot,
                              write_json_atomic)


def _ser(v):
    return {"type": "basic", "value": v}


def _doc():
    return build_backup_document(
        plugin_version="0.2.0",
        source={"qgis_version": "3.40.0"},
        repositories={"app/plugin_repositories/X/url": _ser("https://x")},
        plugins=[{"key": "QuickOSM", "name": "QuickOSM", "version": "2.2", "active": True, "core": False}],
        plugin_settings={"QuickOSM": {"quickosm/a": _ser(1), "PythonPlugins/QuickOSM": _ser(True)}},
        providers=[{"provider_type": "PostGIS", "name": "ibama", "details": "db",
                    "keys": {"PostgreSQL/connections/ibama/host": _ser("db")}}],
        omitted_sensitive_keys=["PostgreSQL/connections/ibama/password"],
    )


def test_roundtrip_and_plan(tmp_path):
    path = tmp_path / "b.json"
    write_json_atomic(str(path), _doc())
    doc, warnings = load_backup(str(path))
    assert warnings == []
    plan = build_restore_plan(doc, ["QuickOSM"], [provider_id("PostGIS", "ibama")], include_repositories=True)
    assert set(plan) == {"quickosm/a", "PythonPlugins/QuickOSM", "PostgreSQL/connections/ibama/host",
                         "app/plugin_repositories/X/url"}
    assert build_restore_plan(doc, [], [], include_repositories=False) == {}


def test_atomic_write_leaves_no_temp_on_error(tmp_path):
    path = tmp_path / "b.json"
    with pytest.raises(TypeError):
        write_json_atomic(str(path), {"x": object()})
    assert not path.exists()
    assert os.listdir(tmp_path) == []


def test_legacy_document_is_normalized():
    legacy = {
        "generator": "QGIS Plugin Sync", "version": "0.1",
        "repositories": {},
        "plugins": [{"key": "QuickOSM", "name": "QuickOSM", "version": "2.2", "active": True}],
        "settings": {"quickosm/a": _ser(1), "PythonPlugins/QuickOSM": _ser(True), "plugins/Other/x": _ser(2)},
        "server_connections": {"qgis/connections-wms/IBGE/url": _ser("u"), "qgis/connections-wms/selected": _ser("IBGE")},
    }
    doc, warnings = normalize_document(legacy)
    assert doc["plugin_settings_keys"]["QuickOSM"] == ["PythonPlugins/QuickOSM", "quickosm/a"]
    assert doc["plugin_settings_keys"]["Other"] == ["plugins/Other/x"]
    assert doc["providers"][0]["id"] == "WMS / WMTS::IBGE"
    assert any("formato antigo" in w for w in warnings)
    assert any("não puderam ser associadas" in w for w in warnings)
    plan = build_restore_plan(doc, ["QuickOSM"], ["WMS / WMTS::IBGE"], include_repositories=False)
    assert set(plan) == {"quickosm/a", "PythonPlugins/QuickOSM", "qgis/connections-wms/IBGE/url"}


def test_invalid_and_future_documents():
    with pytest.raises(BackupFormatError):
        normalize_document([])
    with pytest.raises(BackupFormatError):
        normalize_document({"schema_version": 99, "plugins": []})


def test_invalid_json(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(BackupFormatError):
        load_backup(str(path))


def test_snapshots_retention_and_undo(tmp_path):
    for i in range(4):
        save_snapshot(str(tmp_path), {"a": _ser(i), "novo": None}, keep=2)
    snaps = list_snapshots(str(tmp_path))
    assert len(snaps) == 2
    to_set, to_remove = load_snapshot(snaps[0])
    assert to_set == {"a": _ser(3)}
    assert to_remove == ["novo"]
    json.loads(open(snaps[0], encoding="utf-8").read())
