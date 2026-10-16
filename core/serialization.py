# -*- coding: utf-8 -*-
"""
Serialização e deserialização de valores do QSettings para tipos JSON.
"""

import base64
from qgis.PyQt.QtCore import Qt, QByteArray, QDateTime, QDate, QTime, QPoint, QSize, QRect


def serialize_value(val):
    """
    Serializa valores do QSettings (incluindo tipos Qt e Python) para tipos padrão JSON.
    """
    if val is None:
        return {'type': 'basic', 'value': None}
    if isinstance(val, (int, float, bool, str)):
        return {'type': 'basic', 'value': val}
    if isinstance(val, (QByteArray, bytes, bytearray)):
        b_data = val.data() if isinstance(val, QByteArray) else val
        return {'type': 'bytearray', 'value': base64.b64encode(b_data).decode('utf-8')}
    if isinstance(val, QDateTime):
        return {'type': 'datetime', 'value': val.toString('yyyy-MM-ddTHH:mm:ss.zzz')}
    if isinstance(val, QDate):
        return {'type': 'date', 'value': val.toString('yyyy-MM-dd')}
    if isinstance(val, QTime):
        return {'type': 'time', 'value': val.toString('HH:mm:ss.zzz')}
    if isinstance(val, QPoint):
        return {'type': 'point', 'value': [val.x(), val.y()]}
    if isinstance(val, QSize):
        return {'type': 'size', 'value': [val.width(), val.height()]}
    if isinstance(val, QRect):
        return {'type': 'rect', 'value': [val.x(), val.y(), val.width(), val.height()]}
    if isinstance(val, (list, tuple)):
        return {'type': 'list', 'value': [serialize_value(item) for item in val]}
    if isinstance(val, dict):
        return {'type': 'dict', 'value': {str(k): serialize_value(v) for k, v in val.items()}}
    
    # Fallback para string se o tipo for desconhecido
    return {'type': 'fallback', 'value': str(val)}

def deserialize_value(data):
    """
    Reverte a serialização e retorna o tipo Qt correspondente.
    """
    if not isinstance(data, dict) or 'type' not in data or 'value' not in data:
        return data
    
    t = data['type']
    val = data['value']
    
    if t == 'basic':
        return val
    if t == 'bytearray':
        try:
            return QByteArray(base64.b64decode(val.encode('utf-8')))
        except Exception:
            return QByteArray()
    if t == 'datetime':
        dt = QDateTime.fromString(val, 'yyyy-MM-ddTHH:mm:ss.zzz')
        if not dt.isValid():
            dt = QDateTime.fromString(val, Qt.ISODate)
        return dt
    if t == 'date':
        return QDate.fromString(val, 'yyyy-MM-dd')
    if t == 'time':
        return QTime.fromString(val, 'HH:mm:ss.zzz')
    if t == 'point':
        return QPoint(val[0], val[1])
    if t == 'size':
        return QSize(val[0], val[1])
    if t == 'rect':
        return QRect(val[0], val[1], val[2], val[3])
    if t == 'list':
        return [deserialize_value(item) for item in val]
    if t == 'dict':
        return {k: deserialize_value(v) for k, v in val.items()}
    return val
