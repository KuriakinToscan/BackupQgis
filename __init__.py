# -*- coding: utf-8 -*-

def classFactory(iface):
    """
    QGIS plugin factory function.
    """
    from .backup_plugin import BackupPlugin
    return BackupPlugin(iface)
