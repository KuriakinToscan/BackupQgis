# -*- coding: utf-8 -*-
"""
Regras de correlação entre chaves do QSettings, plugins e conexões.

Este módulo não importa QGIS nem Qt: recebe e devolve apenas strings e
estruturas Python, o que permite testá-lo com ``pytest`` puro.
"""

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Grupo em que o instalador de plugins do QGIS grava os repositórios
# (pyplugin_installer/installer_data.py: reposGroup).
REPOSITORIES_GROUP = "app/plugin_repositories"

# Chave de ativação de plugins Python.
ACTIVATION_GROUP = "PythonPlugins"

# Último segmento de chaves que guardam segredos e não devem ser exportadas
# por padrão.
SENSITIVE_KEY_NAMES = frozenset({
    "password", "passwd", "pwd", "pass", "secret", "token", "access_token",
    "refresh_token", "apikey", "api_key", "api-key", "authkey", "client_secret",
})

# Último segmento de chaves que descrevem o endereço da conexão (exibido na tabela).
DETAIL_KEY_NAMES = ("url", "host", "sqlitepath", "path", "database", "dbname", "service")

# Provedores que gravam conexões em "<Provedor>/connections/<nome>/...".
PROVIDER_ROOT_GROUPS = frozenset({
    "postgresql", "spatialite", "mssql", "oracle", "hana", "db2", "gpkg",
})

# Contêineres intermediários da árvore de configurações nova (QGIS >= 3.30),
# ex.: "connections/ows/items/wms/connections/items/<nome>/url".
TREE_CONTAINERS = frozenset({"ows"})

PROVIDER_LABELS = {
    "wms": "WMS / WMTS",
    "wfs": "WFS / OGC API",
    "wcs": "WCS",
    "xyz": "XYZ Tiles",
    "vectortile": "Vector Tiles",
    "vectortiles": "Vector Tiles",
    "arcgismapserver": "ArcGIS MapServer",
    "arcgis-mapserver": "ArcGIS MapServer",
    "arcgisfeatureserver": "ArcGIS FeatureServer",
    "arcgis-featureserver": "ArcGIS FeatureServer",
    "arcgisvectortileservice": "ArcGIS Vector Tiles",
    "geonode": "GeoNode",
    "stac": "STAC Catalog",
    "sensorthings": "SensorThings",
    "tiled-scene": "Tiled Scene",
    "tiledscene": "Tiled Scene",
    "postgresql": "PostGIS",
    "postgres": "PostGIS",
    "spatialite": "SpatiaLite",
    "mssql": "MS SQL Server",
    "oracle": "Oracle",
    "hana": "SAP HANA",
    "db2": "DB2",
    "gpkg": "GeoPackage",
}


def split_key(key: str) -> List[str]:
    """
    Divide uma chave do QSettings em segmentos, ignorando barras iniciais,
    finais ou duplicadas.

    :param key: chave no formato "grupo/subgrupo/nome".
    :returns: lista de segmentos não vazios.
    """
    return [part for part in key.split("/") if part]


def provider_label(raw_type: str) -> str:
    """
    Converte o identificador bruto do tipo de conexão em rótulo legível.

    :param raw_type: ex.: "wms", "postgresql", "connections-xyz" já limpo.
    :returns: rótulo para exibição; desconhecidos voltam em maiúsculas.
    """
    return PROVIDER_LABELS.get(raw_type.lower(), raw_type.upper())


def parse_connection_key(key: str) -> Optional[Tuple[str, str]]:
    """
    Identifica se a chave pertence a uma conexão de provedor de dados.

    Padrões reconhecidos:

    1. ``qgis/connections-<tipo>/<nome>/...`` (OWS, XYZ, ArcGIS etc.);
    2. ``qgis/<TIPO>/<nome>/...`` com tipo OWS (credenciais legadas de WMS/WFS);
    3. ``<Provedor>/connections/<nome>/...`` (PostgreSQL, SpatiaLite, MSSQL...)
       e ``providers/<...>/connections/<nome>/...``;
    4. ``connections/.../items/<nome>/...`` (árvore de configurações do QGIS 3.30+).

    Valores no nível do grupo (ex.: ``qgis/connections-wms/selected``) não são
    conexões e são ignorados: exige-se ao menos um segmento após o nome.

    :param key: chave completa do QSettings.
    :returns: tupla (tipo_bruto, nome_da_conexão) ou ``None``.
    """
    parts = split_key(key)
    lower = [p.lower() for p in parts]
    n = len(parts)

    # Padrão 4: árvore nova "connections/..."
    if n >= 4 and lower[0] == "connections":
        for i in range(1, n - 2):
            if lower[i] != "items":
                continue
            candidate = lower[i - 1]
            if candidate == "connections" and i >= 2:
                candidate = lower[i - 2]
            if candidate in TREE_CONTAINERS or candidate == "connections":
                continue
            return candidate, parts[i + 1]
        return None

    # Padrão 1: segmento "connections-<tipo>"
    for i, part in enumerate(lower):
        if part.startswith("connections-") and i + 2 < n:
            return part[len("connections-"):], parts[i + 1]

    # Padrão 2: "qgis/WMS/<nome>/username" (credenciais OWS legadas)
    if n >= 4 and lower[0] == "qgis" and lower[1] in ("wms", "wfs", "wcs"):
        return lower[1], parts[2]

    # Padrão 3: "<Provedor>/connections/<nome>/..." ou "providers/.../connections/<nome>/..."
    for i in range(1, n - 2):
        if lower[i] != "connections":
            continue
        root_type = lower[i - 1]
        if root_type in PROVIDER_ROOT_GROUPS or lower[0] == "providers":
            return root_type, parts[i + 1]
        break

    return None


def is_sensitive_key(key: str) -> bool:
    """
    Indica se a chave guarda segredo (senha, token, chave de API).

    :param key: chave completa do QSettings.
    :returns: ``True`` se o último segmento for um nome sensível conhecido.
    """
    parts = split_key(key)
    return bool(parts) and parts[-1].lower() in SENSITIVE_KEY_NAMES


def is_detail_key(key: str) -> bool:
    """
    Indica se a chave descreve o endereço de uma conexão (URL, host, caminho).
    """
    parts = split_key(key)
    return bool(parts) and parts[-1].lower() in DETAIL_KEY_NAMES


def plugin_key_matcher(plugin_key: str) -> Callable[[str], bool]:
    """
    Cria um predicado que diz se uma chave do QSettings pertence ao plugin.

    A comparação é feita por segmentos inteiros e sem diferenciar maiúsculas,
    de modo que ``foo`` não captura chaves de ``foobar``. Grupos aceitos:
    ``<plugin>/...``, ``plugins/<plugin>/...``, ``plugin_<plugin>/...`` e a
    chave de ativação ``PythonPlugins/<plugin>``.

    :param plugin_key: nome da pasta do plugin.
    :returns: função ``(chave) -> bool``.
    """
    pk = plugin_key.lower()
    activation = (ACTIVATION_GROUP.lower(), pk)

    def matches(key: str) -> bool:
        parts = [p.lower() for p in split_key(key)]
        if not parts:
            return False
        if len(parts) == 2 and (parts[0], parts[1]) == activation:
            return True
        if len(parts) >= 2 and parts[0] in (pk, "plugin_" + pk):
            return True
        if len(parts) >= 3 and parts[0] == "plugins" and parts[1] == pk:
            return True
        return False

    return matches


class SettingsIndex:
    """
    Índice das chaves do QSettings agrupadas pelo primeiro segmento.

    Substitui varreduras repetidas de ``QSettings.allKeys()`` (lentas no
    Registro do Windows) por uma única leitura indexada.
    """

    def __init__(self, keys: Iterable[str]) -> None:
        """
        :param keys: todas as chaves do QSettings (``QSettings.allKeys()``).
        """
        self.keys: List[str] = list(keys)
        self._by_root: Dict[str, List[str]] = {}
        for key in self.keys:
            parts = split_key(key)
            if parts:
                self._by_root.setdefault(parts[0].lower(), []).append(key)

    def under(self, *roots: str) -> List[str]:
        """
        Retorna as chaves cujo primeiro segmento é um dos ``roots``.

        :param roots: primeiros segmentos, sem diferenciar maiúsculas.
        """
        result: List[str] = []
        for root in roots:
            result.extend(self._by_root.get(root.lower(), []))
        return result

    def plugin_keys(self, plugin_key: str) -> List[str]:
        """
        Lista as chaves do QSettings associadas ao plugin.

        :param plugin_key: nome da pasta do plugin.
        :returns: chaves ordenadas; inclui a chave de ativação, se existir.
        """
        matcher = plugin_key_matcher(plugin_key)
        candidates = self.under(plugin_key, "plugin_" + plugin_key, "plugins", ACTIVATION_GROUP)
        return sorted({key for key in candidates if matcher(key)})

    def repository_keys(self) -> List[str]:
        """
        Lista as chaves dos repositórios de plugins configurados.
        """
        prefix = REPOSITORIES_GROUP.lower() + "/"
        return sorted(k for k in self.under("app") if k.lower().startswith(prefix))

    def group_connections(self) -> Dict[Tuple[str, str], List[str]]:
        """
        Agrupa as chaves de conexões por (rótulo do tipo, nome).

        Chaves de credenciais legadas (``qgis/WMS/<nome>/...``) são unidas à
        conexão de mesmo tipo e nome.
        """
        return group_connection_keys(self.keys)


def group_connection_keys(keys: Iterable[str]) -> Dict[Tuple[str, str], List[str]]:
    """
    Agrupa chaves de conexões por (rótulo do tipo, nome da conexão).

    :param keys: chaves do QSettings.
    :returns: dicionário {(rótulo, nome): [chaves ordenadas]}.
    """
    groups: Dict[Tuple[str, str], List[str]] = {}
    for key in keys:
        parsed = parse_connection_key(key)
        if not parsed:
            continue
        raw_type, name = parsed
        if not name.strip():
            continue
        groups.setdefault((provider_label(raw_type), name), []).append(key)
    for value in groups.values():
        value.sort()
    return groups


def legacy_plugin_key_belongs(key: str, plugin_keys: Iterable[str]) -> bool:
    """
    Reproduz, para arquivos do formato antigo (schema 1), a associação
    chave → plugin usada na exportação, agora sem diferenciar maiúsculas.

    :param key: chave gravada no backup.
    :param plugin_keys: plugins selecionados para restauração.
    """
    return any(plugin_key_matcher(pk)(key) for pk in plugin_keys)


def remap_path_prefix(value: Any, old: str, new: str) -> Any:
    """
    Substitui o prefixo de caminho ``old`` por ``new`` em strings, recursivamente
    em listas e dicionários. Aceita barras ``/`` e ``\\`` como equivalentes no prefixo.

    :param value: valor já desserializado.
    :param old: prefixo de origem (ex.: ``C:/Users/fulano``).
    :param new: prefixo de destino (ex.: ``/home/fulano``).
    :returns: valor com o prefixo trocado; tipos não textuais voltam inalterados.
    """
    if not old:
        return value
    if isinstance(value, str):
        for variant in {old, old.replace("\\", "/"), old.replace("/", "\\")}:
            if value.startswith(variant):
                return new + value[len(variant):]
        return value
    if isinstance(value, list):
        return [remap_path_prefix(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: remap_path_prefix(v, old, new) for k, v in value.items()}
    return value
