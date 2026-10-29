# -*- coding: utf-8 -*-
"""
Tarefas em segundo plano (QgsTask).

Regras: nada aqui toca widgets nem APIs de interface do QGIS dentro de
``run()``. A instalação de plugins pelo repositório usa ``pyplugin_installer``,
que depende da interface, e por isso fica em ``core.installer`` (thread principal).
"""
