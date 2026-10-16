# -*- coding: utf-8 -*-

import json

import pytest
from qgis.PyQt.QtCore import QByteArray, QDateTime, QDate, QTime, QPoint, QSize, QRect

from core.serialization import serialize_value, deserialize_value


def roundtrip(value):
    # Passa pelo JSON para garantir que o formato intermediário é serializável
    return deserialize_value(json.loads(json.dumps(serialize_value(value))))


@pytest.mark.parametrize('value', [None, 0, 42, 3.5, True, False, '', 'texto com acentuação'])
def test_basic_values_roundtrip(value):
    assert roundtrip(value) == value


def test_bytes_become_qbytearray():
    assert roundtrip(b'\x00\x01abc') == QByteArray(b'\x00\x01abc')
    assert roundtrip(QByteArray(b'geometria')) == QByteArray(b'geometria')


def test_qt_types_roundtrip():
    dt = QDateTime(QDate(2026, 10, 16), QTime(13, 45, 7, 120))
    assert roundtrip(dt) == dt
    assert roundtrip(QDate(2024, 2, 29)) == QDate(2024, 2, 29)
    assert roundtrip(QTime(23, 59, 58, 1)) == QTime(23, 59, 58, 1)
    assert roundtrip(QPoint(-3, 7)) == QPoint(-3, 7)
    assert roundtrip(QSize(800, 600)) == QSize(800, 600)
    assert roundtrip(QRect(1, 2, 3, 4)) == QRect(1, 2, 3, 4)


def test_nested_containers_roundtrip():
    value = {'a': [1, 'x', {'b': QPoint(1, 2)}], 'c': None}
    assert roundtrip(value) == value


def test_unknown_type_falls_back_to_string():
    class Custom:
        def __str__(self):
            return 'custom'
    assert serialize_value(Custom()) == {'type': 'fallback', 'value': 'custom'}
    assert deserialize_value({'type': 'fallback', 'value': 'custom'}) == 'custom'


def test_invalid_payload_is_returned_untouched():
    assert deserialize_value('cru') == 'cru'
    assert deserialize_value({'sem_tipo': 1}) == {'sem_tipo': 1}


def test_corrupted_bytearray_returns_empty():
    assert deserialize_value({'type': 'bytearray', 'value': '###'}) == QByteArray()
