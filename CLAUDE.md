# CLAUDE.md: radar-fertilizantes-br

Radar das importações brasileiras de fertilizantes (capítulo 31, posições 3102 a 3105),
feito só com dados públicos do Comex Stat/MDIC. **O repositório é PÚBLICO.**

## Regras inegociáveis

### Segurança (repositório público)

- NUNCA escrever chaves, tokens, senhas, URLs privadas ou credenciais em código, config,
  README, testes ou commits.
- Qualquer segredo (ex.: `ANTHROPIC_API_KEY`, da fase 5) vem SOMENTE de variável de
  ambiente. Os nomes ficam em `.env.example`, com valores vazios.
- `.env`, `.env.*` (exceto `.env.example`), `*.key`, `*.pem` e `secrets/` ficam no
  `.gitignore`. Não remover essas linhas.
- Antes de CADA commit, rodar `git diff --cached` e conferir que não há segredos.
  O pre-commit com gitleaks (`.pre-commit-config.yaml`) bloqueia vazamentos. Se o hook
  não estiver instalado na sessão, rodar o mesmo comando dele:
  `gitleaks git --pre-commit --redact --staged --verbose`.
- Usar apenas Comex Stat e dados abertos do governo. Nunca dados de empresas ou clientes.
- Toda saída pública (README, dashboard, boletim) cita "Fonte: Comex Stat/MDIC".

### Autoria dos commits

- Commits saem SOMENTE com a identidade git do mantenedor:
  `Marcus Santos <77951123+AliceCullen-html@users.noreply.github.com>`.
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
- Python 3.10+. Dependências em `requirements.txt` (requests, pandas, pyarrow); as de
  desenvolvimento em `requirements-dev.txt`.
- Testes: `python -m pytest`. Eles não acessam a rede (a API é simulada).
- CI (`.github/workflows/ci.yml`): pytest em Python 3.10 (pandas 2) e 3.13 (pandas 3) e
  gitleaks em todo o histórico, em push e PR. Só leitura e sem nenhum segredo.
- Actions sempre em versões que rodam em Node 24 (`checkout@v6`, `setup-python@v6`,
  `setup-go@v6`, `configure-pages@v6`, `upload-pages-artifact@v5`, `deploy-pages@v5`).
  Antes de trocar uma versão, conferir `runs.using` no `action.yml` da tag.
- Ingestão: `python -m src.ingest --inicio AAAA-MM [--fim AAAA-MM]`. Opções em
  `python -m src.ingest --help`.
- Dados brutos: `data/raw/ano=AAAA/importacoes.parquet`, de 2015-01 a 2026-09 (61.460
  linhas, 1,6 MB). A coluna `ano` fica no caminho, não dentro do arquivo; leia com
  `src.ingest.ler_raw()`.
- Fase 2: `python -m src.transform` gera em `data/processed/` os CSVs de preço US$/t,
  shares por origem e URF com variação anual, HHI, sazonalidade e o `dashboard.json`.
  As saídas são determinísticas; regenere-as sempre que `data/raw/` mudar.
- Fase 3: `dashboard/index.html` é um HTML único (Chart.js 4.5.1 do jsDelivr, com SRI) que
  lê `dashboard.json`. Para ver localmente: `python -m http.server` na raiz e abrir
  `/dashboard/`. O deploy é feito pelo `.github/workflows/pages.yml` (push na main que mude
  `dashboard/` ou o `dashboard.json`, ou manual), sem segredos. Pré-requisito: Settings →
  Pages → Source: "GitHub Actions".
- Cores fixas dos grupos no dashboard (paleta validada para daltonismo nos dois temas):
  N azul, P laranja, K água, NPK amarelo, "Todos" violeta. Não reordenar.
- Não commitar dados grandes: se `data/raw/` passar de ~20 MB, colocá-lo no `.gitignore`
  e explicar no README como gerar.
- Roadmap: 1 ingestão (feita) → 2 transform (feita) → 3 dashboard no
  GitHub Pages (feito) → 4 GitHub Action mensal → 5 boletim com IA opcional. Não adiantar fases
  sem pedido.

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
