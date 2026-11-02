# -*- coding: utf-8 -*-
import os

from core.destinations import default_dest, overlap_message, paths_overlap


def test_defaults_are_separate(tmp_path):
    a, b = default_dest("config", str(tmp_path)), default_dest("projects", str(tmp_path))
    assert a.endswith(os.path.join("BackupQgis", "configuracoes"))
    assert b.endswith(os.path.join("BackupQgis", "projetos"))
    assert not paths_overlap(a, b)


def test_overlap(tmp_path):
    base = str(tmp_path / "bk")
    assert paths_overlap(base, base + os.sep)
    assert paths_overlap(base, os.path.join(base, "projetos"))
    assert paths_overlap(os.path.join(base, "x"), base)
    assert not paths_overlap(base, base + "2")
    assert not paths_overlap("", base)
    assert overlap_message(base, base, "backup de projetos")
    assert overlap_message(base, base + "2", "backup de projetos") is None


def test_is_within(tmp_path):
    from core.destinations import is_within
    base = str(tmp_path / "p")
    assert is_within(os.path.join(base, "a"), base) and is_within(base, base)
    assert not is_within(str(tmp_path), base) and not is_within(base + "x", base) and not is_within("", base)
