# -*- coding: utf-8 -*-

from conftest import FakeSettings
from core.plugins import scan_installed_plugins, collect_plugin_settings, setting_belongs_to_plugins


def make_plugin(base, folder, metadata):
    plugin_dir = base / folder
    plugin_dir.mkdir()
    if metadata is not None:
        (plugin_dir / 'metadata.txt').write_text(metadata, encoding='utf-8')
    return plugin_dir


def test_scan_installed_plugins(tmp_path):
    make_plugin(tmp_path, 'alpha', '[general]\nname=Alpha\nversion=1.2\ndescription=Primeiro\n')
    make_plugin(tmp_path, 'beta', '[general]\nname=Beta\n')
    make_plugin(tmp_path, 'sem_metadata', None)
    make_plugin(tmp_path, '__pycache__', '[general]\nname=Ignorar\n')
    make_plugin(tmp_path, 'quebrado', 'sem seção')
    (tmp_path / 'arquivo.txt').write_text('x')

    warnings = []
    settings = FakeSettings({'PythonPlugins/alpha': 'true', 'PythonPlugins/beta': 'false'})
    plugins = scan_installed_plugins([str(tmp_path), '/caminho/inexistente'], settings, warn=warnings.append)

    by_key = {p['key']: p for p in plugins}
    assert sorted(by_key) == ['alpha', 'beta']
    assert by_key['alpha']['name'] == 'Alpha'
    assert by_key['alpha']['version'] == '1.2'
    assert by_key['alpha']['active'] is True
    assert by_key['beta']['version'] == 'Desconhecida'
    assert by_key['beta']['active'] is False
    assert len(warnings) == 1 and 'quebrado' in warnings[0]


def test_collect_plugin_settings():
    settings = FakeSettings({
        'PythonPlugins/myplug': 'true',
        'myplug/option': 1,
        'MyPlug/Other': 'x',
        'plugins/myplug/token': 'abc',
        'plugin_myplug/flag': True,
        'myplug_extra/value': 'não pertence',
        'outro/myplug/value': 'não pertence',
    })
    collected = collect_plugin_settings(settings, 'myplug')
    assert sorted(collected) == [
        'MyPlug/Other', 'PythonPlugins/myplug', 'myplug/option',
        'plugin_myplug/flag', 'plugins/myplug/token',
    ]
    assert collected['myplug/option'] == {'type': 'basic', 'value': 1}


def test_setting_belongs_to_plugins():
    assert setting_belongs_to_plugins('PythonPlugins/myplug', ['myplug'])
    assert setting_belongs_to_plugins('myplug/option', ['outro', 'myplug'])
    assert not setting_belongs_to_plugins('outro/option', ['myplug'])
