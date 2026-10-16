# -*- coding: utf-8 -*-
"""
Montagem, leitura e aplicação do arquivo de backup (JSON).

As funções recebem listas de chaves já selecionadas e um objeto compatível
com QSettings; não dependem da interface.
"""

import json

from .serialization import deserialize_value
from .plugins import activation_key, collect_plugin_settings, setting_belongs_to_plugins
from .connections import group_legacy_connections

GENERATOR = 'QGIS Plugin Sync'
FORMAT_VERSION = '0.1'

# Atenção: o QGIS guarda os repositórios em "app/plugin_repositories" (ver AUDITORIA.md, B3)
REPOSITORIES_KEY = "pythonPlugins/pluginRepositories"


def provider_id(provider):
    """
    Identificador de uma conexão: (tipo de provedor, nome).
    """
    return (provider.get('provider_type'), provider.get('name'))


def build_backup(settings, plugins, plugin_keys, config_plugin_keys, providers, provider_ids, date):
    """
    Monta o dicionário do backup.

    plugins: plugins instalados (scan_installed_plugins).
    plugin_keys: chaves dos plugins a incluir na lista de plugins.
    config_plugin_keys: chaves dos plugins cujas configurações serão exportadas.
    providers: conexões encontradas (scan_server_connections).
    provider_ids: conexões selecionadas, como pares (tipo, nome).
    date: data/hora do backup, já formatada.
    """
    plugin_keys = set(plugin_keys)
    provider_ids = set(provider_ids)

    selected_plugins = [
        {
            'key': p['key'],
            'name': p['name'],
            'version': p['version'],
            'active': p['active']
        }
        for p in plugins if p['key'] in plugin_keys
    ]

    settings_to_backup = {}
    for plugin_key in config_plugin_keys:
        if plugin_key:
            settings_to_backup.update(collect_plugin_settings(settings, plugin_key))

    server_connections = {}
    exported_providers = []
    for prov in providers:
        if provider_id(prov) in provider_ids:
            server_connections.update(prov['keys'])
            exported_providers.append({
                'provider_type': prov['provider_type'],
                'name': prov['name'],
                'details': prov['details'],
                'keys': list(prov['keys'].keys())
            })

    return {
        'generator': GENERATOR,
        'version': FORMAT_VERSION,
        'date': date,
        'repositories': settings.value(REPOSITORIES_KEY, {}),
        'plugins': selected_plugins,
        'settings': settings_to_backup,
        'server_connections': server_connections,
        'providers': exported_providers
    }


def write_backup(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def validate_backup(data):
    """
    Levanta ValueError se o conteúdo não parecer um backup deste plugin.
    """
    if not isinstance(data, dict) or ('plugins' not in data and 'server_connections' not in data):
        raise ValueError("Arquivo de backup inválido ou incompatível.")


def read_backup(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    validate_backup(data)
    return data


def backup_providers(data):
    """
    Conexões contidas no backup. Backups antigos, sem a lista 'providers',
    são agrupados a partir das chaves de 'server_connections'.
    """
    providers = data.get('providers', [])
    if not providers and 'server_connections' in data:
        providers = group_legacy_connections(data['server_connections'])
    return providers


def restore_backup(settings, data, plugin_keys, config_plugin_keys, provider_ids,
                   installed_plugin_keys, log=None):
    """
    Grava no QSettings os itens selecionados do backup.

    plugin_keys: plugins a ativar (e instalar, se ausentes).
    config_plugin_keys: plugins cujas configurações serão restauradas.
    provider_ids: conexões a restaurar, como pares (tipo, nome).
    installed_plugin_keys: plugins já instalados localmente.

    Retorna um dict com o resumo. A instalação dos plugins ausentes
    ('plugins_to_install') fica a cargo de quem chama.
    """
    log = log or (lambda message: None)
    installed_plugin_keys = set(installed_plugin_keys)
    provider_ids = set(provider_ids)

    # 1. Adicionar repositórios customizados
    backup_repos = data.get('repositories', {})
    if backup_repos:
        log("Restaurando repositórios customizados...")
        current_repos = settings.value(REPOSITORIES_KEY, {}, type=dict)
        current_repos.update(backup_repos)
        settings.setValue(REPOSITORIES_KEY, current_repos)

    # 2. Plugins a instalar e ativar
    plugins_to_enable = list(plugin_keys)
    plugins_to_install = [key for key in plugins_to_enable if key not in installed_plugin_keys]

    # 3. Restaurar configurações dos plugins selecionados
    restored_settings = 0
    if 'settings' in data and config_plugin_keys:
        log("Restaurando configurações dos plugins selecionados...")
        for key, val_serialized in data['settings'].items():
            if setting_belongs_to_plugins(key, config_plugin_keys):
                settings.setValue(key, deserialize_value(val_serialized))
                restored_settings += 1

    # 4. Restaurar provedores de conteúdo selecionados
    restored_providers = 0
    server_conns = data.get('server_connections', {})
    selected_provider_keys = set()
    for prov in backup_providers(data):
        if provider_id(prov) in provider_ids:
            selected_provider_keys.update(prov.get('keys', []))

    if selected_provider_keys and server_conns:
        log(f"Restaurando {len(selected_provider_keys)} chaves de provedores de conteúdo...")
        for key in selected_provider_keys:
            if key in server_conns:
                settings.setValue(key, deserialize_value(server_conns[key]))
                restored_providers += 1

    # 5. Ativa os plugins selecionados
    for plugin_key in plugins_to_enable:
        settings.setValue(activation_key(plugin_key), True)

    return {
        'plugins_enabled': plugins_to_enable,
        'plugins_to_install': plugins_to_install,
        'settings_restored': restored_settings,
        'provider_keys_restored': restored_providers
    }
