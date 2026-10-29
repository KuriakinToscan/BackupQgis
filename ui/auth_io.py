# -*- coding: utf-8 -*-
"""
Exportação e importação das configurações de autenticação do QGIS (authcfg).

Usa ``QgsAuthManager.exportAuthenticationConfigsToXml`` e
``importAuthenticationConfigsFromXml`` (QGIS 3.20+). O XML gerado é
criptografado pelo QGIS com a senha informada; o banco de autenticação
precisa estar desbloqueado com a senha mestra, que o próprio QGIS pede.

O arquivo fica ao lado do backup: ``qgis-backup_x.json`` ->
``qgis-backup_x.authcfg.xml``.
"""

from typing import Any, Callable, Optional

from qgis.core import QgsApplication

SUFFIX = ".authcfg.xml"
LogFn = Callable[[str], None]


class AuthTransferError(Exception):
    """Falha ao exportar ou importar as configurações de autenticação."""


def authcfg_path_for(backup_path: str) -> str:
    """Caminho do XML de autenticação correspondente a um backup JSON."""
    stem = backup_path[:-5] if backup_path.lower().endswith(".json") else backup_path
    return stem + SUFFIX


def _manager() -> Optional[Any]:
    getter = getattr(QgsApplication, "authManager", None)
    manager = getter() if callable(getter) else None
    if manager is None or not hasattr(manager, "exportAuthenticationConfigsToXml"):
        return None
    return manager


def auth_transfer_available() -> bool:
    """``True`` se a versão do QGIS permite exportar/importar authcfg."""
    return _manager() is not None


def _unlock(manager: Any) -> None:
    """Pede a senha mestra (diálogo do QGIS) se o banco estiver bloqueado."""
    if hasattr(manager, "masterPasswordIsSet") and manager.masterPasswordIsSet():
        return
    if hasattr(manager, "setMasterPassword") and not manager.setMasterPassword(True):
        raise AuthTransferError("Senha mestra de autenticação não informada ou incorreta.")


def export_authcfg(path: str, password: str, log: LogFn) -> int:
    """
    Exporta todas as configurações de autenticação para ``path``.

    :param password: senha que protege o XML (obrigatória).
    :returns: quantidade exportada (0 se não houver nenhuma; nada é gravado).
    :raises AuthTransferError: recurso indisponível, senha vazia ou falha do QGIS.
    """
    manager = _manager()
    if manager is None:
        raise AuthTransferError("Esta versão do QGIS não exporta configurações de autenticação (requer 3.20+).")
    if not password:
        raise AuthTransferError("Informe uma senha para proteger o arquivo de autenticação.")
    _unlock(manager)
    ids = list(manager.configIds())
    if not ids:
        log("Nenhuma configuração de autenticação para exportar.")
        return 0
    if not manager.exportAuthenticationConfigsToXml(path, ids, password):
        raise AuthTransferError("O QGIS não conseguiu exportar as configurações de autenticação.")
    log(f"{len(ids)} configurações de autenticação exportadas para {path}.")
    return len(ids)


def import_authcfg(path: str, password: str, overwrite: bool, log: LogFn) -> None:
    """
    Importa as configurações de autenticação de ``path``.

    :param overwrite: substituir configurações com o mesmo id.
    :raises AuthTransferError: recurso indisponível, senha incorreta ou arquivo inválido.
    """
    manager = _manager()
    if manager is None:
        raise AuthTransferError("Esta versão do QGIS não importa configurações de autenticação (requer 3.20+).")
    _unlock(manager)
    if not manager.importAuthenticationConfigsFromXml(path, password, overwrite):
        raise AuthTransferError("Falha ao importar (senha incorreta ou arquivo inválido).")
    log(f"Configurações de autenticação importadas de {path}.")
