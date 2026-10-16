# -*- coding: utf-8 -*-
"""
Teste de fumaça: o pacote do plugin e o diálogo importam sem erro.
"""

import importlib
import os
import sys


def test_plugin_package_imports():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent, package = os.path.split(root)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    module = importlib.import_module(f'{package}.backup_plugin_dialog')
    assert hasattr(module, 'BackupPluginDialog')
