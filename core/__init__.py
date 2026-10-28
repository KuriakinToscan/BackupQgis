# -*- coding: utf-8 -*-
"""
Lógica do plugin independente da interface.

Os módulos ``keys`` e ``deps`` não importam QGIS nem Qt e podem ser testados
com ``pytest`` puro. Os demais dependem de ``qgis.PyQt``/``qgis.core``.
"""
