# -*- coding: utf-8 -*-
"""
Compatibilidade entre QGIS 3 (PyQt5) e QGIS 4 (PyQt6).

No PyQt6 os enums só existem na forma qualificada (``Classe.Enum.MEMBRO``);
nos bindings antigos do QGIS 3 alguns enums só existem na forma curta.
"""

from typing import Any


def enum_value(owner: Any, enum_name: str, member: str) -> Any:
    """
    Retorna ``owner.enum_name.member`` ou, se indisponível, ``owner.member``.

    :param owner: classe que define o enum (ex.: ``QgsTask``, ``Qgis``).
    :param enum_name: nome do enum (ex.: ``"Flag"``, ``"MessageLevel"``).
    :param member: membro (ex.: ``"CanCancel"``, ``"Info"``).
    :raises AttributeError: se nenhuma das formas existir.
    """
    scoped = getattr(owner, enum_name, None)
    if scoped is not None and hasattr(scoped, member):
        return getattr(scoped, member)
    return getattr(owner, member)
