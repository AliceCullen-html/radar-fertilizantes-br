"""Testes da ingestão da fase 6 (src/benchmark.py): Pink Sheet do Banco Mundial e PTAX do BCB.

Nenhum teste acessa a rede: as respostas são simuladas, e o xlsx é montado no próprio teste
com o layout do arquivo real (CMO-Historical-Data-Monthly.xlsx, edição de outubro de 2026).
"""

import io
import ssl
import json

import openpyxl
import pandas as pd
import pytest
import requests

from src import benchmark, config, ingest

URL_XLSX = "https://thedocs.worldbank.org/en/doc/abc123-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
PAGINA = f"""<html><body>
<a href="https://thedocs.worldbank.org/en/doc/abc123-0050012026/related/CMO-Pink-Sheet-October-2026.pdf">Pink Sheet</a>
<a href="{URL_XLSX}">Monthly prices</a>
<a href="https://thedocs.worldbank.org/en/doc/abc123-0050012026/related/CMO-Historical-Data-Annual.xlsx">Annual</a>
<a href="https://datacatalog.worldbank.org/public-licenses#cc-by">Data Access and Licensing</a>
</body></html>"""
PAGINA_FIREWALL = (
    b'<?xml version="1.0" encoding="pt-br"?>\n<!DOCTYPE html>\n<html><head><title>Requisi\xc3\xa7\xc3\xa3o inv\xc3\xa1lida!</title>'
    b"</head><body>A sua requisi\xc3\xa7\xc3\xa3o foi rejeitada.</body></html>"
)


# --- Dublês -------------------------------------------------------------------------------


class Resposta:
    def __init__(self, status=200, conteudo=b"", headers=None):
        self.status_code = status
        self.content = conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8")
        self.headers = headers or {}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class Sessao:
    """Devolve as respostas na ordem dada (ou levanta as exceções) e registra as chamadas."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.headers = {}

    def request(self, metodo, url, params=None, headers=None, **kwargs):
        self.chamadas.append({"metodo": metodo, "url": url, "params": params, "headers": headers or {}, **kwargs})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def cliente(*respostas):
    esperas = []
    c = benchmark.ClienteFontes(sessao=Sessao(*respostas), pausa=0, dormir=esperas.append)
    return c, esperas


def meses(inicio, fim):
    return benchmark._meses(inicio, fim)


def preco(serie, mes, base=0.0):
    """Valor sintético e determinístico por série e mês."""
    i = meses("2014-01", mes).__len__()
    return {"rocha_fosfatica": 100, "dap": 400, "tsp": 350, "ureia": 300, "kcl": 280}[serie] + i + base


def xlsx_pink(ate="2026-09", desde="2014-11", vazios=(), base=0.0, nomes=None, unidades=None, aba="Monthly Prices", atualizado="October 02, 2026"):
    """Monta o xlsx no layout do arquivo real. `vazios`: {(serie, mes)} com '..' ou '…'."""
    livro = openpyxl.Workbook()
    folha = livro.active
    folha.title = aba
    livro.create_sheet("Description")
    nomes = nomes or ["Crude oil, average", "Phosphate rock", "DAP", "TSP", "Urea ", "Potassium chloride **"]
    unidades = unidades or ["($/bbl)", "($/mt)", "($/mt)", "($/mt)", "($/mt)", "($/mt)"]
    folha.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    folha.append(["monthly prices in nominal US dollars, 1960 to present"])
    folha.append(["(monthly series are available only in nominal US dollars)"])
    folha.append([f"Updated on {atualizado}"])
    folha.append([None, *nomes])
    folha.append([None, *unidades])
    series = ["rocha_fosfatica", "dap", "tsp", "ureia", "kcl"]
    for n, mes in enumerate(meses(desde, ate)):
        valores = []
        for s in series:
            if (s, mes) in vazios:
                valores.append(".." if n % 2 else "…")
            else:
                valores.append(preco(s, mes, base))
        folha.append([f"{mes[:4]}M{mes[5:]}", 70.5, *valores])
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def sgs(*pares):
    """Resposta do SGS: [(AAAA-MM, valor)] -> JSON do BCB."""
    return json.dumps([{"data": f"01/{m[5:]}/{m[:4]}", "valor": f"{v:.4f}"} for m, v in pares])


# --- Cliente --------------------------------------------------------------------------


def test_cliente_repete_quando_o_firewall_devolve_html_com_200():
    c, esperas = cliente(Resposta(200, PAGINA_FIREWALL), Resposta(200, sgs(("2026-09", 5.1447))))
    assert c.get("https://api.bcb.gov.br/dados/x", esperado="json").json()[0]["valor"] == "5.1447"
    assert len(esperas) == 1
    chamada = c.sessao.chamadas[0]
    assert chamada["headers"]["Accept"] == "application/json"  # sem ele o firewall do BCB recusa
    assert chamada["verify"] is True and chamada["allow_redirects"] is False and chamada["timeout"] == config.TIMEOUT_S


def test_cliente_respeita_retry_after_e_desiste_com_erro_claro():
    c, esperas = cliente(Resposta(429, b"", {"Retry-After": "30"}), *[Resposta(503)] * (config.MAX_TENTATIVAS - 1))
    with pytest.raises(benchmark.ErroFonte, match="falhou após"):
        c.get("https://api.bcb.gov.br/dados/x", esperado="json")
    assert esperas[0] >= 30 and len(esperas) == config.MAX_TENTATIVAS - 1


def test_cliente_nao_repete_404_nem_sai_dos_hosts_permitidos():
    c, _ = cliente(Resposta(404))
    with pytest.raises(benchmark.ErroFonte, match="HTTP 404"):
        c.get("https://api.bcb.gov.br/dados/x", esperado="json")
    c, _ = cliente()
    for url in ["http://api.bcb.gov.br/dados/x", "https://exemplo.com/CMO-Historical-Data-Monthly.xlsx"]:
        with pytest.raises(benchmark.ErroFonte, match="fora das fontes"):
            c.get(url, esperado="json")
    assert c.sessao.chamadas == []


def test_cliente_segue_redirecionamento_so_dentro_dos_hosts_permitidos():
    c, _ = cliente(Resposta(301, b"", {"Location": "https://www.worldbank.org/en/research/commodity-markets/"}), Resposta(200, PAGINA))
    assert "Monthly prices" in c.get(config.PINK_PAGINA, esperado="html").text
    c, _ = cliente(Resposta(302, b"", {"Location": "https://outro.exemplo/arquivo.xlsx"}))
    with pytest.raises(benchmark.ErroFonte, match="fora das fontes"):
        c.get(URL_XLSX, esperado="xlsx")
    assert len(c.sessao.chamadas) == 1


def test_cliente_recusa_xlsx_que_nao_e_zip_e_arquivo_grande(monkeypatch):
    c, _ = cliente(*[Resposta(200, b"<html>erro</html>")] * config.MAX_TENTATIVAS)
    with pytest.raises(benchmark.ErroFonte, match="não veio como xlsx"):
        c.get(URL_XLSX, esperado="xlsx")
    monkeypatch.setattr(config, "PINK_MAX_BYTES", 10)
    c, _ = cliente(Resposta(200, b"PK\x03\x04" + b"0" * 20))
    with pytest.raises(benchmark.ErroFonte, match="acima do limite"):
        c.get(URL_XLSX, esperado="xlsx")


def test_cliente_falha_de_rede_repete():
    c, esperas = cliente(requests.ConnectionError("sem rota"), Resposta(200, PAGINA))
    assert c.get(config.PINK_PAGINA, esperado="html").status_code == 200
    assert len(esperas) == 1


# --- Pink Sheet -------------------------------------------------------------------------


def test_acha_o_link_do_xlsx_mensal_na_pagina():
    assert benchmark.url_do_arquivo_pink(PAGINA) == URL_XLSX


@pytest.mark.parametrize(
    "html",
    [
        "<html><a href='https://thedocs.worldbank.org/x/CMO-Historical-Data-Annual.xlsx'>a</a></html>",
        f"<html><a href='{URL_XLSX}'>a</a><a href='{URL_XLSX.replace('abc123', 'def456')}'>b</a></html>",
        "<html><a href='https://exemplo.com/x/CMO-Historical-Data-Monthly.xlsx'>a</a></html>",
        "<html><a href='http://thedocs.worldbank.org/x/CMO-Historical-Data-Monthly.xlsx'>a</a></html>",
    ],
)
def test_link_ausente_duplicado_ou_de_outro_host_e_erro(html):
    with pytest.raises(benchmark.ErroFonte, match="layout da página"):
        benchmark.url_do_arquivo_pink(html)


def test_le_o_xlsx_no_layout_real():
    df, atualizado = benchmark.ler_xlsx_pink(xlsx_pink(vazios={("rocha_fosfatica", "2015-03"), ("kcl", "2026-09")}))
    assert atualizado == "2026-10-02"
    assert list(df.columns) == ["mes", "ureia", "dap", "tsp", "kcl", "rocha_fosfatica"]
    assert df["mes"].iloc[0] == "2014-11" and df["mes"].iloc[-1] == "2026-09"
    linha = df.set_index("mes").loc["2026-08"]
    assert linha["ureia"] == preco("ureia", "2026-08") and linha["kcl"] == preco("kcl", "2026-08")
    assert pd.isna(df.set_index("mes").loc["2015-03", "rocha_fosfatica"])
    # "Urea " com espaço e "Potassium chloride **" com asteriscos são reconhecidos
    assert benchmark.ultimo_mes_completo(df) == "2026-08"  # KCl de 2026-09 está vazio


@pytest.mark.parametrize(
    "kwargs, trecho",
    [
        ({"nomes": ["Crude oil, average", "Phosphate rock", "DAP", "TSP", "Urea", "Potash"]}, "não tem as colunas"),
        ({"unidades": ["($/bbl)", "($/mt)", "($/mt)", "($/mt)", "($/kg)", "($/mt)"]}, "unidade"),
        ({"aba": "Prices"}, "não tem a aba"),
    ],
)
def test_layout_mudado_e_erro_claro(kwargs, trecho):
    with pytest.raises(benchmark.ErroFonte, match=trecho):
        benchmark.ler_xlsx_pink(xlsx_pink(**kwargs))


def test_arquivo_corrompido_e_erro_claro():
    with pytest.raises(benchmark.ErroFonte, match="não foi possível abrir"):
        benchmark.ler_xlsx_pink(b"PK\x03\x04 quebrado")


def _pink(raw_dir, arquivo, fim=None, forcar=False, inicio="2015-01"):
    c, _ = cliente(Resposta(200, PAGINA), Resposta(200, arquivo))
    return benchmark.ingerir_pink(c, inicio, fim, raw_dir=raw_dir, forcar=forcar), c


def test_ingestao_grava_csv_desde_2015_e_a_fonte(tmp_path):
    (inicio, fim, mudancas), c = _pink(tmp_path, xlsx_pink())
    assert (inicio, fim, mudancas) == ("2015-01", "2026-09", [])
    assert [ch["url"] for ch in c.sessao.chamadas] == [config.PINK_PAGINA, URL_XLSX]
    df = benchmark.ler_pink(tmp_path)
    assert df["mes"].iloc[0] == "2015-01" and len(df) == len(meses("2015-01", "2026-09"))
    fonte = json.loads((tmp_path / config.PINK_FONTE).read_text(encoding="utf-8"))
    assert fonte["url"] == URL_XLSX and fonte["licenca"] == "CC BY 4.0" and fonte["atualizado_em"] == "2026-10-02"
    assert len(fonte["sha256"]) == 64
    assert benchmark.ultimo_pink_gravado(tmp_path) == "2026-09"
    # mesma entrada, mesmos bytes
    antes = (tmp_path / config.PINK_ARQUIVO).read_bytes()
    _pink(tmp_path, xlsx_pink(), forcar=True)
    assert (tmp_path / config.PINK_ARQUIVO).read_bytes() == antes


def test_mes_ainda_nao_publicado_vai_ate_o_ultimo_completo(tmp_path, caplog):
    caplog.set_level("INFO")
    (_, fim, _), _ = _pink(tmp_path, xlsx_pink(vazios={("ureia", "2026-09")}), fim="2026-10")
    assert fim == "2026-08" and "ainda não foi publicado" in caplog.text
    assert benchmark.ultimo_pink_gravado(tmp_path) == "2026-08"


def test_rocha_fosfatica_vazia_nao_trava_a_serie(tmp_path):
    (_, fim, _), _ = _pink(tmp_path, xlsx_pink(vazios={("rocha_fosfatica", "2026-09"), ("rocha_fosfatica", "2020-05")}))
    assert fim == "2026-09"
    df = benchmark.ler_pink(tmp_path).set_index("mes")
    assert pd.isna(df.loc["2020-05", "rocha_fosfatica"]) and df.loc["2020-05", "kcl"] == preco("kcl", "2020-05")


def test_lacuna_no_meio_falha_sem_gravar(tmp_path):
    with pytest.raises(benchmark.ErroFonte, match="lacuna.*dap sem valor em 2021-04"):
        _pink(tmp_path, xlsx_pink(vazios={("dap", "2021-04")}))
    assert not (tmp_path / config.PINK_ARQUIVO).exists()


def test_revisao_do_banco_mundial_e_listada_e_gravada(tmp_path, caplog):
    _pink(tmp_path, xlsx_pink())
    (_, _, mudancas), _ = _pink(tmp_path, xlsx_pink(base=0.5), forcar=True)
    assert len(mudancas) == 5 * len(meses("2015-01", "2026-09"))
    mes, serie, antes, depois = mudancas[0]
    assert depois == antes + 0.5 and "revisou" in caplog.text
    assert benchmark.ler_pink(tmp_path).set_index("mes").loc["2015-01", "ureia"] == preco("ureia", "2015-01", 0.5)


def test_arquivo_que_termina_antes_do_gravado_e_recusado(tmp_path):
    _pink(tmp_path, xlsx_pink(ate="2026-09"))
    antes = (tmp_path / config.PINK_ARQUIVO).read_bytes()
    with pytest.raises(benchmark.ErroFonte, match="antes do que já está gravado"):
        _pink(tmp_path, xlsx_pink(ate="2026-07"), forcar=True)
    assert (tmp_path / config.PINK_ARQUIVO).read_bytes() == antes


def test_sem_forcar_nao_baixa_mes_ja_gravado(tmp_path):
    _pink(tmp_path, xlsx_pink())
    c, _ = cliente()
    assert benchmark.ingerir_pink(c, "2015-01", "2026-09", raw_dir=tmp_path) == ("2015-01", "2026-09", [])
    assert c.sessao.chamadas == []


# --- PTAX (SGS 3698) ----------------------------------------------------------------------


def ptax(mes):
    return 3.0 + meses("2015-01", mes).__len__() / 100


def test_normaliza_o_json_do_sgs():
    df = benchmark.normalizar_ptax(json.loads(sgs(("2026-08", 5.1532), ("2026-09", 5.1447))))
    assert df.to_dict("records") == [{"mes": "2026-08", "brl_por_usd": 5.1532}, {"mes": "2026-09", "brl_por_usd": 5.1447}]


@pytest.mark.parametrize(
    "registro, trecho",
    [
        ({"data": "2026-09-01", "valor": "5.1"}, "data inesperada"),
        ({"data": "01/09/2026", "valor": "abc"}, "valor inesperado"),
        ({"data": "01/09/2026", "valor": "5144.7"}, "fora do intervalo"),
    ],
)
def test_sgs_com_formato_inesperado_e_erro(registro, trecho):
    with pytest.raises(benchmark.ErroFonte, match=trecho):
        benchmark.normalizar_ptax([registro])


def test_carga_completa_em_janelas_de_cinco_anos(tmp_path):
    todos = meses("2015-01", "2026-09")
    janelas = [todos[:60], todos[60:120], todos[120:]]
    c, _ = cliente(Resposta(200, sgs(("2026-09", ptax("2026-09")))), *[Resposta(200, sgs(*[(m, ptax(m)) for m in j])) for j in janelas])
    assert benchmark.ingerir_ptax(c, "2015-01", raw_dir=tmp_path) == ("2015-01", "2026-09")
    params = [ch["params"] for ch in c.sessao.chamadas]
    assert params[0] == {"formato": "json"} and c.sessao.chamadas[0]["url"].endswith("/bcdata.sgs.3698/dados/ultimos/1")
    assert [(p["dataInicial"], p["dataFinal"]) for p in params[1:]] == [
        ("01/01/2015", "31/12/2019"),
        ("01/01/2020", "31/12/2024"),
        ("01/01/2025", "30/09/2026"),
    ]
    df = benchmark.ler_ptax(tmp_path)
    assert len(df) == len(todos) and benchmark.ultimo_ptax_gravado(tmp_path) == "2026-09"


def test_incremental_so_pede_o_que_falta(tmp_path):
    benchmark._gravar_csv(pd.DataFrame({"mes": meses("2015-01", "2026-08"), "brl_por_usd": [ptax(m) for m in meses("2015-01", "2026-08")]}), tmp_path / config.PTAX_ARQUIVO)
    c, _ = cliente(Resposta(200, sgs(("2026-09", 5.1447))), Resposta(200, sgs(("2026-09", 5.1447))))
    benchmark.ingerir_ptax(c, "2015-01", raw_dir=tmp_path)
    assert (c.sessao.chamadas[1]["params"]["dataInicial"], c.sessao.chamadas[1]["params"]["dataFinal"]) == ("01/09/2026", "30/09/2026")
    assert benchmark.ler_ptax(tmp_path).iloc[-1].to_dict() == {"mes": "2026-09", "brl_por_usd": 5.1447}
    # já atualizado: nem consulta o último publicado
    c, _ = cliente()
    benchmark.ingerir_ptax(c, "2015-01", "2026-09", raw_dir=tmp_path)
    assert c.sessao.chamadas == []


def test_mes_publicado_que_nao_vem_falha_sem_gravar(tmp_path):
    c, _ = cliente(Resposta(200, sgs(("2026-09", 5.1447))), Resposta(200, sgs(("2026-07", 5.1139), ("2026-09", 5.1447))))
    with pytest.raises(benchmark.ErroFonte, match="já publicou até 2026-09, mas não devolveu 2026-08"):
        benchmark.ingerir_ptax(c, "2026-07", raw_dir=tmp_path)
    assert not (tmp_path / config.PTAX_ARQUIVO).exists()


def test_fim_alem_do_publicado_vai_ate_o_ultimo(tmp_path, caplog):
    caplog.set_level("INFO")
    c, _ = cliente(Resposta(200, sgs(("2026-09", 5.1447))), Resposta(200, sgs(("2026-09", 5.1447))))
    assert benchmark.ingerir_ptax(c, "2026-09", "2026-10", raw_dir=tmp_path) == ("2026-09", "2026-09")
    assert "2026-10 ainda não foi publicado" in caplog.text


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def quatro_series(tmp_path, monkeypatch):
    dirs = {s: tmp_path / s for s in ["raw", "raw_soja", "raw_benchmark", "raw_cambio"]}
    monkeypatch.setattr(
        ingest,
        "SERIES",
        {
            "fertilizantes": (dirs["raw"], config.RAW_ARQUIVO),
            "soja": (dirs["raw_soja"], config.RAW_SOJA_ARQUIVO),
            "benchmark": (dirs["raw_benchmark"], config.PINK_ARQUIVO),
            "cambio": (dirs["raw_cambio"], config.PTAX_ARQUIVO),
        },
    )
    return dirs


def _gravar_comex(dirs, ate):
    from tests.test_ingest import TABELAS, registro, registro_soja

    ingest.gravar_ano(ingest.normalizar([registro(2026, int(ate[5:]))], TABELAS), 2026, dirs["raw"])
    ingest.gravar_ano(
        ingest.normalizar_soja([registro_soja(2026, int(ate[5:]))]), 2026, dirs["raw_soja"],
        config.RAW_SOJA_ARQUIVO, ingest.COLUNAS_SOJA, ingest.ORDEM_SOJA,
    )


def test_ultimo_gravado_e_o_menor_entre_as_quatro_series(quatro_series, capsys):
    _gravar_comex(quatro_series, "2026-09")
    assert ingest.main(["--ultimo-gravado"]) == 0
    assert capsys.readouterr().out.strip() == ""  # benchmark e câmbio ainda vazios
    _pink(quatro_series["raw_benchmark"], xlsx_pink(ate="2026-09", vazios={("kcl", "2026-09")}))
    benchmark._gravar_csv(pd.DataFrame({"mes": ["2026-09"], "brl_por_usd": [5.1447]}), quatro_series["raw_cambio"] / config.PTAX_ARQUIVO)
    assert ingest.main(["--ultimo-gravado"]) == 0
    assert capsys.readouterr().out.strip() == "2026-08"  # a Pink Sheet ainda não tem o KCl de setembro
    assert ingest.main(["--ultimo-gravado", "--por-serie"]) == 0
    assert capsys.readouterr().out.split("\n")[:4] == ["fertilizantes 2026-09", "soja 2026-09", "benchmark 2026-08", "cambio 2026-09"]


def test_cli_baixa_benchmark_e_cambio_sem_chamar_o_comex(quatro_series, monkeypatch, capsys):
    respostas = [Resposta(200, PAGINA), Resposta(200, xlsx_pink()), Resposta(200, sgs(("2026-09", 5.1447))), Resposta(200, sgs(("2026-09", 5.1447)))]
    c, _ = cliente(*respostas)
    monkeypatch.setattr(benchmark, "ClienteFontes", lambda: c)
    monkeypatch.setattr(ingest, "ultimo_mes_publicado", lambda cliente: pytest.fail("não deveria chamar o Comex Stat"))
    assert ingest.main(["--inicio", "2026-09", "--serie", "benchmark"]) == 0
    assert ingest.main(["--inicio", "2026-09", "--serie", "cambio"]) == 0
    saida = capsys.readouterr().out
    assert "Resumo Pink Sheet (Banco Mundial, CC BY 4.0) 2026-09 a 2026-09" in saida and "Resumo PTAX" in saida


def test_cli_falha_com_codigo_1_se_a_fonte_falhar(quatro_series, monkeypatch):
    c, _ = cliente(Resposta(200, "<html>nada aqui</html>"))
    monkeypatch.setattr(benchmark, "ClienteFontes", lambda: c)
    assert ingest.main(["--inicio", "2026-09", "--serie", "benchmark"]) == 1
    assert not (quatro_series["raw_benchmark"] / config.PINK_ARQUIVO).exists()


# --- Revisão adversarial da fase 6 -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://outro.exemplo\\@api.bcb.gov.br/dados",  # urlsplit lê o BCB; o requests vai a outro.exemplo
        "https://usuario@api.bcb.gov.br/dados",
        "https://api.bcb.gov.br\\x/dados",
        "https://[x]/dados",
        "http://api.bcb.gov.br/dados",
    ],
)
def test_cliente_recusa_endereco_que_engana_a_lista_de_hosts(url):
    with pytest.raises(benchmark.ErroFonte):
        benchmark._conferir_host(url)
    c, _ = cliente(Resposta(302, b"", {"Location": url}))
    with pytest.raises(benchmark.ErroFonte):
        c.get("https://api.bcb.gov.br/dados", esperado="json")
    assert len(c.sessao.chamadas) == 1  # não chega a pedir o endereço do redirecionamento


def test_link_com_barra_invertida_na_pagina_nao_conta():
    html = f'<a href="https://outro.exemplo\\@thedocs.worldbank.org/x/{config.PINK_NOME_ARQUIVO}">x</a>'
    with pytest.raises(benchmark.ErroFonte, match="tem 0"):
        benchmark.url_do_arquivo_pink(html)


def test_cliente_repete_corte_no_meio_do_corpo_e_tls_transitorio_mas_nao_certificado():
    c, esperas = cliente(requests.exceptions.ChunkedEncodingError("IncompleteRead"), requests.exceptions.SSLError(ssl.SSLEOFError(8, "EOF")), Resposta(200, PAGINA))
    assert c.get(config.PINK_PAGINA, esperado="html").status_code == 200 and len(esperas) == 2
    c, _ = cliente(requests.exceptions.SSLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED]")))
    with pytest.raises(benchmark.ErroFonte, match="verificação TLS"):
        c.get(config.PINK_PAGINA, esperado="html")


def test_retry_after_tem_teto():
    c, esperas = cliente(Resposta(429, b"", {"Retry-After": "3600"}), Resposta(200, sgs(("2026-09", 5.1))))
    c.get("https://api.bcb.gov.br/dados/x", esperado="json")
    assert esperas[0] <= config.BACKOFF_MAX_S + 1


class RespostaEmBlocos(Resposta):
    """Como a do requests com stream=True: o corpo só existe depois de lido em blocos."""

    def __init__(self, blocos, headers=None):
        super().__init__(200, b"", headers)
        self.blocos, self.lidos, self._content_consumed = blocos, 0, False

    @property
    def content(self):
        return self._content

    @content.setter
    def content(self, valor):
        self._content = valor

    def iter_content(self, chunk_size=1):
        for bloco in self.blocos:
            self.lidos += 1
            yield bloco

    def close(self):
        pass


def test_download_para_no_limite_sem_ler_o_resto(monkeypatch):
    monkeypatch.setattr(config, "MAX_BYTES_PAGINA", 1000)
    grande = RespostaEmBlocos([b"<html>" + b"x" * 600] * 50)
    c, _ = cliente(grande)
    with pytest.raises(benchmark.ErroFonte, match="passou de 1000 bytes"):
        c.get(config.PINK_PAGINA, esperado="html")
    assert grande.lidos == 2  # parou no segundo bloco
    c, _ = cliente(Resposta(200, b"<html>", {"Content-Length": "999999"}))
    with pytest.raises(benchmark.ErroFonte, match="acima do limite"):
        c.get(config.PINK_PAGINA, esperado="html")
    c, _ = cliente(RespostaEmBlocos([b"<html><body>", b"ok</body></html>"]))
    assert c.get(config.PINK_PAGINA, esperado="html").text == "<html><body>ok</body></html>"


def test_xlsx_que_descompacta_demais_e_recusado(monkeypatch):
    monkeypatch.setattr(config, "PINK_MAX_DESCOMPACTADO", 1000)
    with pytest.raises(benchmark.ErroFonte, match="descompacta"):
        benchmark.ler_xlsx_pink(xlsx_pink())


def _editar(arquivo, editar):
    livro = openpyxl.load_workbook(io.BytesIO(arquivo))
    editar(livro["Monthly Prices"])
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def test_layout_tolerante_no_que_nao_e_comparado(caplog):
    # nota no rodapé da aba, número gravado como texto e data em formato inesperado
    arquivo = _editar(xlsx_pink(atualizado="Sept 02, 2026"), lambda f: (f.append(["Note: prices are monthly averages."]), f.cell(row=8, column=3, value="170")))
    df, atualizado = benchmark.ler_xlsx_pink(arquivo)
    assert df["mes"].iloc[-1] == "2026-09" and atualizado is None and df["rocha_fosfatica"].iloc[1] == 170.0
    # período estranho no meio continua sendo erro
    no_meio = _editar(xlsx_pink(), lambda f: (f.append(["Note"]), f.append(["2026M10", 70.5, 1, 2, 3, 4, 5])))
    with pytest.raises(benchmark.ErroFonte, match="período inesperado"):
        benchmark.ler_xlsx_pink(no_meio)
    # sem a coluna da rocha fosfática (só ingerida): aviso, e as séries comparadas seguem
    caplog.set_level("WARNING")
    sem_rocha = xlsx_pink(nomes=["Crude oil, average", "Outra coisa", "DAP", "TSP", "Urea ", "Potassium chloride **"])
    df, _ = benchmark.ler_xlsx_pink(sem_rocha)
    assert df["rocha_fosfatica"].isna().all() and df["kcl"].notna().all() and "Phosphate rock" in caplog.text


def test_fim_anterior_ao_gravado_nao_apaga_a_pink(tmp_path):
    _pink(tmp_path, xlsx_pink())
    (inicio, fim, _), _c = _pink(tmp_path, xlsx_pink(), fim="2020-12", forcar=True)
    assert fim == "2026-09" and benchmark.ultimo_pink_gravado(tmp_path) == "2026-09"


def test_edicao_mais_antiga_que_a_gravada_e_recusada(tmp_path):
    _pink(tmp_path, xlsx_pink(atualizado="November 03, 2026"))
    antes = (tmp_path / config.PINK_ARQUIVO).read_bytes()
    with pytest.raises(benchmark.ErroFonte, match="anterior à gravada"):
        _pink(tmp_path, xlsx_pink(atualizado="October 02, 2026"), forcar=True)
    assert (tmp_path / config.PINK_ARQUIVO).read_bytes() == antes


def test_ptax_consulta_trechos_seguidos(tmp_path):
    gravados = meses("2016-01", "2026-08")
    benchmark._gravar_csv(pd.DataFrame({"mes": gravados, "brl_por_usd": [ptax(m) for m in gravados]}), tmp_path / config.PTAX_ARQUIVO)
    c, _ = cliente(
        Resposta(200, sgs(("2026-09", ptax("2026-09")))),
        Resposta(200, sgs(*[(m, ptax(m)) for m in meses("2015-01", "2015-12")])),
        Resposta(200, sgs(("2026-09", ptax("2026-09")))),
    )
    benchmark.ingerir_ptax(c, "2015-01", raw_dir=tmp_path)
    janelas = [(ch["params"]["dataInicial"], ch["params"]["dataFinal"]) for ch in c.sessao.chamadas[1:]]
    assert janelas == [("01/01/2015", "31/12/2015"), ("01/09/2026", "30/09/2026")]
    assert len(benchmark.ler_ptax(tmp_path)) == len(meses("2015-01", "2026-09"))
