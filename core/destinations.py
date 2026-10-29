# -*- coding: utf-8 -*-
"""
Pastas de destino dos dois tipos de backup.

O backup das configurações (JSON, authcfg, ZIP de plugins) e o de projetos
(pastas ``<projeto>_bkp``) ficam em pastas distintas, por padrão
``<Documentos>/BackupQgis/configuracoes`` e ``<Documentos>/BackupQgis/projetos``.
Uma pasta não pode ser igual à outra nem estar dentro dela: a retenção de um
tipo nunca alcança os arquivos do outro e a lista de cada histórico fica limpa.

Não importa QGIS nem Qt.
"""

import os
from typing import Optional

ROOT_NAME = "BackupQgis"
CONFIG = "configuracoes"
PROJECTS = "projetos"


def default_dest(kind: str, documents: str) -> str:
    """
    Pasta padrão de um tipo de backup.

    :param kind: ``"config"`` ou ``"projects"``.
    :param documents: pasta Documentos do usuário (ou a pasta pessoal).
    """
    return os.path.join(documents, ROOT_NAME, CONFIG if kind == "config" else PROJECTS)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(path))))


def paths_overlap(a: str, b: str) -> bool:
    """``True`` se as pastas são a mesma ou uma está dentro da outra (vazias nunca conflitam)."""
    if not a.strip() or not b.strip():
        return False
    na, nb = _norm(a), _norm(b)
    return na == nb or na.startswith(nb.rstrip(os.sep) + os.sep) or nb.startswith(na.rstrip(os.sep) + os.sep)


def is_within(path: str, folder: str) -> bool:
    """``True`` se ``path`` é ``folder`` ou está dentro dela (vazias nunca)."""
    if not path.strip() or not folder.strip():
        return False
    np_, nf = _norm(path), _norm(folder)
    return np_ == nf or np_.startswith(nf.rstrip(os.sep) + os.sep)


def overlap_message(path: str, other: str, other_label: str) -> Optional[str]:
    """Texto do aviso quando ``path`` conflita com a pasta ``other`` (``None`` se não conflita)."""
    if not paths_overlap(path, other):
        return None
    return (f"A pasta {path} coincide com a do {other_label} ({other}) ou uma está dentro da outra. "
            f"Use pastas separadas, por exemplo .../{ROOT_NAME}/{CONFIG} e .../{ROOT_NAME}/{PROJECTS}.")
