"""Configurações do Graaos (módulo Fertilizantes).

Tudo aqui é público. Segredos, se um dia existirem, vêm SOMENTE de variáveis de
ambiente (veja .env.example) e nunca são escritos em código.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

# --- Caminhos -------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent.parent
DADOS_DIR = RAIZ / "data"
RAW_DIR = DADOS_DIR / "raw"
PROCESSED_DIR = DADOS_DIR / "processed"
NCM_CSV = DADOS_DIR / "ncm_fertilizantes.csv"
# Um arquivo por ano, em partição no estilo Hive: data/raw/ano=AAAA/importacoes.parquet
RAW_ARQUIVO = "importacoes.parquet"
# Exportação de soja em grão (relação de troca): data/raw_soja/ano=AAAA/exportacoes.parquet
RAW_SOJA_DIR = DADOS_DIR / "raw_soja"
RAW_SOJA_ARQUIVO = "exportacoes.parquet"
# Boletim mensal com IA (fase 5): boletins/AAAA-MM.md e boletins/latest.json
BOLETINS_DIR = RAIZ / "boletins"

# --- API Comex Stat (dados abertos do MDIC, sem autenticação) --------------------
# Documentação: https://api-comexstat.mdic.gov.br/docs (spec OpenAPI em /docs/doc.yaml)
API_BASE_URL = "https://api-comexstat.mdic.gov.br"
API_HOST = urlsplit(API_BASE_URL).hostname
IDIOMA = "pt"
USER_AGENT = "graaos (+https://github.com/mvsantos-dev/graaos)"

ENDPOINT_CONSULTA = "/general"
ENDPOINT_ULTIMA_ATUALIZACAO = "/general/dates/updated"
ENDPOINT_NCM = "/tables/ncm"
ENDPOINT_PAISES = "/tables/countries"
ENDPOINT_UFS = "/tables/uf"
ENDPOINT_VIAS = "/tables/ways"

FLUXO = "import"
PRIMEIRO_MES = "1997-01"  # início da série mensal da API
# Detalhamentos (GET /general/details): país de origem, NCM, UF do produto, via e URF.
DETALHES = ["country", "ncm", "state", "via", "urf"]
# Métricas (GET /general/metrics) -> colunas gravadas no Parquet.
METRICAS = {
    "metricFOB": "vl_fob",  # US$ FOB
    "metricKG": "kg_liquido",  # quilograma líquido
    "metricCIF": "vl_cif",  # US$ CIF (só importação)
    "metricFreight": "vl_frete",  # US$ frete (só importação)
    "metricInsurance": "vl_seguro",  # US$ seguro (só importação)
}

# Soja em grão exceto para semeadura (confirmado em /tables/ncm/12019000): exportação mensal.
NCM_SOJA = "12019000"
METRICAS_SOJA = {"metricFOB": "vl_fob", "metricKG": "kg_liquido"}

# --- Rate limiting -----------------------------------------------------------------
# A API responde HTTP 429 ("tente novamente em 10 segundos") a chamadas em sequência.
PAUSA_ENTRE_CHAMADAS_S = 10.0
MAX_TENTATIVAS = 6
BACKOFF_BASE_S = 10.0  # 10, 20, 40, 80, 160 s...
BACKOFF_MAX_S = 300.0
TIMEOUT_S = (15, 180)  # (conexão, leitura)

# --- SSL ---------------------------------------------------------------------------
# Opcional: arquivo PEM ou DER com CAs extras (ex.: cadeia ICP-Brasil), somado ao
# bundle padrão na verificação TLS. A verificação nunca é desligada por padrão.
ENV_CA_EXTRA = "COMEXSTAT_CA_BUNDLE"

# --- Fase 6: benchmark internacional (Banco Mundial) e câmbio (BCB) ----------------------
# Banco Mundial, Commodity Price Data (The Pink Sheet), CC BY 4.0. A URL do xlsx muda a cada
# edição; ela é procurada na página Commodity Markets.
RAW_BENCHMARK_DIR = DADOS_DIR / "raw_benchmark"
PINK_ARQUIVO = "pink_sheet_mensal.csv"
PINK_FONTE = "fonte.json"
PINK_PAGINA = "https://www.worldbank.org/en/research/commodity-markets"
PINK_HOST_ARQUIVO = "thedocs.worldbank.org"
PINK_NOME_ARQUIVO = "CMO-Historical-Data-Monthly.xlsx"
PINK_ABA = "Monthly Prices"
PINK_MAX_BYTES = 20 * 1024 * 1024
PINK_MAX_DESCOMPACTADO = 200 * 1024 * 1024  # o xlsx real descompacta para poucos MB
MAX_BYTES_PAGINA = 5 * 1024 * 1024  # página do Banco Mundial e JSON do SGS
# Nome da coluna no xlsx (sem espaços nem asteriscos nas pontas) -> coluna gravada.
PINK_SERIES = {
    "Urea": "ureia",
    "DAP": "dap",
    "TSP": "tsp",
    "Potassium chloride": "kcl",
    "Phosphate rock": "rocha_fosfatica",
}
# As comparadas com o Brasil: um mês só está publicado quando as quatro têm valor.
PINK_OBRIGATORIAS = ("ureia", "dap", "tsp", "kcl")
# Banco Central do Brasil, SGS (dados abertos, ODbL). 3698: taxa de câmbio livre, dólar
# americano (venda), média do período, mensal (média das PTAX de venda dos dias úteis).
RAW_CAMBIO_DIR = DADOS_DIR / "raw_cambio"
PTAX_ARQUIVO = "ptax_venda_media_mensal.csv"
SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
SGS_PTAX_VENDA_MEDIA_MENSAL = 3698
SGS_JANELA_ANOS = 5
INICIO_FASE6 = "2015-01"
PAUSA_FONTES_S = 5.0
# Únicos hosts que o cliente das fontes externas acessa (inclusive em redirecionamentos).
HOSTS_FONTES = ("www.worldbank.org", "thedocs.worldbank.org", "api.bcb.gov.br")

# --- Fertilizantes (capítulo 31 do SH) ----------------------------------------------
# Grupo por posição (SH4)...
GRUPO_POR_SH4 = {"3102": "N", "3103": "P", "3104": "K", "3105": "NPK"}
# ...com exceções por subposição (SH6): DAP e MAP são fosfatados na convenção de mercado.
GRUPO_POR_SH6 = {"310530": "P", "310540": "P"}
