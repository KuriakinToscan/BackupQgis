# -*- coding: utf-8 -*-
"""
Descoberta das conexões de provedores de conteúdo (WMS, WFS, XYZ, STAC, bancos de dados...)
salvas no QSettings.
"""

from .serialization import serialize_value

# Rótulos amigáveis para o sufixo de "connections-<tipo>"
PROVIDER_LABELS = {
    'wms': 'WMS / WMTS',
    'wfs': 'WFS / OGC API',
    'wcs': 'WCS',
    'xyz': 'XYZ Tiles',
    'vectortiles': 'Vector Tiles',
    'stac': 'STAC Catalog',
    'arcgis-mapserver': 'ArcGIS MapServer',
    'arcgis-featureserver': 'ArcGIS FeatureServer',
    'postgis': 'PostGIS',
    'spatialite': 'SpatiaLite',
    'oracle': 'Oracle',
    'hana': 'SAP HANA',
    'mssql': 'MS SQL Server'
}


def scan_server_connections(settings):
    """
    Escaneia o QSettings e agrupa conexões individuais por tipo de provedor e nome.
    """
    connections_map = {}

    for key in settings.allKeys():
        parts = key.split('/')
        provider_type = None
        conn_name = None

        for p_idx, part in enumerate(parts):
            if part.lower().startswith('connections-'):
                raw_type = part.lower().replace('connections-', '')
                provider_type = PROVIDER_LABELS.get(raw_type, raw_type.upper())
                if p_idx + 1 < len(parts):
                    conn_name = parts[p_idx + 1]
                break
            elif part.lower() in ['wms', 'wfs', 'wcs', 'xyz', 'stac', 'vectortiles'] and p_idx > 0 and parts[p_idx-1].lower() in ['qgis', 'plugins']:
                provider_type = PROVIDER_LABELS.get(part.lower(), part.upper())
                if p_idx + 1 < len(parts):
                    conn_name = parts[p_idx + 1]
                break

        if not provider_type and len(parts) >= 2:
            first_lower = parts[0].lower()
            if 'stac' in first_lower:
                provider_type = 'STAC Catalog'
                conn_name = parts[1] if len(parts) > 1 else parts[0]

        if provider_type and conn_name and conn_name.strip():
            map_key = (provider_type, conn_name)
            if map_key not in connections_map:
                connections_map[map_key] = {
                    'id': f"{provider_type}::{conn_name}",
                    'provider_type': provider_type,
                    'name': conn_name,
                    'details': '',
                    'keys': {}
                }

            serialized_val = serialize_value(settings.value(key))
            connections_map[map_key]['keys'][key] = serialized_val

            key_end = parts[-1].lower()
            if key_end in ['url', 'host', 'sqlitepath', 'database', 'dbname'] and not connections_map[map_key]['details']:
                connections_map[map_key]['details'] = str(settings.value(key))

    return list(connections_map.values())


def group_legacy_connections(server_connections):
    """
    Agrupa as chaves de conexão de backups antigos, que não têm a lista 'providers'.
    Usa as duas primeiras partes da chave como tipo e nome.
    """
    temp_map = {}
    for key in server_connections:
        parts = key.split('/')
        p_type = parts[0] if len(parts) > 0 else 'Servidor'
        c_name = parts[1] if len(parts) > 1 else 'Conexão'
        m_key = (p_type, c_name)
        if m_key not in temp_map:
            temp_map[m_key] = {'provider_type': p_type, 'name': c_name, 'details': '', 'keys': []}
        temp_map[m_key]['keys'].append(key)
    return list(temp_map.values())
