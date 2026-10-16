# Auditoria técnica — Sincronizador de Plugins e Configurações (BackupQgis)

Data: 2026-10-16 · Commit auditado: `f15e04b` · Escopo: todo o repositório

## 1. Visão geral

| Item | Situação |
|---|---|
| Código Python | 3 arquivos, ~1 350 linhas; 94% em um único arquivo (`backup_plugin_dialog.py`, 1 273 linhas) |
| Testes automatizados | Nenhum |
| CI / lint | Nenhum (pyflakes acusa 2 imports e 2 variáveis não usados) |
| Documentação | Sem README, sem changelog, sem guia de uso |
| Internacionalização | `tr()` existe só em `backup_plugin.py`; o diálogo tem todas as strings fixas em português |
| Compatibilidade | Declara QGIS 3.0–3.99; usa APIs que não existem ou mudaram nesse intervalo (ver abaixo) |

O plugin tem três funções: **exportar** (plugins, configs de plugins e conexões de provedores para JSON), **importar/restaurar** e **verificar/corrigir pendências** (plugins QGIS e pacotes pip). A interface é bem pensada (seleção granular, filtros, log), mas várias rotinas centrais não funcionam como prometido e há riscos de segurança relevantes.

Legenda de severidade: 🔴 crítico · 🟠 alto · 🟡 médio · ⚪ baixo.

> Observação: a auditoria foi feita por leitura de código e análise estática; não havia QGIS disponível para execução. Os pontos que dependem do comportamento interno do QGIS estão sinalizados como tal.

---

## 2. Segurança

### 🔴 S1 — Senhas exportadas em texto puro
`scan_server_connections()` (`backup_plugin_dialog.py:554`) copia **todas** as chaves de cada conexão, incluindo `username` e `password` salvos de PostGIS, Oracle, MSSQL, HANA, WMS/WFS com autenticação básica etc. O JSON é gravado sem aviso, sem cifragem e sem opção de excluir credenciais. Um backup compartilhado ou guardado em nuvem vaza as credenciais de banco.

**Proposta:** por padrão, omitir chaves `password`/`username` (e similares); oferecer um checkbox "Incluir credenciais" com aviso explícito; idealmente cifrar com senha (ex.: `cryptography`/Fernet ou o próprio `QgsAuthManager` exportando um `.xml` cifrado).

### 🔴 S2 — Importar um backup não confiável permite execução de código
Um arquivo de backup é tratado como confiável:
- `repositories` é mesclado sem validação em `restore_backup()` (`:974`);
- `plugins[*].key` é instalado e **ativado** (`:994–1050`);
- em `server_connections`, qualquer chave listada em `providers[*].keys` é gravada no QSettings sem verificar se pertence a um namespace de conexão (`:1038`), e no modo legado **todas** as chaves são gravadas (`:1043–1046`).

Um JSON malicioso pode, portanto, gravar chaves arbitrárias no perfil (ex.: `PythonPlugins/…`, repositórios) e levar à instalação/ativação de plugins — ou seja, código Python arbitrário.

**Proposta:** whitelist de prefixos permitidos por categoria; tela de pré-visualização/confirmação antes de gravar; nunca adicionar repositórios sem confirmação item a item; validar o JSON contra um schema.

### 🟠 S3 — `pip install` automático de nomes vindos de metadados de terceiros
`fix_dependencies()` instala o que estiver declarado em `requirements.txt`/`metadata.txt` de qualquer plugin, sem confirmação e sem mostrar a lista. Nomes mal parseados (ver B7) ou typosquatting viram instalações reais.

**Proposta:** diálogo de confirmação listando pacotes e versões; permitir desmarcar; registrar o comando executado.

### 🟡 S4 — `importlib.import_module` em pacotes arbitrários na thread da GUI
`check_dependencies()` (`:1168`) importa de fato cada dependência para testar se existe. Importar executa código do pacote (efeitos colaterais, lentidão, travamento da UI).

**Proposta:** usar `importlib.util.find_spec()` ou `importlib.metadata.distribution()` (que não executa o pacote).

---

## 3. Bugs funcionais

### 🔴 B1 — `sys.executable` não é o Python no Windows/macOS
`DependencyFixerWorker.run()` (`:173`) executa `[sys.executable, "-m", "pip", ...]`. Dentro do QGIS no Windows, `sys.executable` aponta para `qgis-bin.exe`/`qgis.exe`; no macOS, para o binário do app. O resultado é abrir **outra instância do QGIS** em vez do pip. A correção de pacotes Python só funciona no Linux.

**Proposta:** localizar o interpretador (`os.path.join(sys.exec_prefix, 'python.exe')` / `python3`), validar com `--version`, e cair para instruções manuais (OSGeo4W Shell) quando não encontrado. `--user` também falha em venv/conda — detectar `sys.prefix != sys.base_prefix`.

### 🔴 B2 — Instalador de plugins do QGIS chamado de dentro de uma `QThread`
`pyplugin_installer` (`:141–157`) usa rede via `QgsNetworkAccessManager`, sinais da thread principal e **abre diálogos modais** durante `installPlugin`. Chamá-lo de uma thread secundária é comportamento indefinido no Qt (travamentos, crash, "QObject: Cannot create children for a parent that is in a different thread"). Além disso, `fetchAvailablePlugins` é assíncrono e não garante que a lista esteja carregada quando o laço começa.

Também (comportamento do QGIS, a confirmar em execução): `installPlugin()` não retorna booleano, portanto `success` é `None` e o log sempre diz "Falha ao instalar".

**Proposta:** fazer a instalação de plugins na thread principal (é rápida e já tem feedback próprio do QGIS) ou baixar o zip manualmente em `QgsTask` e instalar com `pyplugin_installer.installer_utils.unzip` + `qgis.utils.loadPlugin/startPlugin`. Verificar sucesso consultando `qgis.utils.available_plugins` depois.

### 🔴 B3 — Repositórios de plugins nunca são salvos nem restaurados
O código lê/grava `pythonPlugins/pluginRepositories` (`:831`, `:977`), chave que o QGIS não usa. Os repositórios ficam em `app/plugin_repositories/<nome>/{url,enabled,authcfg}`. Resultado: o campo `repositories` do backup é sempre `{}`, e plugins vindos de repositórios customizados não podem ser instalados na máquina de destino.

**Proposta:** exportar o grupo `app/plugin_repositories` (sem `authcfg` sensível ou com aviso) e restaurá-lo antes de instalar.

### 🟠 B4 — Configurações em `plugins/<chave>/` são exportadas mas nunca restauradas
A coleta (`collect_plugin_settings`, `:746`) inclui o prefixo `plugins/{key}/` — o mais comum, pois é a seção `Plugins` do `QgsSettings`. A restauração (`:1009`) testa uma lista diferente de prefixos **sem** `plugins/{key}/`. Além disso a coleta é case-insensitive e a restauração case-sensitive. Boa parte das configurações exportadas é silenciosamente descartada no import.

**Proposta:** uma única função `belongs_to_plugin(key, plugin_key)` usada nos dois sentidos. Melhor ainda: gravar no backup o mapeamento `plugin_key -> [chaves]` (como já é feito para provedores) e restaurar exatamente essas chaves.

### 🟠 B5 — Prefixos que capturam configurações demais
- `startswith(f"PythonPlugins/{sel_key}")` sem `/` faz `foo` capturar `PythonPlugins/foobar`.
- `plugin_paths` inclui plugins de sistema (`processing`, `db_manager`, `MetaSearch`, `grassprovider`). O prefixo `processing/` arrasta todas as configurações do Processing — inclusive caminhos de scripts e provedores da máquina de origem.

**Proposta:** comparar a chave inteira (`== f"PythonPlugins/{k}"`); marcar plugins core como "core" e tratá-los à parte (não instalar, configurações opcionais).

### 🟠 B6 — Conexões do QGIS ≥ 3.30 não são detectadas (comportamento do QGIS, a confirmar)
O scanner só reconhece `…/connections-<tipo>/<nome>/…` ou `qgis|plugins/<tipo>/<nome>`. A partir do QGIS 3.30 várias conexões (XYZ, WMS/WFS/WCS, vector tiles, ArcGIS) migraram para a árvore `QgsSettingsTree`, com chaves do tipo `connections/xyz/items/<nome>/url`. Nesse formato o laço não encontra o tipo e a conexão some do backup. Ao mesmo tempo, chaves como `qgis/connections-postgres/selected` geram uma "conexão" falsa chamada `selected`, e `postgresql` aparece como `POSTGRESQL` porque o mapa usa `postgis`.

**Proposta:** usar a API oficial em vez de varrer chaves: `QgsProviderRegistry.instance().providerMetadata(p).connections()` + `QgsAbstractProviderConnectionMetadata` (3.10+), e `QgsSettingsTree` (3.30+) para as conexões web; tratar os sufixos `selected`, `items` etc.

### 🟠 B7 — Parser de dependências frágil
`extract_plugin_dependencies()` (`:1071`) não trata `~=`, `!=`, `===`, extras (`pkg[extra]`), marcadores (`; python_version<"3.8"`), opções (`-r`, `-e`, `--index-url`), URLs `git+https://…`. Uma linha `-r base.txt` vira o pacote `-r`.

Além disso, `plugin_dependencies` no `metadata.txt` lista **nomes** de plugins (campo `name`), não a pasta. O código compara com as chaves de pasta → falsos "Ausente" e `installPlugin(nome)` falha.

**Proposta:** usar `packaging.requirements.Requirement` (vem com o pip) para requirements; para plugins, resolver nome → chave via `pyplugin_installer.installer_data.plugins.all()`. Respeitar as versões.

### 🟠 B8 — Serialização corrompe tipos não previstos
`serialize_value()` faz fallback para `str(val)` (`:70`) e `deserialize_value()` grava essa string de volta. Um `QColor`, `QFont`, `QKeySequence` etc. vira algo como `"<PyQt5.QtGui.QColor object at 0x…>"` gravado no perfil de destino, corrompendo a configuração.

**Proposta:** serializar `QVariant` genérico com `QDataStream` → base64 (cobre todos os tipos Qt), ou **pular** tipos desconhecidos em vez de gravar a string.

### 🟡 B9 — Ciclo de vida da thread
- `on_worker_finished` faz `self.worker = None` enquanto `run()` ainda não retornou (o sinal é emitido de dentro do `run`) → o objeto `QThread` pode ser coletado em execução ("QThread: Destroyed while thread is still running", crash).
- `cancel()` existe mas não há botão; fechar o diálogo ou descarregar o plugin (`unload`) com a thread rodando não a interrompe.
- O processo `pip` não é encerrado no cancelamento.

**Proposta:** substituir `QThread` por `QgsTask` (barra de progresso nativa, cancelamento, integração com o gerenciador de tarefas) ou conectar `finished` → `deleteLater` e aguardar/cancelar em `closeEvent`/`unload`.

### 🟡 B10 — Restauração incompleta/confusa
- Plugins ativados via `PythonPlugins/<key>=True` só carregam após reiniciar o QGIS; não há aviso nem chamada a `loadPlugin/startPlugin`.
- Todos os plugins selecionados são ativados, mesmo os que estavam **inativos** na origem (o campo `active` do backup é ignorado).
- Quando há plugins a instalar, o resumo de configurações/provedores restaurados nunca é exibido.
- A versão do backup é ignorada; sempre instala a última disponível.
- Plugins instalados manualmente (zip, git) não existem no repositório e falham sem orientação.

### 🟡 B11 — Validação do arquivo de backup
`refresh_import_table` acessa `plugin['name']` etc. diretamente; um JSON com campo faltando ou `version` numérico gera `KeyError`/`TypeError` não tratado. O campo `version: '0.1'` é fixo e não há migração de formato.

### ⚪ B12 — Outros
- `QSettings` instanciado dentro do laço de cada plugin; `settings.allKeys()` percorrido uma vez **por plugin** em `collect_plugin_settings` (custo O(plugins × chaves)).
- O diálogo escaneia tudo no construtor e reaproveita os dados ao reabrir (dados desatualizados até exportar de novo).
- Duas caixas de mensagem seguidas após a correção (fim do worker + `check_dependencies`).
- Diálogo sem pai (`parent=None`) — não fica preso à janela principal.

---

## 4. Compatibilidade

| Problema | Onde | Efeito |
|---|---|---|
| `text=True` no `Popen` exige Python 3.7; f-strings exigem 3.6 | `:184` | QGIS 3.0–3.4 (Python 3.5/3.6 em algumas distribuições) quebra |
| Enums não-escopados (`Qt.Checked`, `QHeaderView.Stretch`, `textCursor().End`, `Qt.ISODate`) | todo o diálogo | Incompatível com Qt6 / QGIS 4 |
| `qgisMaximumVersion=3.99` | `metadata.txt` | Bloqueia QGIS 4 sem migração |
| Cores fixas (verde/cinza/vermelho escuro) | tabelas | Ilegível em temas escuros |

**Proposta:** subir `qgisMinimumVersion` para 3.22 (LTR mais antiga razoável), usar enums escopados (`Qt.CheckState.Checked`, compatíveis com PyQt5 ≥ 5.11 e PyQt6) e declarar `supportsQt6=True` depois de testar no QGIS 4.

---

## 5. Metadados e empacotamento (bloqueiam publicação no repositório oficial)

- `author=Antigravity` / `email=antigravity@gemini.ai` — valores de ferramenta, não do autor real. O repositório oficial exige autor e e-mail válidos.
- `category=Plugins` não é uma categoria válida (válidas: Raster, Vector, Database, Web, Mesh, Analysis). Para um plugin utilitário como este, o melhor é remover a linha `category` e deixá-lo no menu *Plugins*.
- Faltam `homepage`, `tracker` (obrigatório) e `changelog`.
- `experimental=False` para uma versão 0.1 — deveria ser `True`.
- Inconsistência de versões: commit "v1.0.0", `metadata.txt` 0.1, `version` do JSON 0.1 duplicado no código.
- `icon.png` tem 1040×1043 px e **1,1 MB** (o próprio zip do plugin ficará grande); o recomendado é ~64–128 px e poucos KB. `icon_old.png` não é usado.
- Nome do diretório/plugin ("BackupQgis") difere do `name` no metadata.

---

## 6. Arquitetura e qualidade de código

- **Arquivo monolítico:** UI, varredura de configurações, serialização, parser de dependências e worker estão todos em `backup_plugin_dialog.py`. Nada disso pode ser testado sem abrir a UI.
- **Duplicação:** seis métodos `select_all_*` quase idênticos; criação de checkbox repetida em seis lugares; o "strip de versão" repetido três vezes; listas de prefixos divergentes (causa direta de B4).
- **Exceções engolidas:** `except Exception as e: pass` em `:1107` e `:1123` — o usuário nunca sabe que um `metadata.txt` falhou.
- **Lógica dependente do texto da tabela:** `fix_dependencies` decide pelo texto exibido (`'Ausente'`, `'Plugin QGIS'`); qualquer tradução quebra a lógica. Guardar dados no `Qt.UserRole` do item.
- **Sem log persistente:** usar `QgsMessageLog` além do `QTextEdit`, para diagnóstico.
- **Imports não usados:** `QCheckBox`, `QIcon` (pyflakes).

---

## 7. Plano de melhorias proposto

### Fase 1 — Correções urgentes (segurança e funcionamento)
1. Excluir credenciais do export por padrão + opção explícita com aviso (S1).
2. Pré-visualização/confirmação e whitelist de chaves no import (S2); confirmação antes do pip (S3).
3. Corrigir o interpretador Python usado pelo pip (B1).
4. Tirar `pyplugin_installer` da thread secundária (B2).
5. Usar `app/plugin_repositories` (B3).
6. Unificar a regra de pertencimento de chaves a um plugin (B4, B5).
7. Parar de gravar `str()` de tipos desconhecidos (B8).

### Fase 2 — Robustez
8. Detectar conexões via `QgsProviderRegistry`/`QgsSettingsTree` (B6).
9. Parser com `packaging`; dependências de plugin por nome (B7).
10. `QgsTask` com cancelamento e tratamento em `unload` (B9).
11. Schema versionado do backup (`format_version: 2`) com validação e migração (B11).
12. Respeitar `active` do backup e avisar sobre reinício (B10).

### Fase 3 — Estrutura e manutenção
13. Reorganizar em módulos:
    ```
    core/serialization.py   # serialize/deserialize (testável sem UI)
    core/settings_scan.py   # plugins, configs, conexões, repositórios
    core/dependencies.py    # parser + verificação
    core/backup_io.py       # schema, leitura/gravação, validação
    tasks/installer_task.py # QgsTask
    ui/main_dialog.py       # só interface
    ```
14. Testes com `pytest` para `core/*` (serialização ida-e-volta, parser de requirements, filtro de chaves) e `pytest-qgis` para integração.
15. GitHub Actions: `flake8`/`ruff`, testes em contêiner `qgis/qgis:ltr` e `qgis/qgis:latest`.
16. i18n: `self.tr()` em todas as strings + `i18n/*.ts` (pt_BR, en).
17. README com instruções, capturas de tela e alerta sobre credenciais; `CHANGELOG`.
18. Corrigir `metadata.txt`, reduzir o ícone, remover `icon_old.png`, preparar empacotamento (`qgis-plugin-ci` ou `pb_tool`).

### Ideias de funcionalidade (depois das fases acima)
- Backup cifrado com senha, ou integração com `QgsAuthManager` para migrar credenciais com segurança.
- Incluir também: estilos (`symbology-style.db`), modelos/scripts do Processing, templates de layout, expressões personalizadas, atalhos.
- Diff entre backup e máquina atual antes de restaurar.
- Modo linha de comando (`qgis_process` ou script) para provisionar várias máquinas.
- Opção de empacotar no backup os plugins que não vêm de repositório (zip da pasta).

---

## 8. Resumo

A ideia e a interface são boas, mas hoje **três das funções anunciadas não funcionam de forma confiável**: restauração de repositórios (B3), restauração de boa parte das configurações (B4) e correção de pacotes Python fora do Linux (B1); a instalação de plugins roda numa thread onde o QGIS não suporta (B2). Além disso o backup **expõe senhas** (S1) e um backup de terceiros pode **instalar e ativar código** (S2). Recomenda-se tratar a Fase 1 antes de qualquer publicação no repositório oficial do QGIS.

---

## 9. Andamento

Modularização (etapas 1–4) concluída na branch `claude/serene-lamport-6qe2lz`:

| Módulo | Conteúdo |
|---|---|
| `core/serialization.py` | `serialize_value` / `deserialize_value` |
| `core/dependencies.py` | leitura de `metadata.txt`/`requirements.txt` e verificação de pendências |
| `core/plugins.py` | varredura de plugins e regra única de chaves por plugin |
| `core/connections.py` | varredura de conexões de provedores |
| `core/backup.py` | montar, ler, validar e restaurar o JSON |

- **B4 corrigido** (configurações em `plugins/<chave>/` voltam a ser restauradas; comparação sem diferenciar maiúsculas).
- **B5 parcialmente corrigido** (chave de ativação exata). Falta tratar plugins core (`processing` etc.).
- **Novo achado, corrigido:** backups antigos sem a lista `providers` exibiam as conexões mas não restauravam nenhuma.
- Testes: `pip install -r requirements-dev.txt` e `QT_QPA_PLATFORM=offscreen python -m pytest tests` (47 testes, incluindo um fluxo exportar → carregar → restaurar pelo diálogo).

Próximas etapas: divisão da UI (`ui/`), worker de instalação (`tasks/`), CI, e as correções de segurança S1/S2.
