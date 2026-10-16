# -*- coding: utf-8 -*-
"""
Fluxo completo pela interface (sem QGIS): exportar -> carregar -> restaurar.

Usa o diálogo real com PyQt5 em modo offscreen, um qgis.utils falso e um
QSettings em arquivo temporário.
"""

import importlib
import json
import os
import sys
import types

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qgis.PyQt.QtCore import QCoreApplication, QSettings  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402


@pytest.fixture
def dialog_module(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])

    # Plugins instalados
    plugins_dir = tmp_path / 'plugins'
    for key, name in [('alpha', 'Alpha'), ('beta', 'Beta')]:
        (plugins_dir / key).mkdir(parents=True)
        (plugins_dir / key / 'metadata.txt').write_text(f'[general]\nname={name}\nversion=1.0\n')

    utils = types.ModuleType('qgis.utils')
    utils.plugin_paths = [str(plugins_dir)]
    monkeypatch.setitem(sys.modules, 'qgis.utils', utils)
    monkeypatch.setattr(sys.modules['qgis'], 'utils', utils, raising=False)

    # QSettings isolado em arquivo temporário
    QCoreApplication.setOrganizationName('BackupQgisTest')
    QCoreApplication.setApplicationName(f'dialog-{os.getpid()}-{tmp_path.name}')
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / 'settings'))

    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a, **k: pytest.fail(f'Erro exibido: {a[2]}'))

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent, package = os.path.split(root)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    module = importlib.import_module(f'{package}.backup_plugin_dialog')
    yield module
    app.processEvents()


def test_export_load_restore(dialog_module, tmp_path, monkeypatch):
    settings = QSettings()
    settings.setValue('PythonPlugins/alpha', True)
    settings.setValue('PythonPlugins/beta', False)
    settings.setValue('plugins/alpha/token', 'abc')
    settings.setValue('beta/option', 2)
    settings.setValue('qgis/connections-wms/Ortofoto/url', 'https://exemplo.org/wms')
    settings.sync()

    dlg = dialog_module.BackupPluginDialog(iface=None)
    assert dlg.export_table.rowCount() == 2
    assert dlg.export_providers_table.rowCount() == 1

    # Exporta tudo (beta inativo vem desmarcado por padrão; marcamos todos)
    dlg.select_all_export_plugins(True)
    dlg.select_all_export_configs(True)
    backup_path = str(tmp_path / 'backup.json')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (backup_path, ''))
    dlg.export_backup()

    data = json.load(open(backup_path, encoding='utf-8'))
    assert sorted(p['key'] for p in data['plugins']) == ['alpha', 'beta']
    assert 'plugins/alpha/token' in data['settings']
    assert list(data['server_connections']) == ['qgis/connections-wms/Ortofoto/url']

    # Limpa o perfil e restaura
    settings.clear()
    settings.sync()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (backup_path, ''))
    dlg.load_backup_file()
    assert dlg.import_table.rowCount() == 2
    assert dlg.import_providers_table.rowCount() == 1

    dlg.restore_backup()
    settings.sync()
    assert settings.value('plugins/alpha/token') == 'abc'
    assert settings.value('qgis/connections-wms/Ortofoto/url') == 'https://exemplo.org/wms'
    assert settings.value('PythonPlugins/beta', type=bool) is True
    dlg.close()
