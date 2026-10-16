# -*- coding: utf-8 -*-

from conftest import FakeSettings
from core.connections import scan_server_connections, group_legacy_connections


def by_id(connections):
    return {c['id']: c for c in connections}


def test_scan_classic_connection_keys():
    settings = FakeSettings({
        'qgis/connections-wms/Ortofoto/url': 'https://exemplo.org/wms',
        'qgis/connections-wms/Ortofoto/ignoreAxisOrientation': False,
        'PostgreSQL/connections-postgis/Banco/host': 'localhost',
        'PostgreSQL/connections-postgis/Banco/database': 'gis',
        'qgis/connections-xyz/OSM/url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        'qgis/outra/chave': 1,
    })
    conns = by_id(scan_server_connections(settings))

    assert sorted(conns) == ['PostGIS::Banco', 'WMS / WMTS::Ortofoto', 'XYZ Tiles::OSM']
    assert conns['WMS / WMTS::Ortofoto']['details'] == 'https://exemplo.org/wms'
    assert len(conns['WMS / WMTS::Ortofoto']['keys']) == 2
    assert conns['PostGIS::Banco']['details'] in ('localhost', 'gis')


def test_scan_plugin_and_stac_keys():
    settings = FakeSettings({
        'plugins/wfs/Servico/url': 'https://exemplo.org/wfs',
        'STACConnections/Catalogo/url': 'https://stac.exemplo.org',
    })
    conns = by_id(scan_server_connections(settings))
    assert sorted(conns) == ['STAC Catalog::Catalogo', 'WFS / OGC API::Servico']


def test_unknown_type_is_uppercased():
    conns = scan_server_connections(FakeSettings({'qgis/connections-foo/X/url': 'u'}))
    assert conns[0]['provider_type'] == 'FOO'


def test_group_legacy_connections():
    groups = group_legacy_connections({'wms/A/url': {}, 'wms/A/user': {}, 'wfs/B/url': {}, 'solo': {}})
    summary = sorted((g['provider_type'], g['name'], len(g['keys'])) for g in groups)
    assert summary == [('solo', 'Conexão', 1), ('wfs', 'B', 1), ('wms', 'A', 2)]
