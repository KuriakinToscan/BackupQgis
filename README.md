# BackupQgis

Plugin do QGIS para backup e restauração de **plugins**, **configurações de plugins (QSettings)**,
**repositórios de plugins** e **conexões de provedores de dados** (WMS/WMTS, WFS/OGC API, WCS, XYZ,
Vector Tiles, ArcGIS, STAC, PostGIS, SpatiaLite, GeoPackage, MS SQL Server, Oracle, SAP HANA) entre
máquinas, perfis e versões do QGIS.

## O que é copiado

| Item | Copiado | Observação |
| --- | --- | --- |
| Lista de plugins instalados | Sim | Reinstalados a partir dos repositórios configurados. Plugins do núcleo (Processing, DB Manager, MetaSearch...) não são reinstalados. |
| Configurações de cada plugin | Sim | Chaves `<plugin>/...`, `plugins/<plugin>/...`, `plugin_<plugin>/...` e `PythonPlugins/<plugin>`. |
| Repositórios de plugins | Sim | Grupo `app/plugin_repositories`. |
| Conexões de provedores | Sim | Inclui o esquema antigo (`qgis/connections-*`, `<Provedor>/connections/...`) e a árvore nova do QGIS 3.30+ (`connections/.../items/...`). |
| Senhas e tokens | **Não, por padrão** | Podem ser incluídos marcando a opção; ficam em **texto puro** no JSON. |
| Configurações de autenticação (authcfg) | Não | Conexões que usam authcfg precisam da configuração criada na máquina de destino. |
| Plugins instalados por ZIP ou de repositório privado não configurado | Não | Aparecem como falha na restauração. |
| Projetos e dados | Não | Ver [issue #1](https://github.com/KuriakinToscan/BackupQgis/issues/1). |

## Restauração segura

1. **Simular** mostra, chave a chave, o que é novo, alterado ou igual.
2. **Restaurar** grava antes uma cópia do estado atual em
   `<pasta do perfil>/backupqgis/snapshots/` (últimas 10).
3. **Desfazer última restauração** regrava os valores anteriores e remove as chaves criadas.
   Plugins instalados não são desinstalados.
4. **Trocar caminho** substitui um prefixo (ex.: `C:/Users/ana` → `/home/ana`) nos valores restaurados.

Reinicie o QGIS após restaurar para que todas as configurações tenham efeito.

## Pendências

A aba Pendências lê `plugin_dependencies` (nomes de plugins), `requirements.txt` e campos
`python_dependencies`/`pip_dependencies` dos plugins ativos. A verificação usa
`importlib.metadata`/`importlib.util.find_spec` e **não importa** os pacotes. A instalação com `pip`
só ocorre após confirmação e usa o interpretador Python do QGIS (não o executável do QGIS). Se o
interpretador não for encontrado, instale manualmente (no Windows, pelo OSGeo4W Shell).

## Formato do arquivo

JSON com `schema_version: 2`, dados de origem (versão do QGIS, SO, máquina, perfil), chaves por
plugin e por conexão. Arquivos da versão 0.1 continuam sendo lidos.

## Estrutura do código

```
core/    lógica sem interface (chaves, conexões, dependências, formato, instalador)
tasks/   QgsTask (varredura, pip)
ui/      diálogo, prévia e componentes
tests/   pytest (core sem QGIS; interface com stubs em modo offscreen)
```

Testes: `pip install pytest PyQt5 && QT_QPA_PLATFORM=offscreen python -m pytest tests`.

Para gerar o ZIP do plugin sem testes: `git archive --prefix=BackupQgis/ -o BackupQgis.zip HEAD`.

## Licença

MIT.
