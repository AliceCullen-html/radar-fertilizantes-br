"""Ingestão das importações brasileiras de fertilizantes.

Baixa da API pública do Comex Stat (MDIC) as importações mensais dos NCMs de
data/ncm_fertilizantes.csv, detalhadas por país de origem, NCM, UF, via e URF,
com as métricas US$ FOB, kg líquido, US$ CIF, frete e seguro. Grava em Parquet
particionado por ano (data/raw/ano=AAAA/importacoes.parquet) e só consulta os
meses que ainda não estão no disco.

Uso:
    python -m src.ingest --inicio 2025-01 --fim 2025-03
    python -m src.ingest --atualizar-ncm

Fonte: Comex Stat/MDIC.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import random
import re
import ssl
import sys
import tempfile
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import requests
from urllib3.exceptions import InsecureRequestWarning

from . import config

log = logging.getLogger(__name__)

COLUNAS_RAW = [
    "mes",
    "co_ncm",
    "co_pais",
    "no_pais",
    "sg_uf",
    "no_uf",
    "co_via",
    "no_via",
    "co_urf",
    "no_urf",
    *config.METRICAS.values(),
]
# Ordem estável das linhas, para arquivos reprodutíveis.
ORDEM_RAW = ["mes", "co_ncm", "no_pais", "no_uf", "no_via", "co_urf"]
COLUNAS_NCM = ["ncm", "descricao", "grupo", "sh4", "sh6"]

_RE_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_RE_SEGUNDOS = re.compile(r"(\d+)\s*segundo", re.IGNORECASE)


class ErroAPI(RuntimeError):
    """Falha ao consultar a API do Comex Stat."""


class ErroSSL(ErroAPI):
    """Falha na verificação TLS do host da API."""


# --- Cliente HTTP ---------------------------------------------------------------------


class ClienteComexStat:
    """Cliente mínimo da API do Comex Stat.

    - Respeita uma pausa mínima entre chamadas e repete com backoff exponencial em
      HTTP 429, 5xx e falhas de rede, usando o tempo sugerido pela API quando houver.
    - Verificação TLS sempre ligada; CAs extras opcionais via COMEXSTAT_CA_BUNDLE.
    - Com ssl_inseguro=True, e só depois de a verificação falhar, repete as chamadas
      sem verificação TLS. Vale apenas para este host: o cliente só monta URLs a partir
      de base_url e não segue redirecionamentos.
    """

    def __init__(
        self,
        base_url: str = config.API_BASE_URL,
        *,
        pausa: float = config.PAUSA_ENTRE_CHAMADAS_S,
        max_tentativas: int = config.MAX_TENTATIVAS,
        ssl_inseguro: bool = False,
        ca_extra: str | None = None,
        sessao: requests.Session | None = None,
        dormir=time.sleep,
        relogio=time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.host = urlsplit(self.base_url).hostname
        self.pausa = pausa
        self.max_tentativas = max_tentativas
        self.ssl_inseguro = ssl_inseguro
        self._dormir = dormir
        self._relogio = relogio
        self._ultima_chamada: float | None = None
        self.sessao = sessao if sessao is not None else requests.Session()
        self.sessao.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        # Passado em toda chamada: um `verify` na sessão seria ignorado quando
        # REQUESTS_CA_BUNDLE está definido. True = bundle padrão (certifi ou o do ambiente).
        self.verify: bool | str = True
        if ca_extra is None:
            ca_extra = os.environ.get(config.ENV_CA_EXTRA, "")
        if ca_extra:
            self.verify = _bundle_com_ca_extra(Path(ca_extra))
            log.info("Verificação TLS com CAs extras de %s=%s", config.ENV_CA_EXTRA, ca_extra)

    def get(self, caminho: str, params: dict | None = None):
        return self._requisitar("GET", caminho, params=params)

    def post(self, caminho: str, corpo: dict, params: dict | None = None):
        return self._requisitar("POST", caminho, json=corpo, params=params)

    def _requisitar(self, metodo: str, caminho: str, **kwargs):
        if not caminho.startswith("/"):
            raise ValueError(f"caminho deve começar com '/': {caminho!r}")
        url = self.base_url + caminho
        motivo = ""
        for tentativa in range(1, self.max_tentativas + 1):
            self._aguardar_pausa()
            try:
                resp = self._enviar(metodo, url, **kwargs)
            except requests.exceptions.SSLError as exc:  # antes de ConnectionError (é subclasse)
                if self.ssl_inseguro and self.verify is not False:
                    log.warning(
                        "Verificação TLS falhou para %s (%s). --ssl-inseguro ativo: repetindo "
                        "SEM verificação, apenas para este host.",
                        self.host,
                        exc,
                    )
                    self.verify = False
                    motivo = f"falha TLS ({exc})"
                    continue
                raise ErroSSL(
                    f"Falha na verificação TLS de {self.host}: {exc}. Se a API voltar a usar a "
                    f"cadeia ICP-Brasil, aponte {config.ENV_CA_EXTRA} para o arquivo da cadeia; "
                    "em último caso, use --ssl-inseguro."
                ) from exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                motivo, espera = f"falha de rede ({exc})", self._backoff(tentativa)
            else:
                if resp.status_code == 200:
                    return _dados(resp)
                if resp.status_code != 429 and resp.status_code < 500:
                    raise ErroAPI(f"HTTP {resp.status_code} em {metodo} {caminho}: {_mensagem(resp)}")
                motivo = f"HTTP {resp.status_code} ({_mensagem(resp)})"
                espera = self._backoff(tentativa, _espera_sugerida(resp))
            if tentativa < self.max_tentativas:
                log.warning(
                    "%s em %s %s; tentativa %d/%d em %.0f s",
                    motivo,
                    metodo,
                    caminho,
                    tentativa + 1,
                    self.max_tentativas,
                    espera,
                )
                self._dormir(espera)
        raise ErroAPI(f"{metodo} {caminho} falhou após {self.max_tentativas} tentativas: {motivo}")

    def _enviar(self, metodo: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", config.TIMEOUT_S)
        kwargs["allow_redirects"] = False  # nunca sair do host da API
        if self.verify is not False:
            return self.sessao.request(metodo, url, verify=self.verify, **kwargs)
        log.debug("Requisição SEM verificação TLS: %s %s", metodo, url)
        with warnings.catch_warnings():
            # Silencia só o aviso desta chamada; o risco já foi registrado no log.
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return self.sessao.request(metodo, url, verify=False, **kwargs)

    def _aguardar_pausa(self) -> None:
        if self._ultima_chamada is not None:
            falta = self.pausa - (self._relogio() - self._ultima_chamada)
            if falta > 0:
                self._dormir(falta)
        self._ultima_chamada = self._relogio()

    @staticmethod
    def _backoff(tentativa: int, sugerida: float | None = None) -> float:
        espera = min(config.BACKOFF_MAX_S, config.BACKOFF_BASE_S * 2 ** (tentativa - 1))
        return max(espera, sugerida or 0) + random.uniform(0, 1)


def _dados(resp: requests.Response):
    """Extrai o campo `data` de uma resposta 200 da API."""
    try:
        corpo = resp.json()
    except ValueError as exc:
        raise ErroAPI(f"Resposta da API não é JSON: {resp.text[:200]!r}") from exc
    if not isinstance(corpo, dict) or "data" not in corpo or corpo.get("success") is False:
        raise ErroAPI(f"Resposta inesperada da API: {str(corpo)[:300]}")
    return corpo["data"]


def _mensagem(resp: requests.Response) -> str:
    try:
        corpo = resp.json()
    except ValueError:
        return resp.text[:200]
    erro = corpo.get("error") if isinstance(corpo, dict) else None
    if isinstance(erro, dict) and erro.get("message"):
        return str(erro["message"])
    return str(corpo)[:200]


def _espera_sugerida(resp: requests.Response) -> float | None:
    """Segundos pedidos pela API: Retry-After ou "tente novamente em N segundos"."""
    valor = resp.headers.get("Retry-After", "")
    if valor.isdigit():
        return float(valor)
    achado = _RE_SEGUNDOS.search(_mensagem(resp))
    return float(achado.group(1)) if achado else None


def _bundle_com_ca_extra(ca_extra: Path) -> str:
    """Grava um bundle temporário (CAs padrão + CAs extras) e devolve o caminho."""
    if not ca_extra.is_file():
        raise ErroSSL(f"{config.ENV_CA_EXTRA} aponta para um arquivo inexistente: {ca_extra}")
    extra = ca_extra.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" not in extra:  # o .crt do ITI costuma vir em DER
        extra = ssl.DER_cert_to_PEM_cert(extra).encode("ascii")
    base = (
        os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("CURL_CA_BUNDLE")
        or requests.certs.where()
    )
    with tempfile.NamedTemporaryFile("wb", prefix="radar-ca-", suffix=".pem", delete=False) as arq:
        arq.write(Path(base).read_bytes() + b"\n" + extra)
    atexit.register(Path(arq.name).unlink, missing_ok=True)
    return arq.name


# --- Tabelas auxiliares -------------------------------------------------------------


def _lista(dados) -> list[dict]:
    """A API devolve tabelas como lista ou como {"list": [...], "count": n}."""
    return dados["list"] if isinstance(dados, dict) else dados


def grupo_do_ncm(ncm: str) -> str:
    """N, P, K ou NPK pela posição (SH4), com as exceções por subposição (SH6)."""
    return config.GRUPO_POR_SH6.get(ncm[:6]) or config.GRUPO_POR_SH4[ncm[:4]]


def buscar_ncms_fertilizantes(cliente, por_pagina: int = 100) -> pd.DataFrame:
    """Lista os NCMs das posições 3102 a 3105 a partir da tabela auxiliar /tables/ncm."""
    itens: list[dict] = []
    for sh4 in config.GRUPO_POR_SH4:
        pagina = 1
        while True:
            dados = cliente.get(
                config.ENDPOINT_NCM,
                params={
                    "language": config.IDIOMA,
                    "search": sh4,
                    "add": "sh",
                    "page": pagina,
                    "perPage": por_pagina,
                },
            )
            lista = _lista(dados)
            # A busca casa qualquer trecho do código (ex.: "3102" casa 61031020).
            itens += [
                i
                for i in lista
                if i.get("headingCode") == sh4 and str(i.get("coNcm", "")).startswith(sh4)
            ]
            total = int(dados.get("count", 0)) if isinstance(dados, dict) else 0
            if len(lista) < por_pagina or pagina * por_pagina >= total:
                break
            pagina += 1
    if not itens:
        raise ErroAPI("A tabela /tables/ncm não retornou NCMs das posições 3102 a 3105.")
    df = pd.DataFrame(
        {
            "ncm": [str(i["coNcm"]) for i in itens],
            "descricao": [" ".join(str(i["noNCM"]).split()) for i in itens],
            "sh4": [str(i["headingCode"]) for i in itens],
            "sh6": [str(i["subHeadingCode"]) for i in itens],
        }
    )
    df["grupo"] = df["ncm"].map(grupo_do_ncm)
    return df.drop_duplicates("ncm").sort_values("ncm", ignore_index=True)[COLUNAS_NCM]


def salvar_ncms(df: pd.DataFrame, caminho: Path = config.NCM_CSV) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df[COLUNAS_NCM].to_csv(caminho, index=False, encoding="utf-8", lineterminator="\n")


def carregar_ncms(caminho: Path = config.NCM_CSV) -> pd.DataFrame:
    df = pd.read_csv(caminho, dtype=str, keep_default_na=False)
    faltando = set(COLUNAS_NCM) - set(df.columns)
    if faltando:
        raise ValueError(f"{caminho} sem as colunas {sorted(faltando)}")
    return df


def carregar_tabelas_auxiliares(cliente) -> dict[str, dict[str, str]]:
    """Nome -> código de países, UFs e vias (a consulta /general devolve só os nomes)."""
    paises = _lista(cliente.get(config.ENDPOINT_PAISES))
    ufs = _lista(cliente.get(config.ENDPOINT_UFS))
    vias = _lista(cliente.get(config.ENDPOINT_VIAS))
    return {
        "co_pais": {i["text"]: i["id"] for i in paises},
        "sg_uf": {i["text"]: i["uf"] for i in ufs},
        "co_via": {i["text"]: i["id"] for i in vias},
    }


# --- Consulta ----------------------------------------------------------------------------


def montar_consulta(inicio: str, fim: str, ncms: list[str]) -> dict:
    """Corpo do POST /general: importações mensais dos NCMs, com todos os detalhamentos."""
    return {
        "flow": config.FLUXO,
        "monthDetail": True,
        "period": {"from": inicio, "to": fim},
        "filters": [{"filter": "ncm", "values": list(ncms)}],
        "details": list(config.DETALHES),
        "metrics": list(config.METRICAS),
    }


def normalizar(registros: list[dict], tabelas: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Converte as linhas da API em colunas tipadas, com códigos e nomes."""
    if not registros:
        return pd.DataFrame(columns=["ano", *COLUNAS_RAW])
    api = pd.DataFrame.from_records(registros)
    urf = api["urf"].str.split(" - ", n=1, expand=True)  # "0917800 - PORTO DE PARANAGUA"
    df = pd.DataFrame(
        {
            "ano": pd.to_numeric(api["year"]).astype("int64"),
            "mes": pd.to_numeric(api["monthNumber"]).astype("int64"),
            "co_ncm": api["coNcm"].astype(str),
            "no_pais": api["country"],
            "no_uf": api["state"],
            "no_via": api["via"],
            "co_urf": urf[0].str.strip(),
            "no_urf": urf[1].str.strip() if 1 in urf else None,
        }
    )
    df["co_pais"] = df["no_pais"].map(tabelas["co_pais"])
    df["sg_uf"] = df["no_uf"].map(tabelas["sg_uf"])
    df["co_via"] = df["no_via"].map(tabelas["co_via"])
    for metrica, coluna in config.METRICAS.items():
        # Valores inteiros (US$ e kg); astype("Int64") falha se vier fração.
        df[coluna] = pd.to_numeric(api[metrica]).astype("Int64")
    for coluna, nome in [("co_pais", "no_pais"), ("sg_uf", "no_uf"), ("co_via", "no_via")]:
        sem_codigo = sorted(df.loc[df[coluna].isna(), nome].dropna().unique())
        if sem_codigo:
            log.warning("Sem código nas tabelas auxiliares para %s: %s", nome, sem_codigo)
    return df[["ano", *COLUNAS_RAW]]


# --- Parquet incremental ------------------------------------------------------------


def mes_valido(texto: str) -> str:
    """Valida AAAA-MM (usado como `type` do argparse)."""
    if not _RE_MES.match(texto):
        raise argparse.ArgumentTypeError(f"mês inválido {texto!r}; use AAAA-MM, ex.: 2025-01")
    return texto


def meses_do_intervalo(inicio: str, fim: str) -> list[tuple[int, int]]:
    ano, mes = map(int, inicio.split("-"))
    ano_fim, mes_fim = map(int, fim.split("-"))
    meses = []
    while (ano, mes) <= (ano_fim, mes_fim):
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def arquivo_do_ano(raw_dir: Path, ano: int) -> Path:
    return Path(raw_dir) / f"ano={ano}" / config.RAW_ARQUIVO


def meses_gravados(raw_dir: Path, ano: int) -> set[int]:
    arquivo = arquivo_do_ano(raw_dir, ano)
    if not arquivo.exists():
        return set()
    return {int(m) for m in pd.read_parquet(arquivo, columns=["mes"])["mes"].unique()}


def planejar(inicio: str, fim: str, raw_dir: Path, forcar: bool = False) -> dict[int, list[int]]:
    """Meses a baixar por ano: os que ainda não estão no disco (ou todos, com forcar)."""
    plano: dict[int, list[int]] = {}
    for ano, mes in meses_do_intervalo(inicio, fim):
        plano.setdefault(ano, []).append(mes)
    if not forcar:
        for ano in plano:
            gravados = meses_gravados(raw_dir, ano)
            plano[ano] = [m for m in plano[ano] if m not in gravados]
    return {ano: meses for ano, meses in plano.items() if meses}


def gravar_ano(df: pd.DataFrame, ano: int, raw_dir: Path) -> Path:
    """Junta os meses novos aos já gravados do ano e regrava o arquivo de forma atômica."""
    arquivo = arquivo_do_ano(raw_dir, ano)
    novo = df.drop(columns="ano", errors="ignore")
    if arquivo.exists():
        atual = pd.read_parquet(arquivo)
        novo = pd.concat([atual[~atual["mes"].isin(novo["mes"])], novo], ignore_index=True)
    novo = novo[COLUNAS_RAW].sort_values(ORDEM_RAW, ignore_index=True)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(arquivo.name + ".tmp")
    novo.to_parquet(temporario, index=False, compression="zstd")
    os.replace(temporario, arquivo)
    return arquivo


def ler_raw(raw_dir: Path = config.RAW_DIR) -> pd.DataFrame:
    """Lê todos os anos gravados, recolocando a coluna `ano` (que fica no caminho)."""
    partes = []
    for arquivo in sorted(Path(raw_dir).glob(f"ano=*/{config.RAW_ARQUIVO}")):
        df = pd.read_parquet(arquivo)
        df.insert(0, "ano", int(arquivo.parent.name.removeprefix("ano=")))
        partes.append(df)
    if not partes:
        return pd.DataFrame(columns=["ano", *COLUNAS_RAW])
    return pd.concat(partes, ignore_index=True)


# --- Orquestração ----------------------------------------------------------------------


def ultimo_mes_publicado(cliente) -> str:
    dados = cliente.get(config.ENDPOINT_ULTIMA_ATUALIZACAO)
    return f"{int(dados['year']):04d}-{int(dados['monthNumber']):02d}"


def ingerir(
    cliente,
    inicio: str,
    fim: str | None = None,
    *,
    raw_dir: Path = config.RAW_DIR,
    ncm_csv: Path = config.NCM_CSV,
    forcar: bool = False,
) -> tuple[str, str]:
    """Baixa os meses faltantes de [inicio, fim], uma consulta por ano; devolve o período efetivo."""
    ultimo = ultimo_mes_publicado(cliente)
    if fim is None or fim > ultimo:
        if fim is not None:
            log.info("%s ainda não foi publicado; indo até %s (última atualização da API).", fim, ultimo)
        fim = ultimo
    if inicio < config.PRIMEIRO_MES:
        log.info("A série mensal começa em %s; ajustando o início.", config.PRIMEIRO_MES)
        inicio = config.PRIMEIRO_MES
    if inicio > fim:
        log.info("Nada a fazer: %s é posterior ao último mês publicado (%s).", inicio, fim)
        return inicio, fim
    plano = planejar(inicio, fim, raw_dir, forcar)
    if not plano:
        log.info("Nada a baixar: %s a %s já está em %s.", inicio, fim, raw_dir)
        return inicio, fim
    ncms = carregar_ncms(ncm_csv)["ncm"].tolist()
    tabelas = carregar_tabelas_auxiliares(cliente)
    for ano, meses in sorted(plano.items()):
        de, ate = f"{ano}-{min(meses):02d}", f"{ano}-{max(meses):02d}"
        log.info("Consultando %s a %s (%d meses a baixar)...", de, ate, len(meses))
        dados = cliente.post(
            config.ENDPOINT_CONSULTA,
            montar_consulta(de, ate, ncms),
            params={"language": config.IDIOMA},
        )
        df = normalizar(_lista(dados), tabelas)
        df = df[(df["ano"] == ano) & df["mes"].isin(meses)]
        if df.empty:
            log.warning("A API não retornou linhas para %s a %s.", de, ate)
            continue
        arquivo = gravar_ano(df, ano, raw_dir)
        log.info("%s: +%d linhas (%d meses).", arquivo, len(df), df["mes"].nunique())
    return inicio, fim


def resumo(df: pd.DataFrame, inicio: str, fim: str, n: int = 5) -> str:
    """Linhas, totais e os maiores países de origem e URFs por kg líquido no período."""
    chave = df["ano"].astype(int) * 100 + df["mes"].astype(int)
    df = df[(chave >= int(inicio.replace("-", ""))) & (chave <= int(fim.replace("-", "")))]
    if df.empty:
        return f"Sem dados gravados para {inicio} a {fim}."
    total_kg = df["kg_liquido"].sum()
    meses = len(df[["ano", "mes"]].drop_duplicates())
    linhas = [
        f"Resumo {inicio} a {fim} (Fonte: Comex Stat/MDIC)",
        f"  Linhas: {_fmt(len(df))} | meses: {meses} | NCMs: {df['co_ncm'].nunique()}",
        f"  Total: {_fmt(total_kg / 1000)} t | US$ FOB {_fmt(df['vl_fob'].sum())}"
        f" | US$ CIF {_fmt(df['vl_cif'].sum())}",
        f"  Top {n} países de origem por kg líquido:",
    ]
    por_pais = df.groupby("no_pais")["kg_liquido"].sum().sort_values(ascending=False).head(n)
    for i, (nome, kg) in enumerate(por_pais.items(), 1):
        linhas.append(f"    {i}. {nome:<30} {_fmt(kg / 1000):>12} t  {_pct(kg, total_kg)}")
    linhas.append(f"  Top {n} URFs por kg líquido:")
    por_urf = (
        df.groupby(["co_urf", "no_urf"])["kg_liquido"].sum().sort_values(ascending=False).head(n)
    )
    for i, ((codigo, nome), kg) in enumerate(por_urf.items(), 1):
        linhas.append(f"    {i}. {codigo} {nome:<30} {_fmt(kg / 1000):>12} t  {_pct(kg, total_kg)}")
    return "\n".join(linhas)


def _fmt(valor: float) -> str:
    """Inteiro com separador de milhar brasileiro."""
    return f"{valor:,.0f}".replace(",", ".")


def _pct(parte: float, total: float) -> str:
    return f"{100 * parte / total:5.1f}%".replace(".", ",")


# --- CLI -----------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.ingest",
        description="Importações brasileiras de fertilizantes. Fonte: Comex Stat/MDIC.",
    )
    p.add_argument(
        "--inicio",
        type=mes_valido,
        metavar="AAAA-MM",
        help="primeiro mês (obrigatório, exceto com --atualizar-ncm)",
    )
    p.add_argument(
        "--fim",
        type=mes_valido,
        metavar="AAAA-MM",
        help="último mês (padrão: último mês publicado na API)",
    )
    p.add_argument(
        "--forcar",
        action="store_true",
        help="baixa de novo meses já gravados (ex.: revisões do MDIC)",
    )
    p.add_argument(
        "--atualizar-ncm",
        action="store_true",
        help="regenera data/ncm_fertilizantes.csv a partir de /tables/ncm",
    )
    p.add_argument(
        "--pausa",
        type=float,
        default=config.PAUSA_ENTRE_CHAMADAS_S,
        metavar="SEG",
        help="pausa mínima entre chamadas à API (padrão: %(default)s s)",
    )
    p.add_argument(
        "--ssl-inseguro",
        action="store_true",
        help="se a verificação TLS falhar, repete SEM verificação, só para o host da API",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.inicio and not args.atualizar_ncm:
        parser.error("informe --inicio AAAA-MM (ou use --atualizar-ncm)")
    if args.inicio and args.fim and args.inicio > args.fim:
        parser.error("--inicio deve ser anterior ou igual a --fim")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.ssl_inseguro:
        log.warning(
            "--ssl-inseguro: se a verificação TLS falhar, as chamadas a %s seguirão SEM verificação.",
            config.API_HOST,
        )
    try:
        cliente = ClienteComexStat(pausa=args.pausa, ssl_inseguro=args.ssl_inseguro)
        if args.atualizar_ncm or not config.NCM_CSV.exists():
            ncms = buscar_ncms_fertilizantes(cliente)
            salvar_ncms(ncms)
            log.info("%d NCMs gravados em %s.", len(ncms), config.NCM_CSV)
        if args.inicio:
            inicio, fim = ingerir(cliente, args.inicio, args.fim, forcar=args.forcar)
            print(resumo(ler_raw(), inicio, fim))
    except ErroAPI as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
