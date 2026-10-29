# BackupQgis

Plugin do QGIS para backup e restauração de **plugins**, **configurações de plugins (QSettings)**,
**repositórios de plugins** e **conexões de provedores de dados** (WMS/WMTS, WFS/OGC API, WCS, XYZ,
Vector Tiles, ArcGIS, STAC, PostGIS, SpatiaLite, GeoPackage, MS SQL Server, Oracle, SAP HANA) entre
máquinas, perfis e versões do QGIS, e para backup de **projetos com os dados** e sincronização de
pastas com HD externo ou NAS.

## Duas ferramentas independentes

O plugin tem duas entradas no menu **Complementos › BackupQgis** e na barra de ferramentas:

| | Configurações do QGIS | Projetos e dados |
| --- | --- | --- |
| Conteúdo | plugins, configurações, conexões, repositórios, autenticação | projetos `.qgz`/`.qgs` com os dados vinculados, pastas sincronizadas |
| Páginas | Exportar, Importar, Histórico, Pendências | Projetos, Sincronizar, Histórico |
| Automação | exportação ao fechar o QGIS e/ou a cada N dias | versão a cada salvamento, snapshot a cada N horas, sincronização a cada N minutos |
| Preferências | `BackupQgis/config/*` | `BackupQgis/projects/*`, `BackupQgis/sync/*` |

Cada janela tem destino, retenção, log, progresso e histórico próprios; uma operação ou automação
não dispara nem depende da outra. Preferências da 0.3.0 são migradas automaticamente.

## O que é copiado

| Item | Copiado | Observação |
| --- | --- | --- |
| Lista de plugins instalados | Sim | Reinstalados a partir dos repositórios configurados. Plugins do núcleo (Processing, DB Manager, MetaSearch...) não são reinstalados. |
| Configurações de cada plugin | Sim | Chaves `<plugin>/...`, `plugins/<plugin>/...`, `plugin_<plugin>/...` e `PythonPlugins/<plugin>`. |
| Repositórios de plugins | Sim | Grupo `app/plugin_repositories`. |
| Conexões de provedores | Sim | Inclui o esquema antigo (`qgis/connections-*`, `<Provedor>/connections/...`) e a árvore nova do QGIS 3.30+ (`connections/.../items/...`). |
| Senhas e tokens | **Não, por padrão** | Opção **Senhas: protegidas por authcfg (recomendado)**: a senha de cada conexão vira uma configuração de autenticação do QGIS e vai para `<backup>.authcfg.xml`, criptografado com uma senha escolhida na exportação; o JSON fica só com a referência `authcfg`. Opção avançada **texto puro**: senhas e tokens legíveis no JSON. |
| Configurações de autenticação (authcfg) | Com senhas protegidas | Todas as configurações existentes vão no mesmo `.authcfg.xml` (QGIS 3.20+). A importação é oferecida na restauração, antes de gravar as conexões, sem sobrescrever ids existentes. |
| Plugins instalados por ZIP ou de repositório privado não configurado | Sim, por padrão | Copiados para `<backup>.plugins.zip`. Na restauração, o repositório tem preferência; o ZIP é usado para os demais e extraído na pasta de plugins do perfil. |
| Projetos e dados | Sim, na página Projetos | Ver abaixo. |

## Restauração segura

1. **Simular** mostra, chave a chave, o que é novo, alterado ou igual.
2. **Restaurar** grava antes uma cópia do estado atual em
   `<pasta do perfil>/backupqgis/snapshots/` (últimas 10).
3. **Desfazer última restauração** regrava os valores anteriores e remove as chaves criadas.
   Plugins instalados não são desinstalados.
4. **Senhas protegidas**: com um `.authcfg.xml` ao lado do backup, o plugin pede a senha do arquivo
   e importa as autenticações antes de gravar as conexões, que passam a usá-las (a senha mestra do
   QGIS de destino é pedida pelo próprio QGIS). O banco de autenticação de origem não é alterado:
   as configurações criadas para a exportação são removidas logo após.
5. **Trocar caminho** substitui um prefixo (ex.: `C:/Users/ana` → `/home/ana`) nos valores restaurados.

Reinicie o QGIS após restaurar para que todas as configurações tenham efeito.

## Backup de projetos

Na janela **Projetos e dados**, a página **Projetos** inventaria projetos `.qgz`/`.qgs` **sem abri-los no QGIS** (o XML é lido
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

- **Empacotar...** copia o projeto e os dados para `<pasta>/<projeto>/` (ou `<projeto>.zip`) e
  reescreve as fontes das camadas para caminhos relativos, de modo que o pacote abre em qualquer
  computador. A estrutura de pastas é mantida a partir da pasta comum a projeto e dados (ou espelha
  o caminho absoluto, quando estão em unidades diferentes). Camadas de banco e serviços continuam
  apontando para o servidor original.
- **Restaurar snapshot...** devolve os arquivos aos caminhos originais (idênticos são mantidos;
  diferentes só são substituídos se você escolher, e a versão atual vira
  `*.antes-restauracao-<data>`) ou recria tudo numa pasta nova, com os projetos reescritos para os
  arquivos restaurados.
- **Guardar versão do projeto a cada salvamento** copia o `.qgz`/`.qgs` (sem os dados) para
  `<destino>/versoes/<projeto>/` a cada salvamento no QGIS, mantendo as N versões mais recentes.

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

**Automático a cada N minutos** (pares aditivo e espelho): roda em segundo plano enquanto o QGIS
estiver aberto, nunca força a trava de segurança e só registra no log quando o destino está
desconectado. A primeira execução ocorre após um intervalo completo.

**Bidirecional (experimental)**: propaga criações, alterações e exclusões nos dois sentidos com
base no estado da última sincronização (`<perfil>/backupqgis/sync_state/<par>.json`). Quando o mesmo
arquivo mudou dos dois lados, o mais recente vence e o outro é preservado como
`<nome>.conflito-<máquina>-<data><ext>`; exclusões vão para a lixeira do lado correspondente. Na
primeira execução não há exclusões, só cópias. Não tem modo automático.

**Snapshot automático a cada N horas** faz o backup da lista de projetos em segundo plano enquanto o
QGIS estiver aberto. O **Histórico** lista os snapshots do destino (manual ou agendado, arquivos,
tamanho, falhas), com **Verificar integridade** (SHA-256 contra o manifesto) e **Restaurar**.

## Exportação automática das configurações

Na janela **Configurações do QGIS**, página Exportar, a exportação automática grava plugins
ativos, configurações, conexões e repositórios (sem senhas) em `qgis-config_<máquina>_<data>.json`
na pasta escolhida, **ao fechar o QGIS** e/ou **a cada N dias**, mantendo os N mais recentes.
Apontar para uma pasta sincronizada com o NAS dá um histórico de configuração sem intervenção. O
**Histórico** reúne backups manuais e automáticos e abre qualquer um deles na página Importar.

## Pendências

A aba Pendências lê `plugin_dependencies` (nomes de plugins), `requirements.txt` e campos
`python_dependencies`/`pip_dependencies` dos plugins ativos. A verificação usa
`importlib.metadata`/`importlib.util.find_spec` e **não importa** os pacotes. A instalação com `pip`
só ocorre após confirmação e usa o interpretador Python do QGIS (não o executável do QGIS). Se o
interpretador não for encontrado, instale manualmente (no Windows, pelo OSGeo4W Shell).

## Formato do arquivo

JSON com `schema_version: 2`, dados de origem (versão do QGIS, SO, máquina, perfil), chaves por
plugin e por conexão. Arquivos da versão 0.1 continuam sendo lidos.

## Traduções

Os textos-fonte estão em português. O plugin carrega `i18n/BackupQgis_<idioma>.qm` conforme o
idioma do QGIS. Para traduzir: `pylupdate5 i18n/BackupQgis.pro`, editar o `.ts` no Qt Linguist e
gerar o `.qm` com `lrelease`.

## Estrutura do código

```
core/    lógica sem interface (chaves, conexões, dependências, formato, instalador)
tasks/   QgsTask (varredura, pip)
ui/      janelas (dialog: configurações; projects_dialog: projetos e dados), moldura comum (shell),
         páginas, automações e componentes
tests/   pytest (core sem QGIS; interface com stubs em modo offscreen)
```

Testes: `pip install pytest PyQt5 && QT_QPA_PLATFORM=offscreen python -m pytest tests`.

Integração com QGIS real (sem stubs; no CI roda com o QGIS 3.34 LTR do Ubuntu 24.04):
`HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen python3 tests/qgis_real/run_real.py`. Cobre projetos
gravados pelo QGIS (inventário, pacote movido e snapshot restaurado reabertos com camadas válidas),
conexões criadas pela API e pela árvore 3.30+, exportação/restauração pelo diálogo, authcfg com
senha mestra, instalação de plugin pelo ZIP e os sinais `projectSaved`/`aboutToQuit`.

Para gerar o ZIP do plugin sem testes: `git archive --prefix=BackupQgis/ -o BackupQgis.zip HEAD`.

## Licença

MIT.
