# -*- coding: utf-8 -*-
"""
Conversão de senhas gravadas nas conexões em referências authcfg.

No modo "senhas protegidas", a senha de cada conexão não vai para o JSON:
o plugin cria uma configuração de autenticação Basic (usuário + senha), que é
exportada no ``.authcfg.xml`` criptografado, e a conexão passa a apontar para
ela pela chave ``authcfg`` irmã da chave de senha. É o mesmo mecanismo que o
QGIS usa quando a conexão é configurada com autenticação.

Não importa QGIS nem Qt: trabalha sobre os valores já serializados.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

PASSWORD_NAMES = frozenset({"password", "passwd", "pwd", "pass"})
USERNAME_NAMES = ("username", "user", "login")


@dataclass
class Credential:
    """Senha encontrada numa conexão e as chaves envolvidas na conversão."""

    connection: str          # rótulo exibido (ex.: "PostGIS: IBAMA PG")
    password_key: str
    password: str
    username: str
    authcfg_key: str
    existing_authcfg: str    # authcfg já configurado na conexão (mantido, senha só é descartada)


def _plain(serialized: Any) -> str:
    """Valor textual de uma entrada serializada (``{"type", "value"}``) ou ``""``."""
    if isinstance(serialized, dict):
        value = serialized.get("value")
        return value if isinstance(value, str) and serialized.get("type") in ("basic", None) else ""
    return serialized if isinstance(serialized, str) else ""


def _parent(key: str) -> str:
    return key.rsplit("/", 1)[0] if "/" in key else ""


def find_credentials(connection: str, values: Dict[str, Any], all_values: Optional[Dict[str, Any]] = None
                     ) -> List[Credential]:
    """
    Senhas preenchidas nas chaves de uma conexão.

    :param connection: rótulo da conexão, usado no nome da configuração criada.
    :param values: ``{chave: valor serializado}`` da conexão (incluindo as senhas).
    :param all_values: valores de onde ler usuário/authcfg irmãos que não estejam
        em ``values`` (padrão: ``values``).
    :returns: uma entrada por chave de senha não vazia.
    """
    pool = dict(all_values or {})
    pool.update(values)
    found: List[Credential] = []
    for key in sorted(values):
        if key.rsplit("/", 1)[-1].lower() not in PASSWORD_NAMES:
            continue
        password = _plain(values[key])
        if not password:
            continue
        parent = _parent(key)
        prefix = parent + "/" if parent else ""
        username = next((_plain(pool[prefix + n]) for n in USERNAME_NAMES if prefix + n in pool), "")
        authcfg_key = prefix + "authcfg"
        found.append(Credential(connection=connection, password_key=key, password=password, username=username,
                                authcfg_key=authcfg_key, existing_authcfg=_plain(pool.get(authcfg_key))))
    return found


def apply_authcfg(values: Dict[str, Any], credentials: List[Credential], ids: Dict[str, str]
                  ) -> Tuple[Dict[str, Any], List[str]]:
    """
    Remove as senhas e grava a referência authcfg nas chaves da conexão.

    :param ids: ``{password_key: id da configuração criada}``; conexões que já
        tinham authcfg não precisam de entrada.
    :returns: (novos valores, chaves de senha removidas).
    """
    out = dict(values)
    removed: List[str] = []
    for cred in credentials:
        out.pop(cred.password_key, None)
        removed.append(cred.password_key)
        new_id = cred.existing_authcfg or ids.get(cred.password_key, "")
        if new_id:
            out[cred.authcfg_key] = {"type": "basic", "value": new_id}
    return out, removed


def referenced_authcfg(values: Dict[str, Any]) -> List[str]:
    """Ids de authcfg já usados pelas chaves ``.../authcfg`` não vazias de uma conexão."""
    return sorted({_plain(v) for k, v in values.items() if k.rsplit("/", 1)[-1].lower() == "authcfg" and _plain(v)})
