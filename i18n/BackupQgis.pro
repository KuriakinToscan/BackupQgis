# Gera/atualiza os arquivos de tradução a partir dos textos em português:
#   pylupdate5 i18n/BackupQgis.pro      (ou pylupdate6)
#   lrelease i18n/BackupQgis_en.ts      -> i18n/BackupQgis_en.qm
# O plugin carrega i18n/BackupQgis_<idioma>.qm conforme o idioma do QGIS.
SOURCES = ../backup_plugin.py \
          ../ui/dialog.py \
          ../ui/preview_dialog.py \
          ../ui/projects_page.py \
          ../ui/sync_page.py \
          ../ui/widgets.py
TRANSLATIONS = BackupQgis_en.ts \
               BackupQgis_es.ts
CODECFORTR = UTF-8
