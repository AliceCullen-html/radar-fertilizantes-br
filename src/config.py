"""Configurações do radar-fertilizantes-br.

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

# --- API Comex Stat (dados abertos do MDIC, sem autenticação) --------------------
# Documentação: https://api-comexstat.mdic.gov.br/docs (spec OpenAPI em /docs/doc.yaml)
API_BASE_URL = "https://api-comexstat.mdic.gov.br"
API_HOST = urlsplit(API_BASE_URL).hostname
IDIOMA = "pt"
USER_AGENT = "radar-fertilizantes-br (+https://github.com/AliceCullen-html/radar-fertilizantes-br)"

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

# --- Fertilizantes (capítulo 31 do SH) ----------------------------------------------
# Grupo por posição (SH4)...
GRUPO_POR_SH4 = {"3102": "N", "3103": "P", "3104": "K", "3105": "NPK"}
# ...com exceções por subposição (SH6): DAP e MAP são fosfatados na convenção de mercado.
GRUPO_POR_SH6 = {"310530": "P", "310540": "P"}
