"""Testes básicos da ingestão. Nenhum teste acessa a rede: a API é simulada."""

import argparse
import json
import re
import ssl
from pathlib import Path

import pandas as pd
import pytest
import requests

from src import config, ingest

# --- Dublês -------------------------------------------------------------------------------


class RespostaFalsa:
    def __init__(self, status=200, corpo=None, headers=None):
        self.status_code = status
        self._corpo = corpo
        self.headers = headers or {}
        self.text = "" if corpo is None else json.dumps(corpo)

    def json(self):
        if self._corpo is None:
            raise ValueError("sem JSON")
        return self._corpo


class SessaoFalsa:
    """Devolve as respostas (ou levanta as exceções) na ordem dada e registra as chamadas."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.headers = {}

    def request(self, metodo, url, **kwargs):
        self.chamadas.append({"metodo": metodo, "url": url, **kwargs})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def ok(dados):
    return RespostaFalsa(200, {"data": dados, "success": True, "message": None})


def novo_cliente(*respostas, **kwargs):
    esperas = []
    cliente = ingest.ClienteComexStat(
        sessao=SessaoFalsa(*respostas), pausa=0, ca_extra="", dormir=esperas.append, **kwargs
    )
    return cliente, esperas


TABELAS = {
    "co_pais": {"Rússia": "676", "Canadá": "149"},
    "sg_uf": {"Paraná": "PR"},
    "co_via": {"MARITIMA": "01"},
}


def registro(ano, mes, pais="Canadá", kg=1000, urf="0917800 - PORTO DE PARANAGUA"):
    """Uma linha no formato devolvido por POST /general."""
    return {
        "coNcm": "31042090",
        "year": str(ano),
        "monthNumber": f"{mes:02d}",
        "country": pais,
        "ncm": "Outros cloretos de potássio",
        "state": "Paraná",
        "via": "MARITIMA",
        "urf": urf,
        "metricFOB": "300",
        "metricFreight": "20",
        "metricInsurance": "1",
        "metricCIF": "321",
        "metricKG": str(kg),
    }


def registro_soja(ano, mes, kg=1_000_000, fob=400_000):
    """Uma linha da exportação de soja (detalhe só por NCM) devolvida por POST /general."""
    return {
        "coNcm": config.NCM_SOJA,
        "year": str(ano),
        "monthNumber": f"{mes:02d}",
        "ncm": "Soja, mesmo triturada, exceto para semeadura",
        "metricFOB": str(fob),
        "metricKG": str(kg),
    }


class ApiFalsa:
    """Simula as rotas usadas por ingest.ingerir e ingest.ingerir_soja."""

    def __init__(self, ultimo="2025-04", kg=1000, sem_dados=()):
        self.ultimo = ultimo
        self.kg = kg
        self.sem_dados = set(sem_dados)  # (fluxo, "AAAA-MM") que a API "esquece" de devolver
        self.consultas = []
        self.corpos = []

    def get(self, caminho, params=None):
        ano, mes = self.ultimo.split("-")
        return {
            config.ENDPOINT_ULTIMA_ATUALIZACAO: {"year": ano, "monthNumber": mes},
            config.ENDPOINT_PAISES: {"list": [{"id": "149", "text": "Canadá"}], "count": 1},
            config.ENDPOINT_UFS: [{"id": "42", "text": "Paraná", "uf": "PR"}],
            config.ENDPOINT_VIAS: [{"id": "01", "text": "MARITIMA"}],
        }[caminho]

    def post(self, caminho, corpo, params=None):
        assert caminho == config.ENDPOINT_CONSULTA
        periodo = corpo["period"]
        self.consultas.append((periodo["from"], periodo["to"]))
        self.corpos.append(corpo)
        meses = [
            (a, m)
            for a, m in ingest.meses_do_intervalo(periodo["from"], periodo["to"])
            if (corpo["flow"], f"{a}-{m:02d}") not in self.sem_dados
        ]
        if corpo["flow"] == "export":
            return {"list": [registro_soja(a, m) for a, m in meses]}
        return {"list": [registro(a, m, kg=self.kg) for a, m in meses]}


@pytest.fixture
def ncm_csv(tmp_path):
    caminho = tmp_path / "ncm.csv"
    ingest.salvar_ncms(
        pd.DataFrame(
            {"ncm": ["31042090"], "descricao": ["KCl"], "grupo": ["K"], "sh4": ["3104"], "sh6": ["310420"]}
        ),
        caminho,
    )
    return caminho


# --- Cliente HTTP: rate limit, erros e SSL --------------------------------------------


def test_repete_em_429_esperando_o_tempo_pedido_pela_api():
    limite = RespostaFalsa(
        429,
        {
            "error": {
                "code": 429,
                "message": "Você excedeu o limite de solicitações. "
                "Por favor, tente novamente em 10 segundos.",
            }
        },
    )
    cliente, esperas = novo_cliente(limite, ok({"year": "2025", "monthNumber": "09"}))
    assert cliente.get("/general/dates/updated") == {"year": "2025", "monthNumber": "09"}
    assert len(cliente.sessao.chamadas) == 2
    assert esperas[0] >= 10


def test_backoff_exponencial_e_desiste_apos_max_tentativas():
    cliente, esperas = novo_cliente(*[RespostaFalsa(503) for _ in range(3)], max_tentativas=3)
    with pytest.raises(ingest.ErroAPI, match="3 tentativas"):
        cliente.get("/general")
    assert len(cliente.sessao.chamadas) == 3
    assert len(esperas) == 2 and esperas[1] > esperas[0] >= config.BACKOFF_BASE_S


def test_erro_400_nao_e_repetido():
    cliente, _ = novo_cliente(RespostaFalsa(400, {"error": {"code": 400, "message": "Filtro inválido"}}))
    with pytest.raises(ingest.ErroAPI, match="Filtro inválido"):
        cliente.post("/general", {})
    assert len(cliente.sessao.chamadas) == 1


def test_nao_segue_redirecionamento_nem_aceita_url_de_outro_host():
    cliente, _ = novo_cliente(RespostaFalsa(302, headers={"Location": "https://outro.exemplo/"}))
    with pytest.raises(ingest.ErroAPI, match="HTTP 302"):
        cliente.get("/general/dates/updated")
    assert cliente.sessao.chamadas[0]["allow_redirects"] is False
    with pytest.raises(ValueError):
        cliente.get("https://outro.exemplo/general")


def test_tls_falho_sem_flag_nao_desliga_a_verificacao():
    cliente, _ = novo_cliente(requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(ingest.ErroSSL, match=config.ENV_CA_EXTRA):
        cliente.get("/general/dates/updated")
    assert [c["verify"] for c in cliente.sessao.chamadas] == [True]


def test_ssl_inseguro_so_depois_da_falha_e_so_no_host_da_api():
    cliente, _ = novo_cliente(
        requests.exceptions.SSLError("certificate verify failed"),
        ok({"year": "2025", "monthNumber": "09"}),
        ok({"year": "2025", "monthNumber": "09"}),
        ssl_inseguro=True,
    )
    cliente.get("/general/dates/updated")
    cliente.get("/general/dates/updated")
    chamadas = cliente.sessao.chamadas
    assert [c["verify"] for c in chamadas] == [True, False, False]
    assert all(c["url"].startswith(config.API_BASE_URL + "/") for c in chamadas)
    assert all(c["allow_redirects"] is False for c in chamadas)


def test_ca_extra_em_der_e_somada_ao_bundle_do_ambiente(tmp_path, monkeypatch):
    bundle = Path(requests.certs.where()).read_text(encoding="utf-8")
    pem = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", bundle, re.S)[0]
    der = tmp_path / "cadeia.crt"
    der.write_bytes(ssl.PEM_cert_to_DER_cert(pem))
    base = tmp_path / "base.pem"
    base.write_text("# bundle do ambiente\n", encoding="ascii")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(base))

    cliente = ingest.ClienteComexStat(sessao=SessaoFalsa(), ca_extra=str(der))

    combinado = Path(cliente.verify).read_text(encoding="ascii")
    assert combinado.startswith("# bundle do ambiente")
    assert ssl.PEM_cert_to_DER_cert(combinado[combinado.index("-----BEGIN") :]) == der.read_bytes()


def test_ca_extra_inexistente_falha_com_erro_claro(tmp_path):
    with pytest.raises(ingest.ErroSSL, match="inexistente"):
        ingest.ClienteComexStat(sessao=SessaoFalsa(), ca_extra=str(tmp_path / "nao-existe.pem"))


# --- NCMs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ncm, grupo",
    [
        ("31021010", "N"),  # ureia
        ("31031100", "P"),  # superfosfato
        ("31042090", "K"),  # cloreto de potássio
        ("31052000", "NPK"),  # NPK
        ("31053010", "P"),  # DAP
        ("31054000", "P"),  # MAP
        ("31055900", "NPK"),  # outros NP
    ],
)
def test_grupo_do_ncm(ncm, grupo):
    assert ingest.grupo_do_ncm(ncm) == grupo


def test_buscar_ncms_filtra_a_posicao_e_classifica():
    class ClienteFalso:
        def get(self, caminho, params=None):
            assert caminho == config.ENDPOINT_NCM and params["add"] == "sh"
            sh4 = params["search"]
            itens = [
                {"coNcm": f"{sh4}9000", "noNCM": f" Outros  {sh4} ", "headingCode": sh4, "subHeadingCode": f"{sh4}90"},
                # a busca casa trechos do código: 61310220 contém "3102" mas é de outra posição
                {"coNcm": f"61{sh4}20", "noNCM": "Ternos", "headingCode": "6131", "subHeadingCode": "613102"},
            ]
            if sh4 == "3105":
                itens.append({"coNcm": "31054000", "noNCM": "MAP", "headingCode": "3105", "subHeadingCode": "310540"})
            return {"list": itens, "count": len(itens)}

    df = ingest.buscar_ncms_fertilizantes(ClienteFalso())

    assert list(df.columns) == ingest.COLUNAS_NCM
    assert df["ncm"].tolist() == ["31029000", "31039000", "31049000", "31054000", "31059000"]
    assert df.set_index("ncm")["grupo"].to_dict() == {
        "31029000": "N",
        "31039000": "P",
        "31049000": "K",
        "31054000": "P",
        "31059000": "NPK",
    }
    assert df.loc[0, "descricao"] == "Outros 3102"


def test_csv_de_ncms_versionado_e_consistente():
    df = ingest.carregar_ncms(config.NCM_CSV)
    assert list(df.columns) == ingest.COLUNAS_NCM
    assert df["ncm"].is_unique
    assert df["ncm"].str.fullmatch(r"310[2-5]\d{4}").all()
    assert (df["sh4"] == df["ncm"].str[:4]).all()
    assert (df["sh6"] == df["ncm"].str[:6]).all()
    assert (df["grupo"] == df["ncm"].map(ingest.grupo_do_ncm)).all()
    assert set(df["grupo"]) == {"N", "P", "K", "NPK"}
    assert (df["descricao"].str.len() > 0).all()


# --- Consulta e normalização ------------------------------------------------------------


def test_montar_consulta():
    corpo = ingest.montar_consulta("2025-01", "2025-03", ["31021010", "31042090"])
    assert corpo["flow"] == "import" and corpo["monthDetail"] is True
    assert corpo["period"] == {"from": "2025-01", "to": "2025-03"}
    assert corpo["filters"] == [{"filter": "ncm", "values": ["31021010", "31042090"]}]
    assert corpo["details"] == ["country", "ncm", "state", "via", "urf"]
    assert corpo["metrics"] == ["metricFOB", "metricKG", "metricCIF", "metricFreight", "metricInsurance"]


def test_normalizar_mapeia_codigos_e_tipa_metricas():
    df = ingest.normalizar([registro(2025, 1), registro(2025, 2, pais="Terra Incógnita")], TABELAS)
    assert list(df.columns) == ["ano", *ingest.COLUNAS_RAW]
    primeira = df.iloc[0]
    assert (primeira["ano"], primeira["mes"], primeira["co_ncm"]) == (2025, 1, "31042090")
    assert (primeira["co_pais"], primeira["sg_uf"], primeira["co_via"]) == ("149", "PR", "01")
    assert (primeira["co_urf"], primeira["no_urf"]) == ("0917800", "PORTO DE PARANAGUA")
    assert (primeira["vl_fob"], primeira["vl_cif"], primeira["kg_liquido"]) == (300, 321, 1000)
    assert str(df["kg_liquido"].dtype) == "Int64"
    # país fora da tabela auxiliar: mantém o nome e fica sem código
    assert pd.isna(df.iloc[1]["co_pais"]) and df.iloc[1]["no_pais"] == "Terra Incógnita"


def test_normalizar_recusa_valor_fracionario():
    with pytest.raises((TypeError, ValueError)):
        ingest.normalizar([{**registro(2025, 1), "metricFOB": "10.5"}], TABELAS)


# --- Incremental --------------------------------------------------------------------------


@pytest.mark.parametrize("texto", ["2025-1", "2025-13", "2025-00", "25-01", "2025/01"])
def test_mes_invalido(texto):
    with pytest.raises(argparse.ArgumentTypeError):
        ingest.mes_valido(texto)


def test_meses_do_intervalo_cruza_o_ano():
    assert ingest.meses_do_intervalo("2024-11", "2025-02") == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_ingestao_incremental_so_baixa_meses_faltantes(tmp_path, ncm_csv):
    raw = tmp_path / "raw"
    api = ApiFalsa(ultimo="2025-04")

    assert ingest.ingerir(api, "2024-11", "2025-02", raw_dir=raw, ncm_csv=ncm_csv) == ("2024-11", "2025-02")
    assert api.consultas == [("2024-11", "2024-12"), ("2025-01", "2025-02")]  # uma consulta por ano
    assert (raw / "ano=2024" / config.RAW_ARQUIVO).exists()

    # só os meses novos; o fim é limitado ao último mês publicado
    api.consultas.clear()
    assert ingest.ingerir(api, "2024-11", "2025-12", raw_dir=raw, ncm_csv=ncm_csv) == ("2024-11", "2025-04")
    assert api.consultas == [("2025-03", "2025-04")]

    # tudo já gravado: nenhuma consulta
    api.consultas.clear()
    ingest.ingerir(api, "2024-11", "2025-04", raw_dir=raw, ncm_csv=ncm_csv)
    assert api.consultas == []

    df = ingest.ler_raw(raw)
    assert list(df.columns) == ["ano", *ingest.COLUNAS_RAW]
    assert list(zip(df["ano"], df["mes"])) == [(2024, 11), (2024, 12), (2025, 1), (2025, 2), (2025, 3), (2025, 4)]


def test_lacuna_no_meio_nao_sobrescreve_mes_gravado(tmp_path, ncm_csv):
    raw = tmp_path / "raw"
    ingest.gravar_ano(ingest.normalizar([registro(2025, 2, kg=111)], TABELAS), 2025, raw)
    assert ingest.planejar("2025-01", "2025-03", raw) == {2025: [1, 3]}

    api = ApiFalsa(ultimo="2025-03", kg=999)
    ingest.ingerir(api, "2025-01", "2025-03", raw_dir=raw, ncm_csv=ncm_csv)

    assert api.consultas == [("2025-01", "2025-03")]
    kg = ingest.ler_raw(raw).set_index("mes")["kg_liquido"].to_dict()
    assert kg == {1: 999, 2: 111, 3: 999}


def test_forcar_substitui_os_meses_sem_duplicar(tmp_path, ncm_csv):
    raw = tmp_path / "raw"
    ingest.ingerir(ApiFalsa(ultimo="2025-02", kg=1000), "2025-01", "2025-02", raw_dir=raw, ncm_csv=ncm_csv)
    assert ingest.planejar("2025-01", "2025-02", raw, forcar=True) == {2025: [1, 2]}

    ingest.ingerir(ApiFalsa(ultimo="2025-02", kg=2000), "2025-02", "2025-02", raw_dir=raw, ncm_csv=ncm_csv, forcar=True)

    kg = ingest.ler_raw(raw).set_index("mes")["kg_liquido"].to_dict()
    assert kg == {1: 1000, 2: 2000}


# --- Exportação de soja (relação de troca) ----------------------------------------------


def test_consulta_da_soja_e_exportacao_so_com_fob_e_kg():
    corpo = ingest.montar_consulta(
        "2025-01", "2025-12", [config.NCM_SOJA], fluxo="export", detalhes=["ncm"], metricas=config.METRICAS_SOJA
    )
    assert corpo["flow"] == "export" and corpo["details"] == ["ncm"]
    assert corpo["metrics"] == ["metricFOB", "metricKG"]
    assert corpo["filters"] == [{"filter": "ncm", "values": ["12019000"]}]


def test_normalizar_soja():
    df = ingest.normalizar_soja([registro_soja(2025, 3, kg=14_657_780_797, fob=5_673_615_141)])
    assert list(df.columns) == ["ano", *ingest.COLUNAS_SOJA]
    assert df.loc[0, "mes"] == 3 and df.loc[0, "co_ncm"] == "12019000"
    assert df.loc[0, "kg_liquido"] == 14_657_780_797 and str(df["vl_fob"].dtype) == "Int64"


def test_soja_incremental_em_diretorio_proprio(tmp_path):
    raw_soja = tmp_path / "raw_soja"
    api = ApiFalsa(ultimo="2025-02")
    assert ingest.ingerir_soja(api, "2024-12", raw_dir=raw_soja) == ("2024-12", "2025-02")
    assert api.consultas == [("2024-12", "2024-12"), ("2025-01", "2025-02")]
    assert all(c["flow"] == "export" and c["details"] == ["ncm"] for c in api.corpos)
    assert (raw_soja / "ano=2025" / config.RAW_SOJA_ARQUIVO).exists()

    api.consultas.clear()
    ingest.ingerir_soja(api, "2024-12", raw_dir=raw_soja)
    assert api.consultas == []
    df = ingest.ler_soja(raw_soja)
    assert list(df.columns) == ["ano", *ingest.COLUNAS_SOJA]
    assert list(zip(df["ano"], df["mes"])) == [(2024, 12), (2025, 1), (2025, 2)]
    assert ingest.ultimo_mes_gravado(raw_soja, config.RAW_SOJA_ARQUIVO) == "2025-02"


def test_ultimo_mes_gravado_sem_dados(tmp_path):
    assert ingest.ultimo_mes_gravado(tmp_path / "vazio") is None


@pytest.fixture
def series(tmp_path, monkeypatch):
    """CLI apontada para diretórios temporários e para a API simulada."""
    dirs = {"fertilizantes": tmp_path / "raw", "soja": tmp_path / "raw_soja"}
    monkeypatch.setattr(
        ingest,
        "SERIES",
        {"fertilizantes": (dirs["fertilizantes"], config.RAW_ARQUIVO), "soja": (dirs["soja"], config.RAW_SOJA_ARQUIVO)},
    )
    return dirs


def test_cli_baixa_as_duas_series_com_o_mesmo_ultimo_mes(series, monkeypatch, capsys):
    api = ApiFalsa(ultimo="2025-02")
    monkeypatch.setattr(ingest, "ClienteComexStat", lambda **kwargs: api)
    assert ingest.main(["--inicio", "2025-01", "--pausa", "0"]) == 0
    assert [c["flow"] for c in api.corpos] == ["import", "export"]
    saida = capsys.readouterr().out
    assert "Resumo 2025-01 a 2025-02" in saida and "soja em grão" in saida

    assert ingest.main(["--ultimo-gravado"]) == 0
    assert capsys.readouterr().out.strip() == "2025-02"


def test_cli_ultimo_gravado_e_o_menor_entre_as_series(series, capsys):
    ingest.gravar_ano(ingest.normalizar([registro(2025, 3)], TABELAS), 2025, series["fertilizantes"])
    ingest.gravar_ano(
        ingest.normalizar_soja([registro_soja(2025, 2)]),
        2025,
        series["soja"],
        config.RAW_SOJA_ARQUIVO,
        ingest.COLUNAS_SOJA,
        ingest.ORDEM_SOJA,
    )
    assert ingest.main(["--ultimo-gravado"]) == 0
    assert capsys.readouterr().out.strip() == "2025-02"
    assert ingest.main(["--ultimo-gravado", "--serie", "fertilizantes"]) == 0
    assert capsys.readouterr().out.strip() == "2025-03"


def test_cli_falha_se_o_ultimo_mes_publicado_nao_veio(series, monkeypatch):
    api = ApiFalsa(ultimo="2025-02", sem_dados={("export", "2025-02")})
    monkeypatch.setattr(ingest, "ClienteComexStat", lambda **kwargs: api)
    assert ingest.main(["--inicio", "2025-01", "--pausa", "0"]) == 1
    # a soja de janeiro foi gravada, mas o mês publicado (fevereiro) não: o código de saída é 1
    assert ingest.ultimo_mes_gravado(series["soja"], config.RAW_SOJA_ARQUIVO) == "2025-01"


# --- Resumo e CLI -------------------------------------------------------------------------


def test_resumo_lista_top_paises_e_urfs():
    df = ingest.normalizar(
        [
            registro(2025, 1, pais="Rússia", kg=3000),
            registro(2025, 1, kg=1000, urf="0927700 - PORTO DE SAO FRANCISCO DO SUL"),
            registro(2025, 4, kg=5000),  # fora do período
        ],
        TABELAS,
    )
    texto = ingest.resumo(df, "2025-01", "2025-03")
    assert "Linhas: 2" in texto
    assert texto.index("Rússia") < texto.index("Canadá")
    assert "75,0%" in texto and "PORTO DE SAO FRANCISCO DO SUL" in texto


@pytest.mark.parametrize("argv", [[], ["--inicio", "2025-03", "--fim", "2025-01"], ["--inicio", "2025-13"]])
def test_cli_valida_argumentos_antes_de_chamar_a_api(argv):
    with pytest.raises(SystemExit):
        ingest.main(argv)
