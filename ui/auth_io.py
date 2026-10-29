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

from typing import Any, Callable, Dict, List, Optional, Tuple

from qgis.core import QgsApplication

from ..core.credentials import Credential

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


DISABLED_MESSAGE = ("O gerenciador de autenticação do QGIS está desabilitado nesta sessão (a senha mestra falhou "
                    "5 vezes). Feche e abra o QGIS para tentar de novo; nada foi alterado no banco de autenticação.")


def auth_disabled() -> bool:
    """``True`` se o QGIS desabilitou o gerenciador de autenticação nesta sessão."""
    manager = _manager()
    return bool(manager is not None and hasattr(manager, "isDisabled") and manager.isDisabled())


def _unlock(manager: Any) -> None:
    """
    Pede a senha mestra uma única vez (diálogo do próprio QGIS) se o banco
    estiver bloqueado. Nunca repete a tentativa: o QGIS desabilita o
    gerenciador após 5 falhas na sessão.
    """
    if hasattr(manager, "isDisabled") and manager.isDisabled():
        raise AuthTransferError(DISABLED_MESSAGE)
    if hasattr(manager, "masterPasswordIsSet") and manager.masterPasswordIsSet():
        return
    if hasattr(manager, "setMasterPassword") and not manager.setMasterPassword(True):
        if hasattr(manager, "isDisabled") and manager.isDisabled():
            raise AuthTransferError(DISABLED_MESSAGE)
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


def export_protected(path: str, password: str, credentials: List[Credential], referenced: List[str],
                     log: LogFn) -> Tuple[Dict[str, str], int]:
    """
    Exporta as autenticações usadas pelas conexões selecionadas.

    Para cada senha sem authcfg, cria uma configuração Basic temporária
    ("BackupQgis: <conexão>") com usuário e senha; exporta essas e as já
    referenciadas pelas conexões (``referenced``) para ``path``, criptografado
    com ``password``, e remove as temporárias do banco local, que continua
    como estava. Sem nada a exportar, não toca na senha mestra.

    :returns: (``{chave de senha: id criado}``, quantidade de configurações exportadas).
    :raises AuthTransferError: recurso indisponível, senha vazia ou falha do QGIS.
    """
    from qgis.core import QgsAuthMethodConfig

    manager = _manager()
    if manager is None:
        raise AuthTransferError("Esta versão do QGIS não exporta configurações de autenticação (requer 3.20+).")
    if not credentials and not referenced:
        return {}, 0
    if not password:
        raise AuthTransferError("Informe uma senha para proteger o arquivo de autenticação.")
    _unlock(manager)
    created: Dict[str, str] = {}
    try:
        for cred in credentials:
            if cred.existing_authcfg:
                continue
            cfg = QgsAuthMethodConfig("Basic")
            cfg.setName(f"BackupQgis: {cred.connection}")
            cfg.setConfig("username", cred.username)
            cfg.setConfig("password", cred.password)
            stored = manager.storeAuthenticationConfig(cfg)
            ok = stored[0] if isinstance(stored, tuple) else stored
            if not ok or not cfg.id():
                raise AuthTransferError(f"Não foi possível criar a autenticação de {cred.connection}.")
            created[cred.password_key] = cfg.id()
        existing = set(manager.configIds())
        missing = [r for r in referenced if r not in existing]
        if missing:
            log(f"Autenticações referenciadas que não existem nesta máquina (ignoradas): {', '.join(missing)}")
        ids = sorted({r for r in referenced if r in existing} | set(created.values()))
        if not ids:
            return created, 0
        if not manager.exportAuthenticationConfigsToXml(path, ids, password):
            raise AuthTransferError("O QGIS não conseguiu exportar as configurações de autenticação.")
        log(f"{len(ids)} configurações de autenticação exportadas para {path} "
            f"({len(created)} criadas a partir de senhas de conexões).")
        return created, len(ids)
    finally:
        for cid in set(created.values()):
            if not manager.removeAuthenticationConfig(cid):
                log(f"Configuração temporária {cid} não pôde ser removida do banco local.")


def import_authcfg(path: str, password: str, overwrite: bool, log: LogFn) -> None:
    """
    Importa as configurações de autenticação de ``path``.

    :param overwrite: substituir configurações com o mesmo id.
    :raises AuthTransferError: recurso indisponível, senha incorreta ou arquivo inválido.
    """
    manager = _manager()
    if manager is None:
        raise AuthTransferError("Esta versão do QGIS não importa configurações de autenticação (requer 3.20+).")
    _unlock(manager)  # verifica também se o gerenciador foi desabilitado
    if not manager.importAuthenticationConfigsFromXml(path, password, overwrite):
        raise AuthTransferError("Falha ao importar (senha incorreta ou arquivo inválido).")
    log(f"Configurações de autenticação importadas de {path}.")
