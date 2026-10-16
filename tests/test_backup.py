# -*- coding: utf-8 -*-

import json

import pytest

from conftest import FakeSettings
from core.backup import (REPOSITORIES_KEY, build_backup, write_backup, read_backup,
                         validate_backup, backup_providers, restore_backup)
from core.connections import scan_server_connections

SOURCE_SETTINGS = {
    'PythonPlugins/alpha': 'true',
    'alpha/option': 1,
    'plugins/alpha/token': 'abc',
    'PythonPlugins/beta': 'false',
    'beta/option': 2,
    'qgis/connections-wms/Ortofoto/url': 'https://exemplo.org/wms',
    'qgis/connections-xyz/OSM/url': 'https://tile.example/{z}/{x}/{y}.png',
}

PLUGINS = [
    {'key': 'alpha', 'name': 'Alpha', 'version': '1.0', 'active': True, 'path': '/x/alpha'},
    {'key': 'beta', 'name': 'Beta', 'version': '2.0', 'active': False, 'path': '/x/beta'},
]


def make_backup(plugin_keys=('alpha', 'beta'), config_keys=('alpha', 'beta'),
                provider_ids=(('WMS / WMTS', 'Ortofoto'), ('XYZ Tiles', 'OSM'))):
    settings = FakeSettings(SOURCE_SETTINGS)
    return build_backup(settings, PLUGINS, plugin_keys, config_keys,
                        scan_server_connections(settings), provider_ids, '2026-10-16 12:00:00')


def test_build_backup_respects_selection():
    data = make_backup(plugin_keys=['alpha'], config_keys=['beta'], provider_ids=[('XYZ Tiles', 'OSM')])

    assert [p['key'] for p in data['plugins']] == ['alpha']
    assert 'path' not in data['plugins'][0]
    assert sorted(data['settings']) == ['PythonPlugins/beta', 'beta/option']
    assert list(data['server_connections']) == ['qgis/connections-xyz/OSM/url']
    assert [p['name'] for p in data['providers']] == ['OSM']
    assert data['date'] == '2026-10-16 12:00:00'


def test_write_and_read_roundtrip(tmp_path):
    data = make_backup()
    path = tmp_path / 'backup.json'
    write_backup(str(path), data)
    assert read_backup(str(path)) == json.loads(json.dumps(data))


@pytest.mark.parametrize('content', [[], {'outra': 1}, 'texto'])
def test_validate_rejects_foreign_files(content):
    with pytest.raises(ValueError):
        validate_backup(content)


def test_restore_writes_only_selected_items():
    data = json.loads(json.dumps(make_backup()))
    target = FakeSettings()

    result = restore_backup(target, data, plugin_keys=['alpha'], config_plugin_keys=['alpha'],
                            provider_ids=[('WMS / WMTS', 'Ortofoto')], installed_plugin_keys=['beta'])

    assert result['plugins_to_install'] == ['alpha']
    assert result['plugins_enabled'] == ['alpha']
    assert result['settings_restored'] == 3
    assert result['provider_keys_restored'] == 1
    assert target.values == {
        'PythonPlugins/alpha': True,
        'alpha/option': 1,
        'plugins/alpha/token': 'abc',
        'qgis/connections-wms/Ortofoto/url': 'https://exemplo.org/wms',
    }


def test_restore_merges_repositories():
    target = FakeSettings({REPOSITORIES_KEY: {'local': 'https://a'}})
    data = {'plugins': [], 'repositories': {'remoto': 'https://b'}}
    restore_backup(target, data, [], [], [], [])
    assert target.values[REPOSITORIES_KEY] == {'local': 'https://a', 'remoto': 'https://b'}


def test_legacy_backup_connections_are_restorable():
    # Backups antigos não têm a lista 'providers': as linhas exibidas vêm de group_legacy_connections
    data = {'server_connections': {'wms/A/url': {'type': 'basic', 'value': 'https://a'},
                                   'wfs/B/url': {'type': 'basic', 'value': 'https://b'}}}
    assert sorted((p['provider_type'], p['name']) for p in backup_providers(data)) == [('wfs', 'B'), ('wms', 'A')]

    target = FakeSettings()
    result = restore_backup(target, data, [], [], [('wms', 'A')], [])
    assert result['provider_keys_restored'] == 1
    assert target.values == {'wms/A/url': 'https://a'}
