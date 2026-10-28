# -*- coding: utf-8 -*-
"""
Formato do arquivo de backup (JSON), gravação atômica, leitura com
compatibilidade com o formato antigo e plano de restauração.

Não importa QGIS nem Qt.
"""

import datetime as _dt
import glob
import json
import os
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .keys import group_connection_keys, split_key

SCHEMA_VERSION = 2
GENERATOR = "BackupQgis"
SNAPSHOT_PREFIX = "pre-restore_"


class BackupFormatError(ValueError):
    """Arquivo de backup inválido ou incompatível."""


def utc_now_iso() -> str:
    """Data/hora atual em ISO 8601 com fuso UTC."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def build_backup_document(
    *,
    plugin_version: str,
    source: Dict[str, Any],
    repositories: Dict[str, Any],
    plugins: List[Dict[str, Any]],
    plugin_settings: Dict[str, Dict[str, Any]],
    providers: List[Dict[str, Any]],
    omitted_sensitive_keys: Iterable[str] = (),
    fallback_keys: Iterable[str] = (),
) -> Dict[str, Any]:
    """
    Monta o documento de backup no formato atual (schema 2).

    :param plugin_version: versão do BackupQgis que gerou o arquivo.
    :param source: dados da origem (versão do QGIS, SO, máquina, perfil).
    :param repositories: chaves serializadas de ``app/plugin_repositories``.
    :param plugins: plugins selecionados (``key, name, version, active, core``).
    :param plugin_settings: ``{plugin: {chave: valor serializado}}``.
    :param providers: conexões ``{provider_type, name, details, keys: {chave: valor}}``.
    :param omitted_sensitive_keys: chaves de senha/token não exportadas.
    :param fallback_keys: chaves gravadas como texto por tipo desconhecido.
    """
    flat_settings: Dict[str, Any] = {}
    plugin_settings_keys: Dict[str, List[str]] = {}
    for plugin_key, values in plugin_settings.items():
        flat_settings.update(values)
        plugin_settings_keys[plugin_key] = sorted(values)

    server_connections: Dict[str, Any] = {}
    providers_index: List[Dict[str, Any]] = []
    for prov in providers:
        server_connections.update(prov["keys"])
        providers_index.append({
            "provider_type": prov["provider_type"],
            "name": prov["name"],
            "details": prov.get("details", ""),
            "keys": sorted(prov["keys"]),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "plugin_version": plugin_version,
        "created_at": utc_now_iso(),
        "source": source,
        "repositories": repositories,
        "plugins": [
            {k: p.get(k) for k in ("key", "name", "version", "active", "core")} for p in plugins
        ],
        "settings": flat_settings,
        "plugin_settings_keys": plugin_settings_keys,
        "server_connections": server_connections,
        "providers": providers_index,
        "omitted_sensitive_keys": sorted(set(omitted_sensitive_keys)),
        "fallback_keys": sorted(set(fallback_keys)),
    }


def write_json_atomic(path: str, data: Any) -> None:
    """
    Grava JSON de forma atômica: arquivo temporário na mesma pasta, ``fsync``
    e ``os.replace``. Uma falha no meio não deixa o destino truncado.

    :raises OSError: se a pasta não for gravável ou o disco estiver cheio.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".backupqgis_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _is_serialized(value: Any) -> bool:
    return isinstance(value, dict) and "type" in value and "value" in value


def legacy_owner(key: str) -> str:
    """
    Infere a pasta do plugin dona de uma chave gravada no formato antigo.
    """
    parts = split_key(key)
    if not parts:
        return ""
    first = parts[0]
    if first.lower() in ("pythonplugins", "plugins") and len(parts) > 1:
        return parts[1]
    if first.lower().startswith("plugin_"):
        return first[len("plugin_"):]
    return first


def normalize_document(data: Any) -> Tuple[Dict[str, Any], List[str]]:
    """
    Valida o documento e converte o formato antigo (sem ``schema_version``)
    para a estrutura atual.

    :returns: (documento normalizado, avisos).
    :raises BackupFormatError: se não for um backup reconhecível ou for de versão futura.
    """
    warnings: List[str] = []
    if not isinstance(data, dict) or not any(k in data for k in ("plugins", "server_connections", "settings")):
        raise BackupFormatError("Arquivo de backup inválido ou incompatível.")

    schema = data.get("schema_version", 1)
    if not isinstance(schema, int) or schema > SCHEMA_VERSION:
        raise BackupFormatError(
            f"Backup gerado por versão mais nova do plugin (schema {schema}); atualize o BackupQgis."
        )

    doc = dict(data)
    doc.setdefault("source", {})
    doc["plugins"] = [dict({"core": False, "active": False}, **p) for p in doc.get("plugins", []) if p.get("key")]
    doc.setdefault("settings", {})
    doc.setdefault("server_connections", {})
    doc.setdefault("omitted_sensitive_keys", [])
    doc.setdefault("fallback_keys", [])

    repos = doc.get("repositories") or {}
    if not isinstance(repos, dict) or not all(_is_serialized(v) for v in repos.values()):
        if repos:
            warnings.append("Repositórios no formato antigo foram ignorados (chave incorreta na versão 0.1).")
        repos = {}
    doc["repositories"] = repos

    if schema < 2:
        warnings.append("Backup no formato antigo (0.1): associação de configurações reconstruída por prefixo.")
        known = {p["key"].lower(): p["key"] for p in doc["plugins"]}
        owners: Dict[str, List[str]] = {}
        for key in doc["settings"]:
            owner = legacy_owner(key)
            owner = known.get(owner.lower(), owner)
            owners.setdefault(owner, []).append(key)
        doc["plugin_settings_keys"] = {k: sorted(v) for k, v in owners.items()}
        doc["providers"] = []
    else:
        doc.setdefault("plugin_settings_keys", {})
        doc.setdefault("providers", [])

    if not doc["providers"] and doc["server_connections"]:
        grouped = group_connection_keys(doc["server_connections"].keys())
        doc["providers"] = [
            {"provider_type": t, "name": n, "details": "", "keys": keys} for (t, n), keys in sorted(grouped.items())
        ]
        orphan = set(doc["server_connections"]) - {k for keys in grouped.values() for k in keys}
        if orphan:
            warnings.append(f"{len(orphan)} chaves de conexão não puderam ser associadas a uma conexão e serão ignoradas.")

    for prov in doc["providers"]:
        prov["id"] = provider_id(prov["provider_type"], prov["name"])

    doc["schema_version"] = SCHEMA_VERSION
    return doc, warnings


def load_backup(path: str) -> Tuple[Dict[str, Any], List[str]]:
    """
    Lê e normaliza um arquivo de backup.

    :raises BackupFormatError: JSON inválido ou estrutura desconhecida.
    :raises OSError: falha de leitura.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise BackupFormatError(f"JSON inválido: {exc}") from exc
    return normalize_document(data)


def provider_id(provider_type: str, name: str) -> str:
    """Identificador estável de uma conexão no backup."""
    return f"{provider_type}::{name}"


def build_restore_plan(
    doc: Dict[str, Any],
    config_plugin_keys: Iterable[str],
    provider_ids: Iterable[str],
    include_repositories: bool,
) -> Dict[str, Any]:
    """
    Calcula as chaves que serão gravadas na restauração.

    :param doc: documento normalizado.
    :param config_plugin_keys: plugins cujas configurações serão restauradas.
    :param provider_ids: conexões selecionadas (:func:`provider_id`).
    :param include_repositories: restaurar repositórios de plugins.
    :returns: ``{chave: valor serializado}``.
    """
    plan: Dict[str, Any] = {}
    settings = doc.get("settings", {})
    keys_by_plugin = doc.get("plugin_settings_keys", {})
    for plugin_key in config_plugin_keys:
        for key in keys_by_plugin.get(plugin_key, []):
            if key in settings:
                plan[key] = settings[key]

    wanted = set(provider_ids)
    conns = doc.get("server_connections", {})
    for prov in doc.get("providers", []):
        if prov.get("id") in wanted:
            for key in prov.get("keys", []):
                if key in conns:
                    plan[key] = conns[key]

    if include_repositories:
        plan.update(doc.get("repositories", {}))
    return plan


def snapshot_dir(settings_dir: str) -> str:
    """Pasta onde ficam as cópias de segurança pré-restauração."""
    return os.path.join(settings_dir, "backupqgis", "snapshots")


def save_snapshot(directory: str, snapshot: Dict[str, Optional[Any]], keep: int = 10) -> str:
    """
    Grava o estado anterior das chaves que serão sobrescritas e mantém
    apenas as ``keep`` cópias mais recentes.

    :param snapshot: ``{chave: valor serializado ou None se não existia}``.
    :returns: caminho do arquivo gravado.
    """
    os.makedirs(directory, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    path = os.path.join(directory, f"{SNAPSHOT_PREFIX}{stamp}.json")
    write_json_atomic(path, {"generator": GENERATOR, "created_at": utc_now_iso(), "values": snapshot})
    for old in list_snapshots(directory)[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def list_snapshots(directory: str) -> List[str]:
    """Cópias pré-restauração, da mais recente para a mais antiga."""
    return sorted(glob.glob(os.path.join(directory, f"{SNAPSHOT_PREFIX}*.json")), reverse=True)


def load_snapshot(path: str) -> Tuple[Dict[str, Any], List[str]]:
    """
    Lê uma cópia pré-restauração.

    :returns: (chaves a regravar com o valor anterior, chaves a remover porque não existiam).
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    values = data.get("values", {}) if isinstance(data, dict) else {}
    to_set = {k: v for k, v in values.items() if v is not None}
    to_remove = [k for k, v in values.items() if v is None]
    return to_set, to_remove
