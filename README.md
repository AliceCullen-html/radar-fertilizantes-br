# radar-fertilizantes-br

Radar das importações brasileiras de fertilizantes, feito apenas com dados públicos do
[Comex Stat](https://comexstat.mdic.gov.br/), do Ministério do Desenvolvimento, Indústria,
Comércio e Serviços (MDIC).

**Fonte: Comex Stat/MDIC.**

## O que é

O Brasil importa a maior parte dos fertilizantes que usa. Este projeto baixa, mês a mês,
as importações de adubos minerais e químicos e organiza os dados para responder perguntas
como: de onde vem o potássio? Por quais portos entra a ureia? Quanto se paga, em média,
pela tonelada de MAP?

Cada linha traz país de origem, NCM, UF, via de transporte e URF (unidade da Receita
Federal onde a carga foi desembaraçada), com valor US$ FOB, peso líquido em kg, valor
US$ CIF, frete e seguro.

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
python -m src.ingest --inicio 2025-01 --fim 2025-03
```

A ingestão é incremental: só consulta os meses que ainda não estão em `data/raw/`.
Ao final, imprime um resumo do período. Saída da execução de teste:

```text
Resumo 2025-01 a 2025-03 (Fonte: Comex Stat/MDIC)
  Linhas: 1.245 | meses: 3 | NCMs: 30
  Total: 7.887.708 t | US$ FOB 2.465.674.057 | US$ CIF 2.681.476.795
  Top 5 países de origem por kg líquido:
    1. Rússia                            2.475.427 t   31,4%
    2. China                             1.789.967 t   22,7%
    3. Canadá                              770.746 t    9,8%
    4. Nigéria                             442.081 t    5,6%
    5. Marrocos                            338.982 t    4,3%
  Top 5 URFs por kg líquido:
    1. 0917800 PORTO DE PARANAGUA                2.001.060 t   25,4%
    2. 0817800 PORTO DE SANTOS                   1.179.645 t   15,0%
    3. 1017700 PORTO DE RIO GRANDE               1.121.599 t   14,2%
    4. 0517800 ALF - SALVADOR                      632.583 t    8,0%
    5. 0317903 IRF SAO LUIS                        598.648 t    7,6%
```

| Opção | O que faz |
| --- | --- |
| `--inicio AAAA-MM` | primeiro mês (a série mensal começa em 1997-01) |
| `--fim AAAA-MM` | último mês; o padrão é o último mês publicado pelo MDIC |
| `--forcar` | baixa de novo meses já gravados (o MDIC pode revisar meses recentes) |
| `--atualizar-ncm` | regenera `data/ncm_fertilizantes.csv` a partir da API |
| `--pausa SEG` | pausa mínima entre chamadas à API (padrão: 10 s) |
| `--ssl-inseguro` | último recurso para problemas de certificado; veja [SSL](#ssl) |

Para baixar o histórico completo, use `python -m src.ingest --inicio 1997-01`. São
algumas dezenas de consultas (uma por ano) e alguns minutos, por causa do limite de
requisições da API.

## Dados gerados

Os dados brutos ficam em Parquet, particionados por ano no estilo Hive:
`data/raw/ano=AAAA/importacoes.parquet`. A coluna `ano` fica no caminho, não dentro do
arquivo.

| Coluna | Descrição |
| --- | --- |
| `mes` | mês (1 a 12) |
| `co_ncm` | NCM, 8 dígitos |
| `co_pais`, `no_pais` | país de origem: código e nome |
| `sg_uf`, `no_uf` | UF do produto (na importação, a UF do importador): sigla e nome |
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

O repositório traz a amostra de 2025-01 a 2025-03 (cerca de 40 KB). O histórico completo
desde 1997 deve ocupar poucos MB, então `data/raw/` continua versionado. Se passar de
~20 MB, a pasta vai para o `.gitignore` e os dados passam a ser gerados localmente com os
comandos acima.

## Observações sobre os dados e a API

- **URF não é exatamente o porto.** É a unidade da Receita onde a mercadoria foi
  desembaraçada. Para fertilizantes, quase sempre é o porto de chegada, mas pode ser um
  porto seco ou uma unidade no interior.
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

Segredos nunca entram no repositório. Qualquer chave futura, como a da fase 5, é lida
apenas de variáveis de ambiente. Os nomes estão em [`.env.example`](.env.example).

## Roadmap

- [x] **Fase 1:** ingestão incremental via API do Comex Stat (`src/ingest.py`).
- [ ] **Fase 2:** `src/transform.py` com preço médio em US$/t e share por país de origem.
- [ ] **Fase 3:** dashboard estático no GitHub Pages.
- [ ] **Fase 4:** GitHub Action mensal para atualizar os dados.
- [ ] **Fase 5:** boletim mensal com IA (opcional; a chave vem só de variável de ambiente).

## Fonte dos dados

Fonte: Comex Stat/MDIC. Portal: <https://comexstat.mdic.gov.br/>. Documentação da API:
<https://api-comexstat.mdic.gov.br/docs>. Para consultas muito grandes, o MDIC também
publica a [base de dados bruta](https://www.gov.br/mdic/pt-br/assuntos/comercio-exterior/estatisticas/base-de-dados-bruta).
