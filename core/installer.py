# -*- coding: utf-8 -*-
"""
Instalação e ativação de plugins QGIS na thread principal.

``pyplugin_installer`` usa o gerenciador de rede, a barra de mensagens e
diálogos do QGIS; chamá-lo fora da thread principal pode travar ou encerrar
o QGIS. Por isso estas funções devem ser chamadas apenas pela interface, com
``QApplication.processEvents()`` entre os plugins para manter a janela viva.
"""

import os
from typing import Callable, Dict, List, Optional, Tuple

LogFn = Callable[[str], None]


def _installer():
    """Instância do instalador de plugins do QGIS ou ``None``."""
    try:
        import pyplugin_installer
        return pyplugin_installer.instance()
    except Exception:
        return None


def repository_plugins(refresh: bool, log: LogFn) -> Dict[str, Dict]:
    """
    Plugins disponíveis nos repositórios configurados.

    :param refresh: recarregar a lista dos repositórios (acesso à rede).
    :param log: função de log.
    :returns: ``{pasta: metadados}`` conforme ``pyplugin_installer.installer_data.plugins``.
    """
    installer = _installer()
    if installer is None:
        log("Instalador de plugins do QGIS indisponível (pyplugin_installer).")
        return {}
    try:
        if refresh:
            log("Atualizando a lista dos repositórios de plugins...")
            installer.fetchAvailablePlugins(False)
        from pyplugin_installer.installer_data import plugins as installer_plugins
        return dict(installer_plugins.all())
    except Exception as exc:
        log(f"Falha ao consultar os repositórios de plugins: {exc}")
        return {}


def resolve_repository_key(name_or_key: str, available: Dict[str, Dict]) -> Optional[str]:
    """
    Converte o nome de um plugin (usado em ``plugin_dependencies``) na pasta
    usada pelo repositório.
    """
    if name_or_key in available:
        return name_or_key
    target = name_or_key.strip().lower()
    for key, meta in available.items():
        if key.lower() == target or str(meta.get("name", "")).strip().lower() == target:
            return key
    return None


def _is_installed(key: str) -> bool:
    import qgis.utils
    if key in getattr(qgis.utils, "available_plugins", []):
        return True
    home = getattr(qgis.utils, "home_plugin_path", "")
    return bool(home) and os.path.isfile(os.path.join(home, key, "metadata.txt"))


def install_plugin(key: str, available: Dict[str, Dict], log: LogFn) -> bool:
    """
    Instala um plugin do repositório (thread principal).

    O retorno de ``installPlugin`` não é confiável entre versões do QGIS, então
    o sucesso é confirmado pela presença do plugin após a chamada.

    :returns: ``True`` se o plugin ficou instalado.
    """
    installer = _installer()
    if installer is None:
        log(f"Impossível instalar '{key}': instalador de plugins indisponível.")
        return False
    if key not in available:
        log(f"'{key}' não está em nenhum repositório configurado (instalado por ZIP ou repositório privado?).")
        return False
    try:
        try:
            installer.installPlugin(key, quiet=True)
        except TypeError:  # versões sem o argumento quiet
            installer.installPlugin(key)
    except Exception as exc:
        log(f"Erro ao instalar '{key}': {exc}")
        return False
    ok = _is_installed(key)
    log(f"Plugin '{key}' instalado." if ok else f"Plugin '{key}' não foi instalado (verifique o log do QGIS).")
    return ok


def activate_plugin(key: str, log: LogFn) -> Tuple[bool, bool]:
    """
    Marca o plugin como ativo e tenta carregá-lo sem reiniciar o QGIS.

    :returns: (ativado agora, precisa reiniciar).
    """
    import qgis.utils
    from qgis.PyQt.QtCore import QSettings

    QSettings().setValue(f"PythonPlugins/{key}", True)
    if key in getattr(qgis.utils, "active_plugins", []):
        return True, False
    try:
        if qgis.utils.loadPlugin(key) and qgis.utils.startPlugin(key):
            log(f"Plugin '{key}' carregado e ativado.")
            return True, False
    except Exception as exc:
        log(f"Plugin '{key}' marcado como ativo, mas não carregou agora: {exc}")
    return False, True


def install_from_archive(key: str, archive: str, log: LogFn) -> bool:
    """
    Instala um plugin a partir do ZIP de plugins do backup, na pasta de
    plugins do perfil atual (thread principal).

    :returns: ``True`` se o plugin ficou instalado.
    """
    import qgis.utils
    from .plugin_archive import PluginArchiveError, extract_plugin

    home = getattr(qgis.utils, "home_plugin_path", "")
    if not home:
        log(f"Impossível instalar '{key}' do ZIP: pasta de plugins do perfil desconhecida.")
        return False
    try:
        path = extract_plugin(archive, key, home)
    except (OSError, PluginArchiveError) as exc:
        log(f"Falha ao instalar '{key}' do ZIP: {exc}")
        return False
    updater = getattr(qgis.utils, "updateAvailablePlugins", None)
    if callable(updater):
        try:
            updater()
        except Exception as exc:
            log(f"Lista de plugins não atualizada: {exc}")
    log(f"Plugin '{key}' instalado a partir do ZIP do backup em {path}.")
    return True


def install_and_activate(
    keys: List[str],
    log: LogFn,
    process_events: Callable[[], None],
    is_canceled: Callable[[], bool],
    progress: Callable[[int, int], None],
    archive: Optional[str] = None,
    archive_keys: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """
    Instala os plugins ausentes e ativa todos os da lista, um por vez.
    O repositório tem preferência; o ZIP de plugins do backup (``archive``)
    é usado para os que não estão em nenhum repositório configurado.

    :param keys: pastas (ou nomes) dos plugins.
    :param log: função de log.
    :param process_events: chamada entre plugins para manter a interface responsiva.
    :param is_canceled: interrompe o laço quando retorna ``True``.
    :param progress: ``progress(feitos, total)``.
    :param archive: ZIP de plugins do backup, se houver.
    :param archive_keys: plugins contidos em ``archive``.
    :returns: ``{"installed": [...], "activated": [...], "failed": [...], "restart": [...]}``.
    """
    summary: Dict[str, List[str]] = {"installed": [], "activated": [], "failed": [], "restart": []}
    missing = [k for k in keys if not _is_installed(k)]
    available = repository_plugins(refresh=bool(missing), log=log) if missing else {}
    total = len(keys)
    for done, raw_key in enumerate(keys):
        if is_canceled():
            log("Instalação interrompida pelo usuário.")
            break
        progress(done, total)
        process_events()
        key = raw_key
        if not _is_installed(raw_key):
            key = resolve_repository_key(raw_key, available) or raw_key
            if not _is_installed(key):
                if key in available:
                    ok = install_plugin(key, available, log)
                elif archive and raw_key in (archive_keys or []):
                    key = raw_key
                    ok = install_from_archive(key, archive, log)
                else:
                    ok = install_plugin(key, available, log)
                if not ok:
                    summary["failed"].append(raw_key)
                    continue
                summary["installed"].append(key)
        activated, restart = activate_plugin(key, log)
        if activated:
            summary["activated"].append(key)
        if restart:
            summary["restart"].append(key)
    progress(total, total)
    return summary
