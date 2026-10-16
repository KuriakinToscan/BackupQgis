# -*- coding: utf-8 -*-
"""
Configuração dos testes.

Dentro do QGIS (ou na imagem Docker qgis/qgis) o módulo ``qgis.PyQt`` existe.
Fora dele, criamos um apelido para o PyQt5 para poder testar a camada ``core``.
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import qgis.PyQt.QtCore  # noqa: F401
except ImportError:
    from PyQt5 import QtCore, QtGui, QtWidgets

    qgis_mod = types.ModuleType('qgis')
    pyqt_mod = types.ModuleType('qgis.PyQt')
    pyqt_mod.QtCore = QtCore
    pyqt_mod.QtGui = QtGui
    pyqt_mod.QtWidgets = QtWidgets
    qgis_mod.PyQt = pyqt_mod
    sys.modules['qgis'] = qgis_mod
    sys.modules['qgis.PyQt'] = pyqt_mod
    sys.modules['qgis.PyQt.QtCore'] = QtCore
    sys.modules['qgis.PyQt.QtGui'] = QtGui
    sys.modules['qgis.PyQt.QtWidgets'] = QtWidgets


class FakeSettings:
    """
    Substituto mínimo do QSettings, baseado em dicionário.
    """
    def __init__(self, values=None):
        self.values = dict(values or {})

    def allKeys(self):
        return list(self.values.keys())

    def value(self, key, default=None, type=None):
        val = self.values.get(key, default)
        if type is not None and val is not None:
            if type is bool and isinstance(val, str):
                return val.lower() == 'true'
            return type(val)
        return val

    def setValue(self, key, value):
        self.values[key] = value
