# -*- coding: utf-8 -*-
import pytest

QtCore = pytest.importorskip("qgis.PyQt.QtCore")

from core.settings_io import canonical, deserialize_value, serialize_value  # noqa: E402


@pytest.mark.parametrize("value", [
    None, True, 3, 2.5, "texto", [1, "a"], {"k": [1, 2]},
    QtCore.QByteArray(b"\x00\x01binario"), QtCore.QDate(2026, 10, 28), QtCore.QTime(13, 45, 1, 250),
    QtCore.QDateTime(QtCore.QDate(2026, 10, 28), QtCore.QTime(13, 45, 1, 250)),
    QtCore.QPoint(3, 4), QtCore.QSize(800, 600), QtCore.QRect(1, 2, 3, 4),
])
def test_roundtrip(value):
    assert deserialize_value(serialize_value(value)) == value


def test_fallback_is_reported():
    class Weird:
        def __str__(self):
            return "weird"
    fallbacks = []
    ser = serialize_value([Weird()], fallbacks, "grupo/chave")
    assert fallbacks == ["grupo/chave"]
    assert ser["value"][0]["type"] == "fallback"


def test_canonical_is_order_independent():
    assert canonical(serialize_value({"a": 1, "b": 2})) == canonical(serialize_value({"b": 2, "a": 1}))


def test_corrupt_value_does_not_raise():
    assert deserialize_value({"type": "point", "value": ["x"]}) is None
