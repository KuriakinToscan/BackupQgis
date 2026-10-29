# -*- coding: utf-8 -*-
"""
Configuração dos testes.

Coloca a raiz do repositório no ``sys.path`` (para ``import core``) e, quando
o PyQt5 está instalado mas o QGIS não, cria um módulo ``qgis.PyQt`` mínimo que
reexporta o PyQt5, permitindo testar a serialização fora do QGIS.
"""

import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _install_qgis_pyqt_shim() -> None:
    try:
        import qgis.PyQt  # noqa: F401  # QGIS real disponível
        return
    except ImportError:
        pass
    try:
        qtcore = importlib.import_module("PyQt5.QtCore")
    except ImportError:
        return
    qgis_mod = sys.modules.setdefault("qgis", types.ModuleType("qgis"))
    pyqt_mod = types.ModuleType("qgis.PyQt")
    pyqt_mod.QtCore = qtcore
    qgis_mod.PyQt = pyqt_mod
    sys.modules["qgis.PyQt"] = pyqt_mod
    sys.modules["qgis.PyQt.QtCore"] = qtcore


_install_qgis_pyqt_shim()
