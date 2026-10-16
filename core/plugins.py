# -*- coding: utf-8 -*-
"""
Descoberta de plugins instalados e das chaves do QSettings que pertencem a cada um.
"""

import os
import configparser

from .serialization import serialize_value


def activation_key(plugin_key):
    """
    Chave do QSettings que guarda se o plugin está ativo.
    """
    return f"PythonPlugins/{plugin_key}"


def scan_installed_plugins(paths, settings, warn=None):
    """
    Escaneia as pastas de plugins instalados e lê seus metadados.

    paths: diretórios de plugins (normalmente qgis.utils.plugin_paths).
    settings: objeto compatível com QSettings.
    warn: função opcional chamada com mensagens de aviso.
    """
    plugins_list = []

    for path in paths:
        if not os.path.exists(path):
            continue
        for folder in os.listdir(path):
            folder_path = os.path.join(path, folder)
            if not os.path.isdir(folder_path):
                continue

            if folder.startswith('__'):
                continue

            metadata_file = os.path.join(folder_path, 'metadata.txt')
            if os.path.exists(metadata_file):
                try:
                    config = configparser.ConfigParser()
                    with open(metadata_file, 'r', encoding='utf-8', errors='ignore') as f:
                        config.read_file(f)

                    name = config.get('general', 'name', fallback=folder)
                    version = config.get('general', 'version', fallback='Desconhecida')
                    description = config.get('general', 'description', fallback='')

                    is_active = settings.value(activation_key(folder), False, type=bool)

                    plugins_list.append({
                        'key': folder,
                        'name': name,
                        'version': version,
                        'description': description,
                        'active': is_active,
                        'path': folder_path
                    })
                except Exception as e:
                    if warn:
                        warn(f"Aviso ao ler metadados do plugin '{folder}': {str(e)}")

    return plugins_list


def collect_plugin_settings(settings, plugin_key):
    """
    Vasculha o QSettings em busca de configurações associadas à chave do plugin.
    Retorna {chave: valor serializado}.
    """
    plugin_settings = {}

    act_key = activation_key(plugin_key)
    plugin_settings[act_key] = serialize_value(settings.value(act_key))

    prefixes = [
        f"{plugin_key}/",
        f"/{plugin_key}/",
        f"plugins/{plugin_key}/",
        f"/plugins/{plugin_key}/",
        f"plugin_{plugin_key}/",
        f"Plugin_{plugin_key}/"
    ]

    for key in settings.allKeys():
        key_lower = key.lower()
        match = False
        for prefix in prefixes:
            if key_lower.startswith(prefix.lower()):
                match = True
                break
        if match:
            plugin_settings[key] = serialize_value(settings.value(key))

    return plugin_settings


def setting_belongs_to_plugins(key, plugin_keys):
    """
    Indica se uma chave salva no backup pertence a algum dos plugins informados.
    """
    for sel_key in plugin_keys:
        if (key.startswith(f"PythonPlugins/{sel_key}") or key.startswith(f"{sel_key}/")
                or key.startswith(f"/{sel_key}/") or key.startswith(f"plugin_{sel_key}/")
                or key.startswith(f"Plugin_{sel_key}/")):
            return True
    return False
