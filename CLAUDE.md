# CLAUDE.md: radar-fertilizantes-br

Radar das importações brasileiras de fertilizantes (capítulo 31, posições 3102 a 3105),
feito só com dados abertos oficiais: Comex Stat/MDIC, Pink Sheet do Banco Mundial e PTAX do
BCB. **O repositório é PÚBLICO.**

## Regras inegociáveis

### Segurança (repositório público)

- NUNCA escrever chaves, tokens, senhas, URLs privadas ou credenciais em código, config,
  README, testes ou commits.
- Qualquer segredo (ex.: `ANTHROPIC_API_KEY`, da fase 5) vem SOMENTE de variável de
  ambiente. Os nomes ficam em `.env.example`, com valores vazios.
- Nenhuma chave está nem vai estar nas sessões de desenvolvimento: nunca pedir nem tentar
  ler chaves (nem `.env`, nem variáveis de ambiente). Testes e o modo `--mock` não usam chave.
- `.env`, `.env.*` (exceto `.env.example`), `*.key`, `*.pem` e `secrets/` ficam no
  `.gitignore`. Não remover essas linhas.
- Antes de CADA commit, rodar `git diff --cached` e conferir que não há segredos.
  O pre-commit com gitleaks (`.pre-commit-config.yaml`) bloqueia vazamentos. Se o hook
  não estiver instalado na sessão, rodar o mesmo comando dele:
  `gitleaks git --pre-commit --redact --staged --verbose`.
- Usar apenas dados abertos oficiais, de governo ou de organismos internacionais (ex.: Banco
  Mundial), com licença que permita redistribuição. Nunca dados de empresas ou clientes.
- Toda saída pública (README, dashboard, boletim) cita todas as fontes que usa, no formato
  "Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX".

### Autoria dos commits

- Commits saem SOMENTE com a identidade git do mantenedor:
  `Marcus Santos <77951123+AliceCullen-html@users.noreply.github.com>`.
- Única exceção, autorizada pelo mantenedor: os commits automáticos da fase 4
  (`.github/workflows/atualizacao.yml`) saem como
  `github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>`, sempre com a
  mensagem `chore(dados): atualização AAAA-MM` e só com arquivos de `data/` e `boletins/`.
- NÃO adicionar `Co-Authored-By: Claude`, "Generated with Claude Code", link de sessão
  nem nenhum trailer ou menção ao Claude em mensagens de commit ou descrições de PR.
  O `.claude/settings.json` desliga essa atribuição (`includeCoAuthoredBy: false` e
  `attribution` vazio).
- Em sessões na nuvem, o git vem configurado como "Claude" e assina os commits com uma
  chave própria. Antes de commitar, configurar o repositório:

  ```bash
  git config user.name "Marcus Santos"
  git config user.email "77951123+AliceCullen-html@users.noreply.github.com"
  git config commit.gpgsign false
  ```

- Mensagens de commit em português, no padrão Conventional Commits
  (`feat:`, `fix:`, `docs:`, `chore:`).

## Como trabalhar aqui

- Trabalhar a partir de `main`, o branch padrão no GitHub. Force-push só com autorização
  explícita.
- Python 3.10+. Dependências em `requirements.txt` (requests, pandas, pyarrow, openpyxl); as
  de desenvolvimento em `requirements-dev.txt`.
- Testes: `python -m pytest`. Eles não acessam a rede (as APIs são simuladas).
- CI (`.github/workflows/ci.yml`): pytest em Python 3.10 (pandas 2) e 3.13 (pandas 3) e
  gitleaks em todo o histórico, em push e PR. Só leitura e sem nenhum segredo.
- Actions sempre em versões que rodam em Node 24 (`checkout@v6`, `setup-python@v6`,
  `setup-go@v6`, `configure-pages@v6`, `upload-pages-artifact@v5`, `deploy-pages@v5`).
  Antes de trocar uma versão, conferir `runs.using` no `action.yml` da tag.
- Ingestão: `python -m src.ingest --inicio AAAA-MM [--fim AAAA-MM] [--serie todas|fertilizantes|soja|benchmark|cambio]`.
  Opções em `python -m src.ingest --help`. `--ultimo-gravado` imprime o último mês completo,
  o menor entre as quatro séries, sem chamar nenhuma fonte (`--por-serie`: uma linha por série).
- Dados brutos: `data/raw/ano=AAAA/importacoes.parquet`, de 2015-01 a 2026-09 (61.460
  linhas, 1,6 MB). A coluna `ano` fica no caminho, não dentro do arquivo; leia com
  `src.ingest.ler_raw()`. A exportação de soja em grão (NCM 12019000, só FOB e kg) fica em
  `data/raw_soja/ano=AAAA/exportacoes.parquet`; leia com `src.ingest.ler_soja()`. A Pink
  Sheet fica em `data/raw_benchmark/pink_sheet_mensal.csv` (US$/t nominal; `fonte.json` com
  edição, URL, sha256 e licença) e a PTAX em `data/raw_cambio/ptax_venda_media_mensal.csv`;
  leia com `src.benchmark.ler_pink()` e `ler_ptax()`.
- Rotas (`rotas_ano.csv` e bloco "rotas" do dashboard.json): país de origem → URF por ano.
  Frete por rota só para rotas com ≥ 50 mil t no ano (`CORTE_ROTA_T`, proporcional em ano
  parcial). O Sankey é SVG próprio, sem lib externa.
- Relação de troca (`src/troca.py`): preço CIF de KCl 31042090, ureia 31021010 e MAP
  31054000 ÷ preço FOB da soja no mesmo mês. É "no porto", não o preço ao produtor; dizer
  isso em toda saída pública que a mostrar. É US$/US$ e NÃO muda com o câmbio: nunca criar
  "troca em R$".
- Fase 2: `python -m src.transform` gera em `data/processed/` os CSVs de preço US$/t,
  shares por origem e URF com variação anual, HHI, sazonalidade e o `dashboard.json`.
  As saídas são determinísticas; regenere-as sempre que os dados brutos mudarem.
- Fase 3: `dashboard/index.html` é um HTML único (Chart.js 4.5.1 do jsDelivr, com SRI) que
  lê `dashboard.json`. Para ver localmente: `python -m http.server` na raiz e abrir
  `/dashboard/`. O deploy é feito pelo `.github/workflows/pages.yml` (push na main que mude
  `dashboard/`, o `dashboard.json` ou o `boletins/latest.json`, ou manual), sem segredos. Pré-requisito: Settings →
  Pages → Source: "GitHub Actions".
- Fase 4: `.github/workflows/atualizacao.yml` roda nos dias 1 a 15 de cada mês. Se o mês
  anterior já está gravado (`--ultimo-gravado`), encerra sem chamar nenhuma fonte; senão faz a
  ingestão incremental das quatro séries, o transform e os testes e, se o mês completo
  avançou ou se há boletim novo (o do último mês faltava, ou foi refeito por dispatch), commita só `data/`
  e `boletins/` como github-actions[bot] e chama o `pages.yml` (via `workflow_call`, porque
  pushes com o GITHUB_TOKEN não disparam outros workflows). Os testes rodam sobre o mês
  recém-gravado: nunca fixar valores do último mês nos testes com dados versionados, nem
  supor que as quatro séries terminam no mesmo mês. Mês completo = gravado nas quatro séries
  (Comex Stat de fertilizantes e de soja, Pink Sheet, PTAX); se faltar alguma, nada é
  commitado e o run fica verde com aviso de qual falta; a partir do dia 15, o job `prazo`
  (sem permissões nem checkout) fica vermelho dizendo qual. Falha de uma fonte (ou mês publicado
  que não vem, mesmo no meio do período) = job vermelho, nada commitado. Privilégios
  separados: o job que roda Python é só leitura e entrega `data/` como artifact; o job do
  commit (único com `contents: write`) só roda git, com hooks desligados, e recusa mudança
  fora de `data/` e `boletins/`. Manter essa separação.
- Fase 5: `src/llm.py` (provedores: `RADAR_LLM_PROVIDER` = anthropic, padrão com
  `claude-sonnet-5-5`; openai, qualquer API compatível; ollama, local) e `src/boletim.py`
  (pacote de fatos com valores formatados em pt-BR → IA escreve só com marcadores `{chave}` →
  validação igual para todos os provedores → `boletins/AAAA-MM.md` e `latest.json`). Os dois
  usam SÓ a biblioteca padrão: o job `gerar-boletim` é o único com o secret (environment
  `boletim-ia`) e não instala pacotes. A direção NUNCA é escrita pela IA: entra só pelos
  marcadores `_sentido`/`_posicao`, colados à própria variação. Nunca relaxar a validação
  (caracteres fora de letras e pontuação comum, dígito, número por extenso, mês, período,
  contagem, recorde, unidade ou sinal fora de marcador, palavra de direção ou de juízo,
  direção negada, continuidade, causa/previsão/contexto, região, produto ou nome próprio fora
  do pacote, marcador inexistente ou colado, número ligado ao grupo/produto/total/país/porto/
  posição/período/métrica de outro, troca sem "no porto" ou como preço ao produtor, link,
  Markdown, lista de termos de tom, "ágio"/"sobrepreço", tamanho). Casos ruins e corretos das revisões ficam nos
  testes; regra nova não pode reprovar o mock em nenhum mês dos dados. Nunca usar
  `pull_request_target`. Boletim do `--mock` vai para `boletins/mock/` (fora do Git) e nunca
  é commitado. Rodapé e fontes do boletim incluem o Banco Mundial e o BCB quando o texto cita
  números deles.
- Fase 6: `src/benchmark.py` (ingestão da Pink Sheet e da PTAX, sem
  chave) e `src/comparacao.py` (`benchmark_mensal.csv` e bloco "benchmark" do
  dashboard.json). Tabela fixa NCM → série: KCl 31042090 → Potassium chloride; ureia
  31021010 → Urea; MAP 31054000 → DAP (aproximação, a Pink Sheet não tem MAP); DAP
  31053000/31053010/31053090 → DAP; TSP 31031030/31031100 → TSP. Rocha fosfática só é
  ingerida. Base comparável: FOB×FOB (ureia, MAP/DAP, KCl até 2019-12); CIF×CFR (KCl desde
  2020-01, TSP). Amostra pequena: < 10 mil t no mês (`CORTE_AMOSTRA_T`), fora dos gráficos e
  do boletim. CIF R$/t = CIF × PTAX venda média do mês. O spread NÃO é ágio: é referência de
  outro lugar e de outro momento de contrato; dizer isso em toda saída pública que o mostrar.
- Cores fixas dos grupos no dashboard (paleta validada para daltonismo nos dois temas):
  N azul, P laranja, K água, NPK amarelo, "Todos" violeta. Não reordenar.
- Não commitar dados grandes: se `data/raw/` passar de ~20 MB, colocá-lo no `.gitignore`
  e explicar no README como gerar.
- Roadmap: 1 ingestão → 2 transform → 3 dashboard → 4 Action mensal → 5 boletim com IA
  → 6 benchmark internacional e câmbio (feitas) → 7 logística portuária ANTAQ (fila, espera,
  terminais) → 8 demanda e cobertura (CONAB) → 9 projeção de curto prazo → 10 MCP com o
  Claude Desktop. Não adiantar fases sem pedido.

## Fatos da API (verificados em outubro de 2026)

- Base `https://api-comexstat.mdic.gov.br`, sem autenticação. Spec OpenAPI em
  `/docs/doc.yaml`.
- `POST /general` recebe `flow`, `monthDetail`, `period {from, to}`,
  `filters [{filter, values}]`, `details` e `metrics`. Usamos os detalhes
  `country, ncm, state, via, urf` e as métricas
  `metricFOB, metricKG, metricCIF, metricFreight, metricInsurance` (CIF, frete e seguro
  só existem na importação).
- A resposta traz NOMES de país, UF e via, não códigos. A URF vem como
  `"0917800 - PORTO DE PARANAGUA"`. Os códigos saem de `/tables/countries`, `/tables/uf`
  e `/tables/ways`. Da UF guardamos a sigla, porque os ids da API não são os do IBGE.
- `/tables/ncm?search=3102&add=sh` busca por trecho do código (casa até 61031020), então é
  preciso filtrar por `headingCode`. A tabela inclui NCMs antigos e cobre a série desde 1997.
- Num período de vários anos, a API soma em cada ano só os meses do intervalo
  (2015-01 a 2026-09 traz jan-set de cada ano). Por isso a ingestão consulta um ano por vez.
- Os códigos de URF mudam com reorganizações da Receita (ex.: `PORTO DE SAO LUIS` some e
  `IRF SAO LUIS` aparece). Em séries longas, agrupar por Região Fiscal (2 primeiros dígitos).
- Rate limit agressivo: HTTP 429 com "tente novamente em 10 segundos" e sem `Retry-After`.
  O cliente faz pausa de 10 s entre chamadas e backoff exponencial. Uma consulta por ano
  (~5,3 mil linhas, ~2 s).
- SSL: a API está atrás da Cloudflare, com certificado Google Trust Services, que o
  certifi valida. NÃO é ICP-Brasil. A verificação fica sempre ligada.
  `COMEXSTAT_CA_BUNDLE` soma CAs extras. `--ssl-inseguro` (opt-in) desliga a verificação
  só depois de uma falha e só para esse host.
- Grupo dos NCMs: 3102 → N, 3103 → P, 3104 → K, 3105 → NPK, exceto DAP (310530) e
  MAP (310540), que ficam em P.

## Fatos do Banco Mundial e do BCB (verificados em outubro de 2026)

- Pink Sheet: o xlsx mensal (`CMO-Historical-Data-Monthly.xlsx`) fica em
  `thedocs.worldbank.org`, num caminho com hash que muda a cada edição; o link sai da página
  `https://www.worldbank.org/en/research/commodity-markets`. Edição no 2º dia útil do mês,
  com o mês anterior. Licença CC BY 4.0. Sem chave.
- Aba "Monthly Prices": nomes na linha 4 (com espaços e "**" no fim, ex.: "Urea ",
  "Potassium chloride **"), unidades "($/mt)" na linha 5, períodos "AAAAMmm", faltantes ".."
  ou "…".
- Bases: KCl "CFR Brazil, granular" desde 2020-01 (antes FOB Vancouver; trocada
  retroativamente: a edição de out/2021 trazia FOB Vancouver em 2020); ureia FOB Oriente
  Médio desde 2022-03 (antes FOB Mar Negro); DAP FOB Golfo dos EUA; TSP "import US Gulf"
  (base de 2015 a 2017 não confirmada); rocha fosfática FOB Norte da África. O Banco Mundial
  revisa meses passados: a ingestão regrava o arquivo inteiro e lista as revisões.
- PTAX: SGS 3698 (venda, média mensal = média simples dos dias úteis), em
  `https://api.bcb.gov.br/dados/serie/bcdata.sgs.3698/dados?formato=json&dataInicial=dd/mm/aaaa&dataFinal=dd/mm/aaaa`;
  `/dados/ultimos/1` dá o último mês publicado. Sem chave. Licença ODbL (Portal de Dados
  Abertos do BCB, séries do dólar).
- Sem o cabeçalho `Accept: application/json`, o BCB devolve uma página HTML "Requisição
  inválida!" com HTTP 200. Com ele, aceita janelas de 5 anos e data final no futuro.
- TLS: BCB com DigiCert/Thawte; Banco Mundial com Sectigo/GTS. O certifi valida os dois.
