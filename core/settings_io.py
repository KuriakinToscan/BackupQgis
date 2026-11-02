# -*- coding: utf-8 -*-
"""
Serialização de valores do QSettings para JSON e leitura/gravação em lote.
"""

import base64
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from qgis.PyQt.QtCore import (QByteArray, QDate, QDateTime, QPoint, QRect, QSettings, QSize, Qt,
                              QTime)

try:  # PyQt6 pode não expor QVariant
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # pragma: no cover
    QVariant = None

SerializedValue = Dict[str, Any]


def serialize_value(val: Any, fallbacks: Optional[List[str]] = None, key: str = "") -> SerializedValue:
    """
    Converte um valor do QSettings (tipos Qt ou Python) em estrutura JSON.

    :param val: valor lido do QSettings.
    :param fallbacks: se informado, recebe ``key`` quando o tipo é desconhecido
        e o valor precisa ser gravado como texto (perda de tipo).
    :param key: chave de origem, usada apenas no relatório de fallback.
    :returns: ``{"type": <tipo>, "value": <valor JSON>}``.
    """
    if val is None:
        return {"type": "basic", "value": None}
    if _is_qvariant(val):
        # O QGIS grava campos vazios (ex.: service/authcfg das conexões PostGIS) como QVariant nulo.
        # Gravá-los como o texto "NULL" quebra a conexão restaurada.
        if val.isNull() or not val.isValid():
            return {"type": "null", "value": None}
        return serialize_value(val.value(), fallbacks, key)
    if isinstance(val, (bool, int, float, str)):
        return {"type": "basic", "value": val}
    if isinstance(val, (QByteArray, bytes, bytearray)):
        raw = bytes(val.data()) if isinstance(val, QByteArray) else bytes(val)
        return {"type": "bytearray", "value": base64.b64encode(raw).decode("ascii")}
    if isinstance(val, QDateTime):
        return {"type": "datetime", "value": val.toString("yyyy-MM-ddTHH:mm:ss.zzz")}
    if isinstance(val, QDate):
        return {"type": "date", "value": val.toString("yyyy-MM-dd")}
    if isinstance(val, QTime):
        return {"type": "time", "value": val.toString("HH:mm:ss.zzz")}
    if isinstance(val, QPoint):
        return {"type": "point", "value": [val.x(), val.y()]}
    if isinstance(val, QSize):
        return {"type": "size", "value": [val.width(), val.height()]}
    if isinstance(val, QRect):
        return {"type": "rect", "value": [val.x(), val.y(), val.width(), val.height()]}
    if isinstance(val, (list, tuple)):
        return {"type": "list", "value": [serialize_value(item, fallbacks, key) for item in val]}
    if isinstance(val, dict):
        return {"type": "dict", "value": {str(k): serialize_value(v, fallbacks, key) for k, v in val.items()}}

    if fallbacks is not None and key not in fallbacks:
        fallbacks.append(key)
    return {"type": "fallback", "value": str(val), "pytype": type(val).__name__}


def _is_qvariant(val: Any) -> bool:
    """``True`` para ``QVariant`` (PyQGIS devolve QVariant nulo para valores vazios)."""
    return QVariant is not None and isinstance(val, QVariant)


def deserialize_value(data: Any) -> Any:
    """
    Reverte :func:`serialize_value`.

    :param data: estrutura gravada no JSON.
    :returns: valor Python/Qt pronto para ``QSettings.setValue``.
    """
    if not isinstance(data, dict) or "type" not in data or "value" not in data:
        return data

    kind = data["type"]
    val = data["value"]
    try:
        if kind == "basic":
            return val
        if kind == "null" or (kind == "fallback" and data.get("pytype") == "QVariant" and val == "NULL"):
            return None  # o segundo caso corrige backups gerados pela 0.2.0
        if kind == "bytearray":
            return QByteArray(base64.b64decode(val.encode("ascii")))
        if kind == "datetime":
            parsed = QDateTime.fromString(val, "yyyy-MM-ddTHH:mm:ss.zzz")
            return parsed if parsed.isValid() else QDateTime.fromString(val, Qt.DateFormat.ISODate)
        if kind == "date":
            return QDate.fromString(val, "yyyy-MM-dd")
        if kind == "time":
            return QTime.fromString(val, "HH:mm:ss.zzz")
        if kind == "point":
            return QPoint(int(val[0]), int(val[1]))
        if kind == "size":
            return QSize(int(val[0]), int(val[1]))
        if kind == "rect":
            return QRect(int(val[0]), int(val[1]), int(val[2]), int(val[3]))
        if kind == "list":
            return [deserialize_value(item) for item in val]
        if kind == "dict":
            return {k: deserialize_value(v) for k, v in val.items()}
    except (TypeError, ValueError, IndexError, AttributeError):
        return None
    return val


def canonical(serialized: SerializedValue) -> str:
    """
    Representação textual estável de um valor serializado, para comparar
    valor local × valor do backup.
    """
    return json.dumps(serialized, sort_keys=True, ensure_ascii=False)


def read_values(keys: Iterable[str], settings: Optional[QSettings] = None,
                fallbacks: Optional[List[str]] = None) -> Dict[str, SerializedValue]:
    """
    Lê e serializa um conjunto de chaves do QSettings.

    :param keys: chaves a ler.
    :param settings: instância a usar (padrão: ``QSettings()`` do perfil ativo).
    :param fallbacks: lista que recebe chaves gravadas como texto por tipo desconhecido.
    """
    settings = settings or QSettings()
    return {key: serialize_value(settings.value(key), fallbacks, key) for key in keys}


def snapshot_keys(keys: Iterable[str], settings: Optional[QSettings] = None) -> Dict[str, Optional[SerializedValue]]:
    """
    Captura o estado atual das chaves antes de sobrescrevê-las.

    :returns: ``{chave: valor serializado}`` ou ``{chave: None}`` se a chave não existia.
    """
    settings = settings or QSettings()
    result: Dict[str, Optional[SerializedValue]] = {}
    for key in keys:
        result[key] = serialize_value(settings.value(key)) if settings.contains(key) else None
    return result


def write_values(values: Iterable[Tuple[str, Any]], settings: Optional[QSettings] = None) -> int:
    """
    Grava pares (chave, valor já desserializado) no QSettings e sincroniza.

    :returns: número de chaves gravadas.
    """
    settings = settings or QSettings()
    count = 0
    for key, value in values:
        settings.setValue(key, value)
        count += 1
    settings.sync()
    return count
