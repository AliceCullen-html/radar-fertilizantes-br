# Graaos

**Graaos — hub de inteligência de mercado do agro e da logística, com dados públicos.**

[![CI](https://github.com/mvsantos-dev/graaos/actions/workflows/ci.yml/badge.svg)](https://github.com/mvsantos-dev/graaos/actions/workflows/ci.yml)

**Fertilizantes, o primeiro módulo, está no ar: [radar.graaos.cloud](https://radar.graaos.cloud/)**

[![Graaos · Fertilizantes: KPIs de setembro de 2026 e toneladas importadas por mês, por grupo de fertilizante](dashboard/print.png)](https://radar.graaos.cloud/)

## Módulos

| Módulo | Situação |
| --- | --- |
| **Fertilizantes**: importações brasileiras de adubos (N, P, K e NPK), com volumes, origens, portos, rotas e frete, preços, mercado internacional e relação de troca com a soja | no ar em [radar.graaos.cloud](https://radar.graaos.cloud/) |
| **Logística portuária**: fila, espera e terminais nos portos | planejado |
| **Grãos** | planejado |

O hub cobre granéis sólidos, grãos e logística. Granéis líquidos (óleos vegetais, químicos e
combustíveis) ficam de fora.

O módulo **Fertilizantes** acompanha as importações brasileiras de fertilizantes só com dados
públicos do [Comex Stat](https://comexstat.mdic.gov.br/), do Ministério do Desenvolvimento,
Indústria, Comércio e Serviços (MDIC), comparados com os preços internacionais da
[Pink Sheet do Banco Mundial](#brasil-x-mercado-internacional-fase-6) e convertidos para reais
pela PTAX do Banco Central. Os dados e o dashboard se atualizam sozinhos todo mês
([fase 4](#atualização-automática-fase-4)), com um [boletim escrito por IA](#boletim-mensal-com-ia-fase-5)
em que todos os números são inseridos e conferidos pelo programa.

**Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX.**

## O que é

O Brasil importa a maior parte dos fertilizantes que usa. O módulo Fertilizantes baixa, mês a mês,
as importações de adubos minerais e químicos e organiza os dados para responder perguntas
como: de onde vem o potássio? Por quais portos entra a ureia? Quanto se paga, em média,
pela tonelada de MAP? Quanto custa o frete de cada rota, do país de origem ao porto?
Quantas toneladas de soja exportada pagam uma tonelada de adubo importado? E o preço que o
Brasil paga fica acima ou abaixo da referência internacional?

Cada linha traz país de origem, NCM, UF, via de transporte e URF (unidade da Receita
Federal onde a carga foi desembaraçada), com valor US$ FOB, peso líquido em kg, valor
US$ CIF, frete e seguro. O repositório já inclui a série de 2015-01 a 2026-09.

Para a [relação de troca](#relação-de-troca-no-porto), o projeto baixa também a
exportação mensal de soja em grão (NCM 12019000), com valor US$ FOB e peso. Para a
[comparação com o mercado internacional](#brasil-x-mercado-internacional-fase-6), baixa os
preços mensais de fertilizantes da Pink Sheet do Banco Mundial e a PTAX média mensal do BCB.
Nenhuma das fontes pede chave.

### Produtos acompanhados

| Posição (SH4) | Conteúdo | Grupo |
| --- | --- | --- |
| 3102 | nitrogenados: ureia, sulfato de amônio, nitrato de amônio... | N |
| 3103 | fosfatados: superfosfatos... | P |
| 3104 | potássicos: cloreto de potássio, sulfato de potássio... | K |
| 3105 | compostos: NPK, NP, PK, NK | NPK |
| 3105 (310530 e 310540) | DAP e MAP, fosfatados pela convenção de mercado | P |

A lista completa, com 42 NCMs (inclusive códigos de versões antigas da NCM), está em
[`data/ncm_fertilizantes.csv`](data/ncm_fertilizantes.csv). Ela foi gerada a partir da
tabela auxiliar de NCM da própria API, com `python -m src.ingest --atualizar-ncm`.

## Como rodar

Requer Python 3.10 ou mais recente.

```bash
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.ingest --inicio 2015-01   # baixa só o que falta: Comex Stat, Pink Sheet e PTAX
python -m src.transform                 # gera as tabelas de data/processed/
```

A ingestão é incremental: só consulta os meses que ainda não estão no disco, uma consulta
por ano e por série (importação de fertilizantes e exportação de soja), com o mesmo cliente
e o mesmo limite de requisições. Se a API disser que publicou um mês e ele não vier em
alguma das séries, o comando termina com erro (código 1). Em seguida, baixa a Pink Sheet
(o arquivo inteiro, a cada vez) e os meses da PTAX que faltam; os detalhes estão em
[Brasil x mercado internacional](#brasil-x-mercado-internacional-fase-6). Ao final, imprime
um resumo de cada série. Saída da carga de 2015-01 a 2026-09:

```text
Resumo 2015-01 a 2026-09 (Fonte: Comex Stat/MDIC)
  Linhas: 61.460 | meses: 141 | NCMs: 38
  Total: 405.882.785 t | US$ FOB 140.294.506.174 | US$ CIF 152.229.591.323
  Top 5 países de origem por kg líquido:
    1. Rússia                           90.368.607 t   22,3%
    2. China                            59.228.818 t   14,6%
    3. Canadá                           46.808.647 t   11,5%
    4. Marrocos                         24.761.403 t    6,1%
    5. Estados Unidos                   21.303.111 t    5,2%
  Top 5 URFs por kg líquido:
    1. 0917800 PORTO DE PARANAGUA              113.356.690 t   27,9%
    2. 0817800 PORTO DE SANTOS                  78.152.511 t   19,3%
    3. 1017700 PORTO DE RIO GRANDE              60.390.026 t   14,9%
    4. 0927700 PORTO DE SAO FRANCISCO DO SUL    27.426.288 t    6,8%
    5. 0727600 PORTO DE VITORIA                 23.347.338 t    5,8%
Resumo soja em grão (NCM 12019000, exportação) 2015-01 a 2026-09 (Fonte: Comex Stat/MDIC)
  Meses: 141 | Total: 988.154.583 t | US$ FOB 421.488.513.355 | US$ 427/t
Resumo Pink Sheet (Banco Mundial, CC BY 4.0) 2015-01 a 2026-09
  Meses: 141 | 2026-09 em US$/t: ureia 407,5 | dap 800,6 | tsp 690,0 | kcl 367,5 | rocha_fosfatica 170,0
Resumo PTAX venda, média mensal (BCB, SGS 3698) 2015-01 a 2026-09
  Meses: 141 | 2026-09: R$ 5,1447 por US$
```

| Opção da ingestão | O que faz |
| --- | --- |
| `--inicio AAAA-MM` | primeiro mês (a série mensal começa em 1997-01) |
| `--fim AAAA-MM` | último mês; o padrão é o último mês publicado em cada fonte |
| `--serie todas\|fertilizantes\|soja\|benchmark\|cambio` | série a baixar (padrão: `todas`); `benchmark` é a Pink Sheet e `cambio`, a PTAX |
| `--ultimo-gravado` | só imprime o último mês completo, gravado em todas as séries (o menor entre elas), sem chamar nenhuma fonte; com `--por-serie`, uma linha por série |
| `--forcar` | baixa de novo meses já gravados (o MDIC e o BCB podem revisar meses recentes; a Pink Sheet é sempre baixada inteira) |
| `--atualizar-ncm` | regenera `data/ncm_fertilizantes.csv` a partir da API |
| `--pausa SEG` | pausa mínima entre chamadas à API do Comex Stat (padrão: 10 s) |
| `--ssl-inseguro` | último recurso para problemas de certificado; veja [SSL](#ssl) |

## Dashboard (fase 3)

O [dashboard](https://radar.graaos.cloud/) é uma página estática (`dashboard/index.html`) que lê o
`data/processed/dashboard.json`. Mostra:

- o [boletim do mês](#boletim-mensal-com-ia-fase-5), num card no topo, com link para o histórico;
- KPIs do último mês e do acumulado do ano, comparados com o mesmo período do ano anterior;
- toneladas mensais por grupo (N, P, K e NPK);
- share por país de origem e ranking de portos (URF), com variação sobre o ano anterior;
- [mapa de fluxos](#mapa-de-fluxos) animado: de cada país de origem até o Brasil e até o porto de
  chegada, com ranking e linha do tempo de 2015 a 2026;
- [rotas país de origem → porto](#rotas-e-frete-por-rota): diagrama de fluxo (Sankey) por
  grupo e ano e tabela de frete US$/t das rotas com volume relevante;
- preço implícito US$/t FOB x CIF e frete US$/t, com a legenda do que é FOB e CIF ao lado;
- [custo por nutriente](#custo-por-nutriente-custo-por-molécula): quanto custa, no porto, a
  tonelada de N, de P₂O₅ e de K₂O em cada fertilizante, em US$ e em R$;
- [relação de troca no porto](#relação-de-troca-no-porto) entre soja e KCl, ureia e MAP, com a
  média histórica como referência;
- [Brasil x mercado internacional](#brasil-x-mercado-internacional-fase-6): preço FOB e CIF do
  Brasil contra a Pink Sheet do Banco Mundial, spread na base comparável e CIF em reais, para
  KCl, ureia, MAP, DAP e TSP;
- concentração de fornecedores (HHI) por ano;
- um glossário (FOB, CIF, CFR, custo por nutriente, crédito de N, URF, UF de destino, PTAX,
  spread e outros termos), no fim da página.

A navegação é animada com o anime.js: os links do menu rolam até a seção com início e fim
suaves (qualquer gesto interrompe), o indicador do menu acompanha a rolagem e desliza de um item
para o outro, uma linha fina no pé da barra mostra quanto da página já foi lida e cada seção
entra ao aparecer na tela (o rótulo se "decodifica", o título sobe palavra a palavra e os
cartões deslizam, com os números e as legendas chegando em cascata). Cada gráfico se desenha
quando aparece na tela pela primeira vez: as linhas da esquerda para a direita, ponto a ponto, e
as barras crescendo em sequência. Depois disso, trocar o grupo, o período ou o tema atualiza os
gráficos no lugar: as linhas e as barras deslizam até os novos valores, e os números dos cartões
contam do valor antigo até o novo (na primeira vez que o cartão aparece, a partir de zero).
Outras animações acompanham a própria rolagem (o `onScroll` do anime.js, com suavização) e
voltam quando se rola para cima: o mapa do topo cresce e esmaece ao sair de cena, um fundo de
pontos se move devagar atrás das seções, os títulos têm um leve parallax e o palco do mapa de
fluxos se expande de uma janela recortada até a largura toda. No topo, arcos vetoriais ligam as maiores
origens do último ano completo ao Brasil: cada um se desenha da origem até aqui e, depois,
cometas de luz correm por eles. Os pontos do mapa acendem sob o cursor e o continente apontado
se destaca; um clique (ou o botão "Explorar por continente") abre os dados dele no lugar do
título: toneladas importadas, parte do total do Brasil, variação sobre o ano anterior, divisão
por grupo de nutriente e maiores origens, com as rotas do continente no mapa. Os continentes são
os do Natural Earth (a Rússia conta na Europa). Um clique no Brasil (ou no chip dele) abre a
[visão por estado](#importações-por-estado): um quadrado por UF, na cor da variação sobre o ano
anterior, com os estados que mais cresceram e mais caíram em toneladas. No diagrama de rotas, as fitas "escorrem" das
origens para os portos ao aparecer na tela e a cada troca de grupo ou ano. Com "reduzir
movimento" ligado no sistema, nada disso se mexe e nada fica escondido.

Os filtros de grupo e período valem para todos os gráficos e ficam na URL (por exemplo,
`#grupo=K&de=2021-10&ate=2026-09`), então dá para compartilhar uma visão específica. A página
funciona no celular, tem tema claro e escuro e uma tabela com os dados de cada gráfico.
O Chart.js 4.5.1 vem do jsDelivr com versão fixa e verificação de integridade (SRI).

Para ver localmente, rode `python -m http.server` na raiz do repositório e abra
<http://localhost:8000/dashboard/>.

O workflow [`pages.yml`](.github/workflows/pages.yml) publica o dashboard no GitHub Pages, em
<https://mvsantos-dev.github.io/Graaos/>, o espelho do site principal, a
cada push na `main` que altere `dashboard/`, o `dashboard.json` ou o `boletins/latest.json`
(publicado como `boletim.json`), ou manualmente, em
Actions → Pages → Run workflow. Ele não usa nenhum segredo; o único pré-requisito é, em
**Settings → Pages**, escolher **Source: GitHub Actions**.

O site é montado pelo [`build_site.sh`](build_site.sh), o mesmo no GitHub Pages e na Cloudflare.
Ele copia `dashboard/` e põe os dados na raiz do site, onde o `index.html` os procura:
`dashboard.json` (de `data/processed/`, com todos os gráficos e o mapa de fluxos) e
`boletim.json` (de `boletins/latest.json`; `{}` enquanto não houver boletim). No fim, o build
**falha** (exit 1) se faltar o `index.html`, o `dashboard.json` ou algum arquivo de `assets/`, ou se
algum JSON for inválido ou não tiver os blocos que a página lê: melhor um deploy que falha do que
um site no ar sem dados. Para ver o site montado localmente:

```bash
bash build_site.sh            # monta em site/
python -m http.server -d site # e abra http://localhost:8000/
```

Na Cloudflare (Workers com assets estáticos, sem nenhum segredo no repositório), o
[`wrangler.jsonc`](wrangler.jsonc) publica a pasta `./site` (`assets.directory`). Nas configurações
de build do Worker: comando de build `bash build_site.sh` e comando de deploy `npx wrangler deploy`.
O nome do Worker precisa ser o mesmo do campo `name` do `wrangler.jsonc`.

### Mapa de fluxos

![Mapa de fluxos: a entrada animada, o destaque de uma origem no ranking, o clique na Rússia e a linha do tempo do potássio de 2021 a 2023](dashboard/mapa-fluxos.gif)

A seção "Mapa de fluxos" é um mapa-múndi de pontos (o mesmo da abertura da página) que mostra de
onde vem o adubo e em que porto ele chega, por grupo e por ano:

- **Resumo.** Acima do mapa, o total importado no ano e no grupo e quanto as três maiores
  origens somam, com a legenda do mapa (área do círculo e espessura do arco em toneladas).
- **Visão geral.** Um arco por país de origem até o Brasil (as maiores origens do ano, as mesmas
  do ranking ao lado: de 5 a 8 no desktop, 6 no celular), na cor do grupo e com largura
  proporcional às toneladas. Os pontos do país ganham a mesma cor, com brilho pela tonelagem, e
  um cometa percorre cada arco no sentido da carga. O painel traz o ranking dos países
  (toneladas, participação e variação sobre os mesmos meses do ano anterior; "novo" quando o
  país vendeu menos de 1.000 t naquele ano) ou, na aba Portos, dos portos, com o mapa aproximado
  na costa brasileira. Passar o mouse (ou o foco) numa linha ou num país destaca o arco dele e
  apaga os outros.
- **Comparar portos.** O botão "Comparar portos" alarga o painel e põe os maiores portos lado a
  lado: toneladas e participação, com a barra dividida entre N, P, K e NPK (em "Todos"), variação
  sobre os mesmos meses do ano anterior, frete médio em US$/t (frete ÷ toneladas de todas as
  rotas que chegam ao porto) e as três maiores origens. Os títulos das colunas ordenam a lista
  (toneladas, variação ou frete); o clique num porto abre as origens dele. Com o Reproduzir, os
  portos trocam de posição ano a ano.
- **Origem.** O clique num país (ou Enter, no mapa ou no ranking) aproxima a câmera, acende o
  país numa onda a partir do centro e abre os arcos até cada porto de chegada, com a tonelagem
  da rota no rótulo de cada porto. O painel mostra toneladas, participação no grupo, frete e CIF
  em US$/t, variação e a lista de portos.
- **Porto.** O clique num porto (no mapa, na aba Portos ou na lista de uma origem) inverte a
  visão: os arcos vêm de cada origem até ele.
- **Linha do tempo.** Uma coluna por ano com o total do grupo (o ano parcial fica hachurado) e o
  botão Reproduzir, que vai de 2015 ao último ano completo. Ao trocar de ano, os arcos engrossam
  ou afinam e o ranking se reordena com animação. Em K, dá para ver Belarus cair de 2,4 milhões
  de t em 2021 para 1,1 milhão em 2022 (−55%), enquanto o Canadá cresce e, a partir de 2023, a
  Rússia também.
- **Filtro.** O grupo (N, P, K, NPK) é o mesmo filtro do resto da página. O ano, a visão e a
  seleção também vão para a URL (por exemplo, `#grupo=K&de=2021-10&ate=2026-09&ano=2022&origem=676`
  ou `#ano=2025&visao=comparar`). O
  botão "Visão geral" (sobre o mapa) ou a tecla ESC voltam ao mapa inteiro.

Os números vêm só das tabelas de `data/processed/` (share por origem, ranking de portos, rotas e
preço por origem), reunidos pelo transform no bloco `mapa` do `dashboard.json`. Na visão geral,
cada arco soma tudo o que o país vendeu ao Brasil no ano. Ao abrir um país ou porto, só as rotas
com pelo menos 50 mil t no ano ganham arco, o mesmo corte da [tabela de
rotas](#rotas-e-frete-por-rota) (proporcional em ano parcial; o painel mostra o corte usado no
ano); as menores ficam como "outras". Origens com menos de 1.000 t no ano e grupo entram em
"Outros". A tabela abaixo do mapa traz os mesmos números.

O mapa fica sobre um fundo noturno nos dois temas, para que as cores dos grupos (versões claras
de N azul, P laranja, K água, NPK amarelo e Todos violeta, com contraste de 7:1 ou mais)
apareçam nos arcos. Com "reduzir movimento" ligado no sistema, não há câmera, entrada animada,
cometas nem pulsos, só transições curtas de opacidade; o botão "Pausar animação" para cometas e
pulsos a qualquer momento. O mapa é uma parada só do Tab (as setas andam entre países e portos),
as linhas do ranking são botões, as mudanças são anunciadas para leitores de tela (`aria-live`,
sem repetir a cada ano durante o play) e a animação para quando a seção sai da tela. A animação
usa o [anime.js](https://animejs.com/) 4.5.0 (licença MIT), do jsDelivr, com versão fixa e SRI,
e o canvas e o SVG são desenhados pela própria página.

#### Base do mapa e tabelas de referência

Tudo é gerado em desenvolvimento por `src/mapa.py` e fica versionado; o deploy não roda nada
e o navegador não faz cálculo de geografia.

| Arquivo | Conteúdo | Fonte |
| --- | --- | --- |
| `dashboard/assets/mapa-pontos.json` | grade de pontos a cada 1,5 grau, em projeção equirretangular e sem a Antártida, cada ponto com o código ISO3 e o continente do país (cerca de 6,9 mil pontos, 15 KB) | [Natural Earth](https://www.naturalearthdata.com/) 1:110m, Admin 0 – Countries (v5.1.2) |
| `data/ref/paises_iso.csv` | código e nome do país no Comex Stat → ISO 3166-1 alfa-3, ponto central (longitude, latitude) e continente | ISO3: Comex Stat/MDIC, `/tables/countries/{código}`; ponto e continente: Natural Earth 1:50m (ponto de rótulo e campo CONTINENT do país) |
| `data/ref/urf_coordenadas.csv` | URF → porto no mapa, UF, longitude, latitude e observação | Natural Earth 1:10m, Ports e Populated Places |

A base de pontos é a mesma da abertura da página: em vez de criar um `dashboard/mapa_pontos.json`
separado, a abertura e o mapa de fluxos usam `dashboard/assets/mapa-pontos.json`. Quando o porto
não está no Natural Earth, vale o lugar mais próximo que está, com a distância anotada na
coluna `observacao` (por exemplo, Aratu → Salvador, a 20 km). URFs no mesmo lugar aparecem como
um porto só. Para refazer:

```bash
python -m src.mapa pontos            # dashboard/assets/mapa-pontos.json
python -m src.mapa urfs              # data/ref/urf_coordenadas.csv
python -m src.mapa paises --manter   # data/ref/paises_iso.csv (só consulta no Comex Stat os países novos)
python -m src.transform              # refaz o bloco "mapa" do dashboard.json
```

Os testes conferem que todo país e toda URF com volume relevante (pelo menos 10 mil t num ano e
grupo, ou alguma rota acima do corte) têm coordenada. Se uma URF nova passar desse volume, o
teste falha, inclusive na [atualização mensal](#atualização-automática-fase-4), até que ela
entre na tabela `URF_LOCAL` de `src/mapa.py` e o `python -m src.mapa urfs` seja rodado de novo.

## Atualização automática (fase 4)

O workflow [`atualizacao.yml`](.github/workflows/atualizacao.yml) mantém os dados em dia
sem intervenção. São quatro séries, de três fontes que publicam o mês anterior nos primeiros
dias úteis: importação de fertilizantes e exportação de soja (Comex Stat), preços
internacionais (Pink Sheet) e câmbio (PTAX). O workflow roda uma vez por dia, do dia 1 ao
dia 15 de cada mês (11h17 UTC):

1. Confere se o mês anterior já está gravado nas quatro séries
   (`python -m src.ingest --ultimo-gravado`). Se estiver, encerra sem chamar nenhuma fonte:
   na prática, as checagens param assim que o mês novo entra.
2. Se faltar, roda a ingestão incremental das quatro séries, o `python -m src.transform` e
   os testes. A Pink Sheet e a PTAX vêm primeiro, e o Comex Stat só até o menor mês das
   duas: um mês do Comex sem benchmark ou sem câmbio não chega ao dashboard nem ao boletim.
3. Se o último mês completo avançou, ou se o boletim dele ainda não existe, gera o
   [boletim com IA](#boletim-mensal-com-ia-fase-5).
4. Faz um commit `chore(dados): atualização AAAA-MM` como `github-actions[bot]`, só com
   arquivos de `data/` e `boletins/`, e publica o dashboard.

**Mês completo.** Um mês só entra quando está gravado nas quatro séries: vale o menor
entre os últimos meses de cada uma (`--ultimo-gravado`; com `--por-serie`, uma linha por
série). Como as fontes publicam em dias diferentes, é normal o mês chegar aos poucos. Se
faltar alguma, nada é commitado e o run fica verde, com um aviso que diz qual série falta e
até quando ela está gravada; a execução do dia seguinte tenta de novo. Se o Comex Stat já
publicou o mês, mas foi segurado à espera da Pink Sheet ou da PTAX, o aviso o mostra à
parte, como retido, e não como atrasado. A partir do dia 15, se ainda faltar, o job `prazo`
fica vermelho dizendo qual: a fonte pode ter atrasado ou mudado de endereço ou de formato. Quando ela publicar, rode o workflow manualmente.

Se uma fonte falhar, ou disser que publicou um mês que não vem na consulta (o último ou
qualquer um no meio), o job termina em vermelho, com o erro no log, e nada é commitado: a
ingestão grava em disco no runner, mas o commit só acontece no fim, depois do transform e
dos testes. Cada passo tem limite de tempo, então um travamento também falha em minutos. A
próxima execução tenta de novo.

Os privilégios ficam separados por job:

| Job | Permissão | Acesso ao secret | O que roda |
| --- | --- | --- | --- |
| `atualizar` | leitura | não | ingestão, transform e testes (Python e dependências); entrega `data/` como artifact |
| `gerar-boletim` | leitura | **sim, o único** (environment `boletim-ia`) | só `python -m src.boletim`, que usa a biblioteca padrão: nenhum pacote é instalado; timeout de 10 min |
| `commitar` | escrita no conteúdo | não | só git, com hooks desligados; recusa mudanças fora de `data/` e `boletins/` |
| `prazo` | nenhuma | não | nada além de uma mensagem de erro, sem checkout: fica vermelho se uma fonte não publicou o mês até o dia 15 |
| `publicar` | Pages | não | o `pages.yml` |

O workflow não tem gatilho `pull_request_target`, então PRs, inclusive de forks, nunca
chegam ao secret. O `GITHUB_TOKEN` do próprio Actions só entra no passo do push. Como
pushes feitos com esse token não disparam outros workflows, o deploy do Pages é chamado
pelo próprio workflow. Se o boletim for reprovado na validação, os dados do mês são
commitados sem ele. Para rodar fora do calendário: Actions → Atualização mensal → Run
workflow (com a opção "boletim" marcada, ele também gera ou refaz o boletim do mês).

A ingestão é incremental e não baixa de novo meses já gravados. Se o MDIC ou o BCB revisar
um mês, rode localmente `python -m src.ingest --inicio AAAA-MM --fim AAAA-MM --forcar` e o
transform, e faça o commit. A Pink Sheet é baixada inteira a cada ingestão, e as revisões do
Banco Mundial entram junto com o mês seguinte (o log lista os valores revisados).

## Boletim mensal com IA (fase 5)

Todo mês, um texto curto de análise sobre o último mês de dados: título, lead e 4 a 6
parágrafos, em `boletins/AAAA-MM.md`, com o mais recente em `boletins/latest.json` (lido
pelo card do dashboard). O [histórico](boletins/) fica no repositório.

### Como a IA é usada (e por que ela não inventa números)

A IA escreve o texto; os números vêm do programa. O passo a passo (`src/boletim.py`):

1. **Pacote de fatos.** O programa lê só dados já calculados (`dashboard.json` e
   `troca_mensal.csv`) e monta um JSON com chaves nomeadas, valores já formatados em pt-BR
   e a descrição de cada um, por exemplo:

   ```json
   "troca_map_ultimo": {"valor": "1,82 t de soja", "descricao": "Toneladas de soja que pagam uma tonelada de MAP ..."},
   "share_russia_k_ano": {"valor": "30,7%", "descricao": "Participação de Rússia (segunda maior origem) ..."},
   "var_frete_medio_ytd": {"valor": "30,5%", "descricao": "Variação do frete médio ..."},
   "var_frete_medio_ytd_sentido": {"valor": "alta", "descricao": "Direção de var_frete_medio_ytd ..."}
   ```

   As variações vão sem sinal, com a direção numa chave à parte, para a IA não errar o
   sinal. Para ver o pacote do mês: `python -m src.boletim --fatos`.
2. **Marcadores no lugar de números.** A IA devolve JSON com título, lead e parágrafos e só
   pode citar números por marcadores, como `{troca_map_ultimo}`. É proibido escrever
   números.
3. **Validação automática, igual para todos os provedores.** A resposta é reprovada se tiver:
   - marcador que não existe no pacote, dois marcadores colados, letra ou algarismo colado a
     um marcador ("l{x}", "{x}O") ou dois números separados só por traço, barra ou
     dois-pontos;
   - fora dos marcadores, qualquer caractere que não seja letra do português, espaço ou
     pontuação comum (emoji, seta, símbolo, caractere invisível ou combinante, letra de outro
     alfabeto, "&", "@"), quebra de linha, Markdown, HTML, link, domínio ("site.xyz"),
     emoji por código (":rocket:") ou início de lista;
   - fora dos marcadores: dígito (de qualquer escrita, inclusive "²" e "½"), algarismo
     romano, letras no lugar de algarismos ("lOO"), número por extenso ("três", "mil", "zero",
     "meia", "dobrou", "quíntuplo", "vigésimo", "um terço"), nome de mês, período
     ("semestre", "década", "biênio", "mês anterior", "comparação mensal"), contagem ou
     recorde que o pacote não traz ("terceiro mês seguido", "consecutivo", "pela primeira
     vez", "recorde", "nunca antes", "o maior da série"), unidade (%, "por cento", US$, p.p.)
     ou unidade inventada para um número puro ("USD {hhi_n_ano}", "Mt", "kt", "euros",
     "navios"); uma contagem vem sempre com o que ela conta (`{n_origens_k_ano} países`,
     `{n_rotas_ano} rotas`);
   - **palavra de direção ou comparação** ("subiu", "caiu", "cresceu", "superou", "aquém",
     "alta", "queda", "acima", "abaixo", "maior que", "mais barato", "melhorou"...). A
     direção entra **só** pelos marcadores `_sentido` (alta, queda ou estabilidade) e
     `_posicao` (acima ou abaixo de uma referência), que o programa preenche a partir dos
     dados e que precisam vir colados à própria variação: `{var_x_sentido} de {var_x}` ou
     `{desvio_x} {desvio_x_posicao}`. Toda variação vai com a sua direção, e um sinal (+, −,
     "menos") antes dela, ou antes de qualquer número, reprova. Uma frase com `_sentido` ou
     `_posicao` não pode negar nem inverter a direção ("não houve", "reverteu", "longe de"),
     e o `_sentido` não leva adjetivo de intensidade ("relevante", "leve");
   - palavra de juízo ou de comparação que a lista de direção não pega ("estável", "elevado",
     "forte", "significativo", "decresceu", "custou mais", "do que", "poder de compra",
     "respectivamente"), continuidade ("manteve", "segue", "novamente", "retomada"), recorde
     ou pico ("pico", "em anos", "desde", "já observado"), "única", "maioria", causa,
     previsão ou contexto que o pacote não traz ("reflexo", "devido a", "por conta de",
     "influenciou", "tendência", "deve cair", "estoques", "demanda", "plantio"), região ("Oriente Médio", "Europa"),
     produto fora do pacote ("sulfato", "nitrato", "SSP") ou nome próprio que não está no
     pacote (maiúscula fora do início de frase, como "Cade");
   - nome de país, porto, grupo (nitrogenados, fosfatados, potássicos, compostos) ou produto
     (ureia, KCl, MAP, DAP, TSP) numa frase cujos números não são dele; país ou porto que não está no
     pacote (também sem acento, em maiúsculas ou por gentílico: "Paranagua", "CHINA",
     "russos"); valor do período atual posto no período anterior;
   - **número ligado ao que ele não mede.** Para cada número, o grupo, o produto ou o total
     ("fertilizantes", "todos os grupos") mencionado mais perto precisa ser o dele, e o país
     ou porto mais perto também; "principal", "líder", "segunda maior" e "terceira maior"
     precisam bater com a posição no pacote; o sujeito de uma variação precisa ser a métrica
     dela (volume, preço, participação ou frete); cada número precisa estar no seu período
     (mês, acumulado, ano anterior inteiro, série) e cada variação, na sua base
     (`{mes_ano_anterior}` ou `{periodo_ytd_anterior}`). Por isso a construção "Santos e
     Paranaguá receberam {a} e {b}" reprova: cada número vem logo depois do seu nome;
   - "alta concentração" fora de `{hhi_x_ano_posicao} de alta concentração` ou da definição
     do limiar; "série", "máximo" ou "o maior valor" sem as chaves de máximo e média da série;
     o maior e o menor frete por rota sem dizer "entre as maiores rotas";
   - relação de troca (ou o preço FOB da soja junto do CIF de um fertilizante) sem "no porto"
     no mesmo parágrafo, ou qualquer menção a produtor, agricultor, fazenda, "quem planta",
     safra, agro ou revenda (exceto na forma "não é o preço ao produtor");
   - "ágio", "deságio", "sobrepreço", "subpreço", "prêmio", "desconto", "markup" ou
     "margem", mesmo para negar: o spread contra o benchmark do Banco Mundial não é ágio (veja
     [as limitações](#limitações)); spread junto do preço do Brasil da outra base (o FOB do
     KCl, que é comparado pelo CIF, por exemplo, em número ou por extenso, como "desembarcado"
     ou "com frete e seguro"); spread do ano anterior sem o mês dele (`{mes_ano_anterior}`);
     benchmark citado sem dizer "benchmark" ou "Banco Mundial" junto dele, ou ligado ao que
     o Brasil pagou; benchmark do MAP sem dizer que ele é o do DAP; "em linha", "alinhado", "próximo do" ou "o mesmo que" fora do marcador
     de posição; relação de troca em reais ou junto da PTAX (ela é US$ por US$); câmbio,
     dólar ou PTAX sem `{ptax_mes}` ou `{var_ptax_ano}` na frase; a variação da PTAX sem
     PTAX, dólar ou câmbio como sujeito, ou com o real (ou "a moeda brasileira") como sujeito;
     a variação do CIF em reais atribuída ao câmbio; unidade repetida depois de um valor que já a traz ("R$ 2.030/t
     reais");
   - um termo de uma lista fixa de recomendação e tom alarmista ("recomendamos", "vale a
     pena", "antecipar as compras", "convém", "disparou", "colapso", "crítico");
   - menos de 6 marcadores citados, ou tamanho acima do limite.

   Se reprovar, a IA recebe os erros e tenta mais uma vez; se reprovar de novo, nada é
   publicado. Um modelo fraco que inventa número reprova do mesmo jeito em qualquer provedor.
4. **O programa troca os marcadores pelos valores** e grava o boletim, com uma tabela dos
   números citados e o rodapé fixo: "Texto gerado por IA a partir de dados do Comex
   Stat/MDIC; números inseridos e verificados automaticamente." Quando o texto cita números
   da Pink Sheet ou da PTAX, o rodapé e o campo de fontes incluem o Banco Mundial e o BCB.

O que a validação confere é o que está na lista acima; as listas de palavras são fixas, então
um sinônimo que não esteja nelas passa. O resto é pedido no prompt e não tem como ser
verificado por um programa: o tom de analista (neutro, sem recomendação de compra e venda e
sem adjetivos alarmistas, além da lista fixa de termos) e a regra de não citar causas,
eventos, empresas ou previsões que não estejam no pacote, além das listas fixas. Quando o
boletim cita a relação de troca, o programa acrescenta uma nota fixa (no `.md`, no
`latest.json` e no card): ela é medida no porto e não é o preço pago pelo produtor. Do mesmo jeito,
quando cita o benchmark, entra a nota de que ele é uma referência de outro lugar e de outro
momento de contrato e de que o spread não é ágio (e, para o MAP, de que a referência é o
DAP); quando cita o CIF em reais, a nota de que ele usa a PTAX média do mês. Fica
registrado no boletim qual provedor e qual modelo escreveram o texto: o modelo configurado,
ou a versão dele que a API devolver ("gpt-4o" → "gpt-4o-2024-08-06"); qualquer outro nome
devolvido pela API é ignorado.

Desde a [fase 6](#brasil-x-mercado-internacional-fase-6), o pacote de fatos traz também,
para KCl, ureia, MAP, DAP e TSP: o benchmark do mês e a sua base, os preços FOB e CIF do
Brasil, o spread comparável em % e em US$/t (sem sinal, com a posição "acima do", "abaixo
do" ou "em linha com o" benchmark num marcador `_posicao` à parte), o spread do mesmo mês do ano anterior, também
sem sinal e com a sua posição (só quando a base do benchmark é a mesma nos dois meses: uma
"variação do spread" com sinal seria lida como distância maior quando ela diminuiu), a PTAX
média e o CIF em reais, com a variação anual. Meses de amostra pequena ficam fora do pacote.
Os números são arredondados meio para cima, como no dashboard: 396,5 vira 397 nos dois.

O provedor padrão é a Anthropic, com o modelo `claude-sonnet-5-5` e saída JSON restrita por
schema. Pela tabela de preços do modelo (US$ 2 por milhão de tokens de entrada e US$ 10 de
saída), um boletim custa poucos centavos de dólar, mesmo com a nova tentativa.

| Variável | Para quê |
| --- | --- |
| `RADAR_LLM_PROVIDER` | `anthropic` (padrão), `openai` (qualquer API compatível com OpenAI) ou `ollama` (local) |
| `RADAR_LLM_MODEL` | modelo; padrão `claude-sonnet-5-5` na Anthropic, obrigatório nos outros |
| `ANTHROPIC_API_KEY` | chave da Anthropic |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | chave e endereço da API compatível (padrão: `https://api.openai.com/v1`; num servidor local a chave pode ficar vazia) |
| `OLLAMA_HOST` | endereço do Ollama (padrão: `http://localhost:11434`), sem chave |

Cada provedor lê só as próprias variáveis. Nenhuma é necessária para os testes nem para o
modo `--mock`, que usa uma resposta fixa de exemplo sem chamar nenhuma API e grava em
`boletins/mock/`, fora do Git. O cliente HTTP não segue redirecionamentos (num POST eles
levariam a chave para outro endereço), recusa chave com espaço ou quebra de linha e, com
chave, só aceita `http://` em `OPENAI_BASE_URL` para servidor local.

`--mes AAAA-MM` gera o boletim de um mês anterior. Origens, HHI, portos e rotas só entram
quando o mês é o último do seu ano nos dados (esses blocos do `dashboard.json` cobrem o ano
todo), e um mês antigo não tira o mais recente do `latest.json`.

### Use com a sua IA

1. **Só ler o dashboard.** Nada a configurar: o boletim do mês fica no topo do
   [dashboard](https://radar.graaos.cloud/) e o histórico em
   [`boletins/`](boletins/).
2. **Fork com o seu próprio secret.** Faça um fork. No fork, abra a aba **Actions** e ative
   os workflows (o GitHub os deixa desligados em forks). Em **Settings → Environments**,
   crie o environment `boletim-ia` e, nele, o secret `ANTHROPIC_API_KEY` (de preferência
   restrito ao branch `main`, em *Deployment branches*). Para outro provedor, use os
   secrets `OPENAI_API_KEY` e as variables `RADAR_LLM_PROVIDER`, `RADAR_LLM_MODEL` e
   `OPENAI_BASE_URL`. Em **Settings → Pages**, escolha **Source: GitHub Actions**. Depois,
   **Actions → Atualização mensal → Run workflow**. Sem o secret, o workflow segue
   atualizando os dados e só pula o boletim, com um aviso.
3. **Local, com `.env` ou com Ollama, de graça.**

   ```bash
   python -m src.boletim --mock                         # formato do boletim, sem IA e sem chave
   cp .env.example .env                                 # preencha a chave do seu provedor
   set -a; . ./.env; set +a
   python -m src.boletim --saida boletins/local         # grava boletins/local/AAAA-MM.md e latest.json
   ```

   `boletins/local/` fica fora do Git, como `boletins/mock/`. Sem `--saida`, o programa
   grava em `boletins/`, onde o boletim oficial do mês já está versionado: ele não refaz o
   mês sem `--forcar`, e com `--forcar` sobrescreve a sua cópia (para voltar:
   `git checkout -- boletins`).

   Com o [Ollama](https://ollama.com/), sem chave e sem custo: baixe um modelo
   (`ollama pull <modelo>`) e rode
   `RADAR_LLM_PROVIDER=ollama RADAR_LLM_MODEL=<modelo> python -m src.boletim --saida boletins/local`.
   O pedido já leva contexto para o prompt inteiro (`num_ctx` de pelo menos 16 mil tokens);
   o modelo precisa caber na sua máquina com esse contexto. `OLLAMA_HOST` aceita `host`,
   `host:porta` ou `host/caminho` (porta 11434 se faltar, como no próprio Ollama) ou a URL
   completa com a porta (`http://host:11434`); na URL completa sem porta vale a padrão do
   esquema (80 ou 443). Modelos locais pequenos
   tendem a reprovar mais na validação, mas a regra é a mesma: número inventado não passa.
4. **MCP com o Claude Desktop** (fase 10, ainda não implementada): um servidor MCP local
   para consultar os dados do Graaos direto do Claude Desktop.

## Dados brutos

Ficam em Parquet, particionados por ano no estilo Hive:
`data/raw/ano=AAAA/importacoes.parquet`. A coluna `ano` fica no caminho, não dentro do
arquivo.

| Coluna | Descrição |
| --- | --- |
| `mes` | mês (1 a 12) |
| `co_ncm` | NCM, 8 dígitos |
| `co_pais`, `no_pais` | país de origem: código e nome |
| `sg_uf`, `no_uf` | UF do produto (na importação, a UF de destino da mercadoria, qualquer que seja a sede do importador): sigla e nome |
| `co_via`, `no_via` | via de transporte: código e nome |
| `co_urf`, `no_urf` | URF de desembaraço: código e nome |
| `vl_fob` | valor US$ FOB |
| `kg_liquido` | peso líquido, em kg |
| `vl_cif` | valor US$ CIF (FOB + frete + seguro) |
| `vl_frete` | frete, em US$ |
| `vl_seguro` | seguro, em US$ |

Para ler todos os anos com a coluna `ano`:

```python
from src.ingest import ler_raw

df = ler_raw()
```

A exportação de soja fica em `data/raw_soja/ano=AAAA/exportacoes.parquet`, uma linha por
mês, com `mes`, `co_ncm` (12019000), `vl_fob` e `kg_liquido`. Para ler:
`from src.ingest import ler_soja`.

A Pink Sheet fica em `data/raw_benchmark/pink_sheet_mensal.csv`, uma linha por mês, com o
preço em US$ por tonelada métrica, nominal, de `ureia`, `dap`, `tsp`, `kcl` e
`rocha_fosfatica`, e o `fonte.json` ao lado registra a edição (data de atualização e a
última data legível já vista), a URL,
o sha256 do arquivo baixado e a licença. A PTAX fica em
`data/raw_cambio/ptax_venda_media_mensal.csv`, com `mes` e `brl_por_usd`. Para ler:
`from src.benchmark import ler_pink, ler_ptax`.

A série de 2015-01 a 2026-09 tem 61.460 linhas e ocupa 1,6 MB (mais 37 KB da soja e 8 KB
da Pink Sheet e da PTAX), por isso `data/raw/` e as outras pastas de dados brutos continuam
versionados. Se passar de ~20 MB, a pasta vai para o `.gitignore` e os dados
passam a ser gerados localmente com os comandos acima. Os totais anuais de kg, US$ FOB,
US$ CIF, frete e seguro foram conferidos, até a unidade, com uma consulta agregada
independente à API, que filtra pelas posições SH4 em vez da lista de NCMs.

## Tabelas analíticas (fase 2)

`python -m src.transform` lê `data/raw/` (e as outras pastas de dados brutos) e grava em `data/processed/`:

| Arquivo | Grão | Conteúdo |
| --- | --- | --- |
| `precos_mes_grupo_origem.csv` | mês × grupo × país | toneladas, US$ FOB, CIF e frete, e os preços implícitos em US$/t |
| `precos_mes_grupo.csv` | mês × grupo | o mesmo, somando todas as origens |
| `share_origem_ano.csv` | ano × grupo × país | toneladas, share, variação sobre o ano anterior (% e p.p.), US$/t FOB |
| `share_urf_ano.csv` | ano × grupo × URF | o mesmo, por URF |
| `hhi_ano_grupo.csv` | ano × grupo | HHI, número de origens, país líder e seu share, CR3 |
| `sazonalidade_grupo.csv` | grupo × mês | média de toneladas por mês do calendário e índice sazonal |
| `rotas_ano.csv` | ano × grupo × país × URF | [rotas](#rotas-e-frete-por-rota): toneladas, US$ FOB, CIF e frete, frete US$/t e % do CIF, share, meses com carga e se passa do corte de volume |
| `troca_mensal.csv` | mês × produto | [relação de troca](#relação-de-troca-no-porto): preço CIF do fertilizante, preço FOB da soja, t de soja por t e sacas por t |
| `benchmark_mensal.csv` | mês × produto | [Brasil x mercado internacional](#brasil-x-mercado-internacional-fase-6): toneladas, preços FOB e CIF do Brasil, benchmark e base, spread FOB, custo posto, spread comparável (US$/t e %), PTAX, CIF em reais e a marca de amostra pequena |
| `share_uf_ano.csv` | ano × grupo × UF | [importações por estado](#importações-por-estado): toneladas, share e variação sobre o mesmo período do ano anterior, por UF de destino |
| `custo_nutriente_mensal.csv` | mês × produto | [custo por nutriente](#custo-por-nutriente-custo-por-molécula): teores, toneladas, CIF, custo por tonelada de nutriente com e sem o crédito de N, custo por ponto, PTAX e custo em reais |
| `dashboard.json` | — | export compacto para o dashboard: séries mensais, resumo anual, top 10 origens e URFs, rotas, sazonalidade, relação de troca, benchmark, estados e custo por nutriente |

Como os números são calculados:

- **Preço implícito:** valor total ÷ toneladas, ou seja, média ponderada pelo peso.
  Células com poucas toneladas podem ter preços extremos; use a coluna `toneladas` para
  filtrar.
- **Share:** participação nas toneladas do ano e do grupo. O grupo `TOTAL` soma N, P, K e
  NPK.
- **Variação sobre o ano anterior:** compara sempre os mesmos meses. Em 2026 (jan-set), a
  base é jan-set de 2025. Quem importou no ano anterior e parou aparece com zero.
- **HHI (Herfindahl-Hirschman):** soma dos quadrados dos shares, em %, por país de origem,
  de 0 a 10.000. Como referência, as diretrizes de fusões dos EUA (2023) consideram
  altamente concentrado um mercado com HHI acima de 1.800.
- **Sazonalidade:** média de toneladas de cada mês nos anos completos (2015 a 2025). O
  índice sazonal é a média do mês ÷ média dos 12 meses × 100.
- `n_meses` marca anos parciais. As saídas são determinísticas: os mesmos dados geram
  arquivos idênticos.

### Rotas e frete por rota

[![Diagrama de fluxo das importações de 2026 (jan-set), das maiores origens para as maiores URFs, e a tabela de frete por rota](dashboard/print-rotas.png)](https://radar.graaos.cloud/)

Uma rota é o par país de origem → URF de desembaraço, somado no ano (`rotas_ano.csv`). O
frete US$/t da rota é o frete total ÷ toneladas, isto é, a média ponderada pelo peso.

- **Diagrama de fluxo:** as 7 maiores origens e as 7 maiores URFs de cada ano e grupo, desde
  que tenham pelo menos 2% das toneladas; o resto entra em "Outros". A espessura é
  proporcional às toneladas. É desenhado em SVG próprio, sem biblioteca externa.
- **Corte de volume relevante:** a tabela de frete só mostra rotas com **pelo menos 50 mil t
  no ano**, cerca de um navio Supramax cheio. Num ano parcial, o corte é proporcional aos
  meses com dados (em 2026, jan-set: 37.500 t). Abaixo disso o frete por tonelada é
  ruidoso, porque vem de cargas pequenas, em contêiner, fracionadas ou até aéreas: em
  2025, o frete das rotas acima do corte ficou entre US$ 19 e 36/t (percentis 10 e 90),
  enquanto 53 das 164 rotas com carga e menos de 5 mil t passaram de US$ 100/t, e amostras de
  poucos quilos por aeroporto passaram de US$ 10 mil/t. Mesmo assim, o corte guarda a maior
  parte do volume: em 2025, 107 das 401 rotas com carga e 93,2% das toneladas.
- O dashboard mostra as 20 maiores rotas acima do corte; todas, inclusive as abaixo dele
  (coluna `relevante`), estão no CSV.

### Relação de troca no porto

[![Relação de troca no porto entre soja e KCl, ureia e MAP, de 2015 a 2026, com a média histórica de cada produto](dashboard/print-troca.png)](https://radar.graaos.cloud/)

Quantas toneladas de soja exportada pagam 1 t de fertilizante importado, mês a mês
(`src/troca.py`):

```text
relação de troca = preço CIF do fertilizante (US$/t) ÷ preço FOB da soja (US$/t)
```

| Produto | NCM | Grupo |
| --- | --- | --- |
| KCl (cloreto de potássio) | 31042090 | K |
| Ureia (mais de 45% de N) | 31021010 | N |
| MAP (fosfato monoamônico) | 31054000 | P |
| Soja em grão, exceto para semeadura (exportação) | 12019000 | — |

Os NCMs foram conferidos na tabela auxiliar de NCM da API. Os preços são implícitos
(valor ÷ toneladas) do mês inteiro, e as sacas são de 60 kg. A média histórica de
referência é a média simples das relações mensais de toda a série (2015-01 em diante).

**É uma relação "no porto", não o preço ao produtor.** Compara a soja no embarque (FOB) com
o fertilizante no desembarque (CIF). Ficam de fora o frete interno nos dois sentidos,
impostos, armazenagem, margens da revenda e das tradings, prêmios de porto e o descasamento
entre a compra do adubo e a venda da safra. Serve para ver a direção e a intensidade do
poder de compra da soja frente ao adubo importado, não para orçar a compra de uma fazenda.

A relação é US$/US$ e não muda com o câmbio: a soja e o fertilizante estão em dólar.

### Custo por nutriente (custo por molécula)

[![Custo por tonelada de nutriente no porto, de 2015 a 2026: ureia (N), MAP (P₂O₅) e KCl (K₂O)](dashboard/print-nutrientes.png)](https://radar.graaos.cloud/)

O que se compra no adubo é o nutriente. Uma tonelada de ureia traz 460 kg de N; uma de KCl,
600 kg de K₂O. Para comparar produtos diferentes, `src/nutrientes.py` divide o preço CIF pelo
teor do nutriente (`custo_nutriente_mensal.csv` e o cartão "Custo por nutriente" do dashboard):

```text
custo do nutriente (US$/t)    = CIF do produto (US$/t) ÷ teor do nutriente
MAP e DAP (P₂O₅)              = (CIF − teor de N × custo do N da ureia no mês) ÷ teor de P₂O₅
custo por ponto (MAP e DAP)   = CIF ÷ (teor de N + teor de P₂O₅)
em reais (R$/t)               = custo em US$ × PTAX venda média do mês
```

Exemplo: ureia a US$ 460/t CIF, com 46% de N, sai a US$ 1.000 por tonelada de N.

| Produto | NCM | Teor usado | Nutriente |
| --- | --- | --- | --- |
| Ureia | 31021010 | 46% de N | N |
| Sulfato de amônio | 31022100 | 21% de N | N |
| Nitrato de amônio | 31023000 | 34% de N | N |
| MAP | 31054000 | 11% de N e 52% de P₂O₅ | P₂O₅ (com crédito de N) |
| DAP | 31053000, 31053010, 31053090 | 18% de N e 46% de P₂O₅ | P₂O₅ (com crédito de N) |
| TSP | 31031030, 31031100 | 46% de P₂O₅ | P₂O₅ |
| KCl | 31042090 | 60% de K₂O | K₂O |

- **Teores:** o grau comercial usual de cada produto, porque o Comex Stat não traz o teor de
  cada carga. Um teor diferente muda o custo na mesma proporção (ureia com 45% de N em vez de
  46%: custo do N 2,2% maior).
- **Crédito de N:** o MAP e o DAP levam N junto com o P₂O₅. Esse N vale o que custaria
  comprado na ureia do mesmo mês, e o resto do preço é o custo do P₂O₅. O CSV e o tooltip
  trazem também o custo sem esse desconto. O enxofre do sulfato de amônio (cerca de 24% de S)
  não é descontado.
- **Amostra pequena:** meses com menos de 10 mil t do produto ficam fora do gráfico (um navio
  decide o preço) e aparecem marcados na tabela.
- **No porto:** é o custo CIF, antes de impostos, frete interno e margens. Não é o preço ao
  produtor. Em reais, pela PTAX venda média do mês, e não pelo câmbio de cada declaração.

Fonte: Comex Stat/MDIC; BCB/PTAX.

### Importações por estado

[![Painel do Brasil por estado no mapa do topo: um quadrado por UF, na cor da variação de 2025 sobre 2024, com as maiores altas e quedas em toneladas](dashboard/print-estados.png)](https://radar.graaos.cloud/)

O Comex Stat traz a UF de cada importação: a **UF de destino da mercadoria**, qualquer que seja
a sede do importador, e não a UF do porto de chegada. Mato Grosso, sem porto, aparece entre os
maiores destinos. O transform soma as toneladas por ano, grupo e UF (`share_uf_ano.csv`, com o
share e a variação sobre os mesmos meses do ano anterior; quem parou de importar entra com
zero) e o dashboard mostra isso no painel do Brasil, no mapa do topo:

- um quadrado por UF, na posição aproximada do mapa, na cor da variação em toneladas (quente
  para alta, fria para queda, mais forte até ±50%); estados com menos de 0,5% do total nos dois
  períodos ficam em cinza, porque um navio a mais ou a menos muda muito a porcentagem deles;
- o total, a variação e quantos estados cresceram; as três maiores altas e quedas em toneladas;
- filtros de grupo (todos, N, P, K, NPK) e de ano (o último ano completo e o ano em curso);
  as rotas do mapa viram as das maiores origens do grupo e do ano escolhidos;
- a tabela com todos os estados. Pelo teclado, as setas andam entre os estados vizinhos.

Fonte: Comex Stat/MDIC.

## Brasil x mercado internacional (fase 6)

[![Preço do KCl importado pelo Brasil contra o benchmark do Banco Mundial, de 2015 a 2026, com a mudança de base em 2020, e o spread na base comparável](dashboard/print-benchmark.png)](https://radar.graaos.cloud/)

Compara, mês a mês, o preço implícito pago pelo Brasil com o preço internacional de
referência (benchmark) da [Pink Sheet do Banco Mundial](https://www.worldbank.org/en/research/commodity-markets),
e converte o CIF para reais pela PTAX do BCB (`src/comparacao.py`). A tabela de
correspondência é fixa:

| Produto | NCM (Comex Stat) | Série da Pink Sheet | Base do benchmark | Comparação |
| --- | --- | --- | --- | --- |
| KCl (cloreto de potássio) | 31042090 | Potassium chloride | FOB Vancouver até 2019-12; CFR Brasil, granulado, desde 2020-01 | FOB×FOB até 2019-12; CIF×CFR desde 2020-01 |
| Ureia | 31021010 | Urea | FOB Mar Negro até 2022-02; FOB Oriente Médio desde 2022-03 | FOB×FOB |
| MAP (fosfato monoamônico) | 31054000 | DAP, por aproximação: a Pink Sheet não tem série de MAP | FOB Golfo dos EUA | FOB×FOB |
| DAP (fosfato diamônico) | 31053000, 31053010, 31053090 | DAP | FOB Golfo dos EUA | FOB×FOB |
| TSP (superfosfato triplo) | 31031030, 31031100 | TSP | importação no Golfo dos EUA | CIF×CFR |

A rocha fosfática (Phosphate rock, FOB Norte da África) é baixada e guardada, mas não entra
na comparação: ela é do capítulo 25 da NCM, fora do capítulo 31 acompanhado pelo projeto.

```text
preço FOB Brasil (US$/t)   = US$ FOB ÷ toneladas, no mês, somando os NCMs do produto
preço CIF Brasil (US$/t)   = US$ CIF ÷ toneladas
spread comparável (US$/t)  = FOB Brasil − benchmark, quando o benchmark é FOB (FOB×FOB)
                           = CIF Brasil − benchmark, quando o benchmark já é um preço no destino (CIF×CFR)
spread comparável (%)      = spread comparável ÷ benchmark × 100
custo posto (US$/t)        = CIF Brasil − benchmark, em qualquer base
CIF em reais (R$/t)        = CIF Brasil × PTAX venda média do mês
```

- **Amostra pequena:** meses com menos de 10 mil t do produto ficam marcados em
  `amostra_pequena`, fora dos gráficos e do boletim, e continuam na tabela e no CSV. Com poucas cargas, um navio decide o preço. Na série, isso
  acontece em 41 meses do DAP e em 5 do TSP.
- **Mudança de base:** o gráfico marca com uma linha vertical o mês em que o Banco Mundial
  trocou a base do benchmark (KCl em 2020-01, ureia em 2022-03), e o CSV traz a coluna
  `quebra`. O boletim só compara o spread de um ano para o outro quando a base é a mesma.
- **Ingestão da Pink Sheet:** o endereço do arquivo muda a cada edição, então o programa lê
  a página [Commodity Markets](https://www.worldbank.org/en/research/commodity-markets),
  pega o link do `CMO-Historical-Data-Monthly.xlsx` (só de hosts do Banco Mundial, por
  HTTPS, até 20 MB) e lê a aba "Monthly Prices". Como o Banco Mundial revisa meses passados,
  o arquivo é regravado inteiro a cada ingestão, e o log lista os valores revisados. Falta
  de um mês no meio da série, ou um arquivo com menos meses do que já está gravado, é erro.
  A edição sai no 2º dia útil de cada mês.
- **Ingestão da PTAX:** série 3698 do SGS do Banco Central (PTAX venda, média mensal, a
  média simples das cotações dos dias úteis), em janelas de 5 anos, só com os meses que
  faltam (em trechos de meses seguidos). Se o SGS diz que publicou um mês e ele não vem, é
  erro. O firewall do BCB às vezes devolve uma página HTML "Requisição inválida!" com status
  200; o cliente manda `Accept: application/json`, repete e recusa resposta que não seja
  JSON.
- **Cliente das duas fontes:** só https e só os hosts do Banco Mundial e do BCB, conferidos
  na URL que o requests de fato usa (barra invertida, "@" e caractere de controle reprovam),
  inclusive nos redirecionamentos; corpo lido em blocos com limite de tamanho (20 MB no
  xlsx, também descompactado com teto, e 5 MB na página e no JSON); novas tentativas em
  429, 5xx, falha de rede, corte no meio do corpo e erro TLS transitório, com o Retry-After
  limitado; a falha de verificação do certificado aborta. O redirecionamento é seguido
  pelo próprio cliente, sem ler o corpo do 3xx. Uma edição da Pink Sheet mais antiga que a
  gravada é recusada, mesmo depois de uma edição com a data "Updated on" ilegível.

### Limitações

- **Referência de outro lugar, não ágio.** O benchmark é o preço num porto de referência
  (Golfo dos EUA, Oriente Médio, Mar Negro, Vancouver) ou no destino (KCl desde 2020, TSP),
  num momento de contrato diferente. O spread mistura origem (o Brasil compra de vários
  países), frete até o ponto de referência, prazo de contrato e a defasagem entre o
  fechamento do negócio e o desembarque registrado no Comex Stat. Na alta de 2021-22, por
  exemplo, o CIF do KCl ficou muito abaixo do CFR Brasil spot (abr/2022: US$ 724/t contra
  US$ 1.202/t, −39,8%) e, na queda, acima (out/2022: US$ 847/t contra US$ 622,5/t, +36,1%).
- **Preço implícito é média.** Valor ÷ peso de todas as declarações do mês, que somam
  especificações e contratos diferentes no mesmo NCM.
- **MAP pelo DAP.** São produtos próximos, mas não iguais; o spread do MAP inclui a
  diferença de preço entre eles.
- **Bases trocadas retroativamente.** O Banco Mundial substituiu a série do KCl, desde
  2020, pela CFR Brasil (a edição de outubro de 2021 ainda trazia FOB Vancouver nesses
  meses) e passou a ureia para o Oriente Médio em março de 2022. A série guardada é sempre a
  da edição mais recente, registrada no `fonte.json`.
- **Base do TSP de 2015 a 2017.** A Pink Sheet descreve o TSP como "spot, import US Gulf"
  na edição atual e nas de outubro de 2021 e março de 2022, que trazem os mesmos valores de
  2018 em diante; uma edição de 2014 o descrevia como TSP tunisiano granulado FOB. A base de 2015 a 2017 não está confirmada, e o
  projeto trata a série toda como preço no destino (CIF×CFR).
- **CIF em reais pela PTAX média do mês,** e não pelo câmbio da data de cada declaração, que
  o Comex Stat não publica. Serve para dimensionar o preço em reais, não como custo contábil.

### Licenças das fontes

- **Pink Sheet:** World Bank Commodity Price Data (The Pink Sheet), do Banco Mundial, com
  licença [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Os preços vêm de
  fontes como Bloomberg e Green Markets, compilados e publicados pelo Banco Mundial.
- **PTAX:** série 3698 do SGS, do Banco Central do Brasil. O Portal de Dados Abertos do BCB
  publica as séries de câmbio do dólar com licença
  [ODbL](https://opendatacommons.org/licenses/odbl/) (a série 3698 não tem página própria
  lá). Os arquivos que trazem a PTAX (`data/raw_cambio/` e as colunas `ptax_brl_usd` e
  `cif_brl_t`) seguem a mesma licença.
- **Comex Stat:** dados abertos do MDIC.
- **Natural Earth:** base cartográfica do mapa (fronteiras, pontos de rótulo dos países e
  portos), em [domínio público](https://www.naturalearthdata.com/about/terms-of-use/).

O repositório não tem arquivo de licença próprio; os dados de terceiros seguem as licenças
das fontes.

## Observações sobre os dados e a API

- **URF não é exatamente o porto.** É a unidade da Receita onde a mercadoria foi
  desembaraçada. Para fertilizantes, quase sempre é o porto de chegada, mas pode ser um
  porto seco ou uma unidade no interior.
- **Códigos de URF mudam com o tempo.** A Receita reorganiza suas unidades. Na série,
  `PORTO DE SAO LUIS` some e `IRF SAO LUIS` aparece; o mesmo acontece entre
  `PORTO DE ARATU` e `ALF - SALVADOR`. Em séries longas por porto, agrupe por Região
  Fiscal (os dois primeiros dígitos do código) ou revise os nomes.
- **Consultas de vários anos.** Num período como 2015-01 a 2026-09, a API soma, em cada
  ano, só os meses do intervalo (jan a set). Por isso a ingestão consulta um ano por vez.
- **Limite de requisições.** A API responde HTTP 429 a chamadas seguidas. O cliente faz
  uma pausa de 10 s entre chamadas e repete com backoff exponencial (10, 20, 40... s),
  respeitando o tempo pedido pela API.
- **Revisões.** O MDIC pode revisar meses recentes. Use `--forcar` para baixá-los de novo.
- **Códigos.** A API devolve nomes de país, UF e via. Os códigos vêm das tabelas
  auxiliares da própria API. Se um nome não for encontrado, o código fica vazio e o log
  avisa.

### SSL

A API está atrás da Cloudflare, com certificado de uma autoridade certificadora pública.
A verificação TLS fica sempre ligada, com o bundle padrão do `certifi`.

- Se a API voltar a usar a cadeia ICP-Brasil, defina `COMEXSTAT_CA_BUNDLE` com o caminho
  do arquivo da cadeia (PEM ou DER, como os publicados pelo ITI). Ele é somado ao bundle
  padrão.
- Como último recurso, `--ssl-inseguro` repete as chamadas sem verificação. Isso só
  acontece depois de uma falha de verificação e só para `api-comexstat.mdic.gov.br`,
  sempre com aviso no log.

## Desenvolvimento

```bash
pip install -r requirements-dev.txt
pre-commit install      # o gitleaks passa a bloquear commits com segredos
python -m pytest        # os testes não acessam a rede
```

O hook do gitleaks é compilado em Go; o pre-commit usa o Go instalado ou baixa um
automaticamente.

O teste de navegador do mapa de fluxos (`tests/e2e/`) usa Playwright e Chromium e fica de fora
do CI: sem o Playwright, ou sem a variável `RADAR_E2E_LIBS` apontando para uma pasta com cópias
locais do `chart.umd.min.js` e do `anime.umd.min.js` (os testes não acessam a rede), ele é pulado.

O [CI](.github/workflows/ci.yml) roda em todo push e pull request: os testes em Python
3.10 e 3.13 e o gitleaks em todo o histórico do repositório. A exceção são os commits
automáticos de dados da fase 4, que não disparam outros workflows; os dados passam pelos testes
(Python 3.13) dentro do próprio workflow de atualização, e o gitleaks do push seguinte
varre o histórico inteiro, inclusive eles. O workflow só tem permissão
de leitura e não usa nenhum segredo. As actions estão em versões que rodam em Node 24.

Segredos nunca entram no repositório. As chaves do boletim (fase 5) são lidas apenas de
variáveis de ambiente, e no GitHub só do environment `boletim-ia`, acessível a um único
job. Os nomes estão em [`.env.example`](.env.example). Nenhum teste depende de chave.

## Roadmap

- [x] **Fase 1:** ingestão incremental via API do Comex Stat (`src/ingest.py`).
- [x] **Fase 2:** `src/transform.py` com preço US$/t, shares com variação anual, HHI,
  sazonalidade e o `dashboard.json`.
- [x] **Fase 3:** [dashboard estático](https://radar.graaos.cloud/) na Cloudflare e no GitHub Pages, lendo `data/processed/dashboard.json`.
- [x] Rotas origem → porto (diagrama de fluxo e frete por rota) e relação de troca soja x
  fertilizante no porto.
- [x] **Fase 4:** [atualização automática mensal](#atualização-automática-fase-4) da
  importação de fertilizantes e da exportação de soja, com commit do github-actions[bot]
  e novo deploy do dashboard.
- [x] **Fase 5:** [boletim mensal com IA](#boletim-mensal-com-ia-fase-5), com números
  inseridos e verificados pelo programa e provedor à escolha (Anthropic, API compatível com
  OpenAI ou Ollama).
- [x] **Fase 6:** [benchmark internacional e câmbio](#brasil-x-mercado-internacional-fase-6):
  Pink Sheet do Banco Mundial e PTAX do BCB, com a regra do mês completo na atualização.
- [ ] **Fase 7:** logística portuária com dados da ANTAQ (fila, espera, terminais).
- [ ] **Fase 8:** demanda e cobertura (CONAB).
- [ ] **Fase 9:** projeção de curto prazo.
- [ ] **Fase 10:** servidor MCP para consultar o Graaos no Claude Desktop.

## Fonte dos dados

Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX.

- **Comex Stat/MDIC.** Portal: <https://comexstat.mdic.gov.br/>. Documentação da API:
  <https://api-comexstat.mdic.gov.br/docs>. Para consultas muito grandes, o MDIC também
  publica a [base de dados bruta](https://www.gov.br/mdic/pt-br/assuntos/comercio-exterior/estatisticas/base-de-dados-bruta).
- **Banco Mundial, Pink Sheet** (CC BY 4.0): <https://www.worldbank.org/en/research/commodity-markets>.
- **Banco Central do Brasil, PTAX** (SGS 3698, ODbL):
  <https://www3.bcb.gov.br/sgspub/>; API: `https://api.bcb.gov.br/dados/serie/bcdata.sgs.3698/dados?formato=json`.

Base cartográfica do mapa: [Natural Earth](https://www.naturalearthdata.com/) v5.1.2 (domínio
público), ver [Base do mapa e tabelas de referência](#base-do-mapa-e-tabelas-de-referência).
