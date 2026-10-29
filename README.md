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
| Projetos e dados | Sim, na página Projetos | Ver abaixo. |

## Restauração segura

1. **Simular** mostra, chave a chave, o que é novo, alterado ou igual.
2. **Restaurar** grava antes uma cópia do estado atual em
   `<pasta do perfil>/backupqgis/snapshots/` (últimas 10).
3. **Desfazer última restauração** regrava os valores anteriores e remove as chaves criadas.
   Plugins instalados não são desinstalados.
4. **Trocar caminho** substitui um prefixo (ex.: `C:/Users/ana` → `/home/ana`) nos valores restaurados.

Reinicie o QGIS após restaurar para que todas as configurações tenham efeito.

## Backup de projetos

A página **Projetos** inventaria projetos `.qgz`/`.qgs` **sem abri-los no QGIS** (o XML é lido
direto) e copia para um snapshot `AAAA-MM-DD_HHMMSS` no destino:

- dados de cada camada de arquivo e seus auxiliares (componentes de shapefile, `.ovr`, `.aux.xml`,
  world files, `.qml`, `.qmd`, fontes de `.vrt`), SVGs/imagens referenciados e o `.qgd`;
- GeoPackage/SpatiaLite pela API de backup do SQLite, o que gera cópia íntegra mesmo com o arquivo
  aberto e em edição no QGIS;
- `manifest.json` com SHA-256 de cada arquivo (verificado após a cópia), camadas remotas (URI sem
  senha), camadas em memória e fontes ausentes.

O caminho absoluto original é espelhado dentro do snapshot (`arquivos/C/...`, `arquivos/home/...`),
preservando os caminhos relativos entre projeto e dados. A cópia é feita em `<snapshot>.parcial` e só
é renomeada no fim; cancelar não deixa snapshot incompleto. A retenção apaga apenas snapshots antigos
criados pelo próprio plugin. O destino precisa existir: com o HD/NAS desconectado o backup não começa.

## Sincronização entre pastas

A página **Sincronizar** mantém pares origem → destino (ex.: `D:/SIG` → `//nas/backup/SIG`):

- **Aditivo**: copia novos e alterados; nunca apaga no destino.
- **Espelho**: remove do destino o que não existe mais na origem, movendo para
  `.backupqgis_trash/AAAA-MM-DD_HHMMSS/` no próprio destino.

Proteções: simulação obrigatória antes de cada execução; marcador `.backupqgis-target.json` com UUID
no destino (reconhece o disco mesmo após troca de letra no Windows e impede gravar no disco local
quando o HD/NAS não está montado); origem vazia aborta; espelho que apagaria mais de 20% dos arquivos
do destino pede confirmação; cópia atômica que preserva a data de modificação; tolerância de 2 s
na comparação (FAT/exFAT/SMB); arquivo mais recente no destino não é sobrescrito.

Fora do escopo atual: sincronização bidirecional, gatilhos automáticos e restauração de snapshot
pela interface (fase 3 da issue #1).

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
