# -*- coding: utf-8 -*-
import os
import zipfile

import pytest

from core.plugin_archive import (PluginArchiveError, archive_keys, archive_path_for, extract_plugin,
                                 write_plugins_archive)


def _plugin(root, key):
    path = root / key
    (path / "__pycache__").mkdir(parents=True)
    (path / "metadata.txt").write_text("[general]\nname=X\n")
    (path / "main.py").write_text("x = 1\n")
    (path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\0")
    (path / "old.pyc").write_bytes(b"\0")
    return str(path)


def test_archive_roundtrip(tmp_path):
    src = tmp_path / "plugins"
    a = _plugin(src, "alpha")
    empty = src / "sem_meta"
    empty.mkdir()
    zp = archive_path_for(str(tmp_path / "qgis-backup.json"))
    assert zp.endswith("qgis-backup.plugins.zip")
    msgs = []
    assert write_plugins_archive(zp, [("alpha", a), ("sem_meta", str(empty))], msgs.append) == ["alpha"]
    assert msgs and "sem_meta" in msgs[0]
    names = zipfile.ZipFile(zp).namelist()
    assert "alpha/main.py" in names and not any(n.endswith(".pyc") for n in names)
    assert archive_keys(zp) == ["alpha"]

    dest = tmp_path / "perfil"
    out = extract_plugin(zp, "alpha", str(dest))
    assert os.path.isfile(os.path.join(out, "main.py"))
    with pytest.raises(PluginArchiveError):
        extract_plugin(zp, "alpha", str(dest))  # não sobrescreve
    with pytest.raises(PluginArchiveError):
        extract_plugin(zp, "beta", str(dest))
    assert sorted(os.listdir(dest)) == ["alpha"]  # nenhuma pasta temporária sobrando


def test_extract_rejects_zip_slip(tmp_path):
    zp = tmp_path / "mal.plugins.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("evil/metadata.txt", "x")
        z.writestr("evil/../../fora.txt", "x")
    with pytest.raises(PluginArchiveError):
        extract_plugin(str(zp), "evil", str(tmp_path / "perfil"))
    assert not (tmp_path / "fora.txt").exists()
    assert os.listdir(tmp_path / "perfil") == []


def test_nothing_to_archive_writes_nothing(tmp_path):
    zp = str(tmp_path / "b.plugins.zip")
    assert write_plugins_archive(zp, [], lambda m: None) == []
    assert not os.path.exists(zp) and os.listdir(tmp_path) == []
