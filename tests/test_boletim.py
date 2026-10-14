"""Testes do boletim com IA (src/boletim.py). Nenhum teste usa rede nem chave."""

import json
import subprocess
import sys

import pandas as pd
import pytest

from src import boletim, config, llm, transform

VARIAVEIS_LLM = ["RADAR_LLM_PROVIDER", "RADAR_LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OLLAMA_HOST"]


@pytest.fixture(autouse=True)
def sem_variaveis_de_llm(monkeypatch):
    """Os testes nunca dependem de chave ou provedor configurado na máquina."""
    for nome in VARIAVEIS_LLM:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture(scope="module")
def fatos():
    painel, troca = boletim.carregar(config.PROCESSED_DIR)
    return boletim.montar_fatos(painel, troca)


def resposta(titulo="Importações em {mes_referencia}", lead=None, paragrafos=None):
    lead = lead or "O Brasil importou {t_total_mes} em {mes_referencia}, {var_t_total_mes_sentido} de {var_t_total_mes}."
    paragrafos = paragrafos or [
        "No acumulado de {periodo_ytd}, as compras somaram {t_total_ytd}.",
        "O frete médio do ano ficou em {frete_medio_ytd}.",
        "A participação da Rússia nos potássicos foi de {share_russia_k_ano}.",
        "No porto, uma tonelada de MAP valeu {troca_map_ultimo}; não é o preço ao produtor.",
    ]
    return json.dumps({"titulo": titulo, "lead": lead, "paragrafos": paragrafos}, ensure_ascii=False)


class ProvedorFalso:
    nome, modelo = "falso", "modelo-falso"

    def __init__(self, *textos):
        self.textos = list(textos)
        self.prompts = []

    def gerar(self, sistema, usuario, *, esquema=None):
        self.prompts.append(usuario)
        return llm.Resposta(self.textos.pop(0), self.modelo)


# --- Formatação ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "t, texto",
    [(3_110_000, "3,11 milhões de t"), (1_082_668, "1,08 milhão de t"), (845_400, "845 mil t"), (8_460, "8,5 mil t"), (850, "850 t")],
)
def test_toneladas_em_pt_br(t, texto):
    assert boletim.fmt_t(t) == texto


def test_formatos_pt_br():
    assert boletim.fmt_usd_t(1153.77) == "US$ 1.154/t"
    assert boletim.fmt_pct(45.69) == "45,7%" and boletim.fmt_pp(2.06) == "2,1 p.p."
    assert boletim.fmt_razao(1.8217) == "1,82 t de soja"
    assert boletim.mes_extenso("2026-03") == "março de 2026" and boletim.mes_curto("2026-03") == "mar/2026"
    assert [boletim.sentido(v) for v in (3.2, -0.4, 0.01)] == ["alta", "queda", "estabilidade"]
    assert boletim.slug("Rússia") == "russia" and boletim.slug("Estados Unidos") == "estados_unidos"


@pytest.mark.parametrize(
    "bruto, nome",
    [("PORTO DE PARANAGUA", "Paranaguá"), ("IRF SAO LUIS", "IRF São Luís"), ("ALF - SALVADOR", "ALF - Salvador"), ("SANTAREM", "Santarém")],
)
def test_nome_urf(bruto, nome):
    assert boletim.nome_urf(bruto) == nome


# --- Pacote de fatos -------------------------------------------------------------------------


def test_pacote_de_fatos_com_dados_versionados(fatos):
    assert fatos.periodo == "2026-09"
    for chave in ["troca_map_ultimo", "share_russia_k_ano", "frete_medio_ytd", "t_total_mes", "hhi_k_ano", "rota_1_ano"]:
        assert chave in fatos, chave
    assert all(isinstance(v["valor"], str) and v["valor"] and v["descricao"] for v in fatos.values())
    assert fatos["troca_map_ultimo"]["valor"] == "1,82 t de soja"  # troca_mensal.csv: 1,8217
    assert fatos["mes_referencia"]["valor"] == "setembro de 2026"
    assert "não é o preço ao produtor" in fatos["troca_map_ultimo"]["descricao"]
    sentidos = {v["valor"] for k, v in fatos.items() if k.endswith("_sentido")}
    assert sentidos <= {"alta", "queda", "estabilidade"}
    # variações vão sem sinal: a direção fica no _sentido
    assert not any(v["valor"].startswith(("-", "−", "+")) for v in fatos.values())


def test_pacote_de_um_mes_anterior(fatos):
    painel, troca = boletim.carregar(config.PROCESSED_DIR)
    antigo = boletim.montar_fatos(painel, troca, "2016-01")
    assert antigo.periodo == "2016-01" and antigo["periodo_ytd"]["valor"] == "janeiro de 2016"
    with pytest.raises(boletim.ErroBoletim, match="não está nos dados"):
        boletim.montar_fatos(painel, troca, "2030-01")


def test_pacote_sem_ano_anterior_nem_soja(tmp_path):
    base = pd.DataFrame(
        [
            {
                "ano": 2025, "mes": m, "co_ncm": "31042090", "grupo": "K", "co_pais": "149", "no_pais": "Canadá",
                "sg_uf": "PR", "no_uf": "Paraná", "co_via": "01", "no_via": "MARITIMA", "co_urf": "0917800",
                "no_urf": "PORTO DE PARANAGUA", "toneladas": 100_000.0, "kg_liquido": 1e8, "vl_fob": 3e7,
                "vl_frete": 2e6, "vl_seguro": 0.0, "vl_cif": 3.2e7,
            }
            for m in range(1, 13)
        ]
    )
    transform.gerar(base, tmp_path)
    painel, troca = boletim.carregar(tmp_path)
    f = boletim.montar_fatos(painel, troca)
    assert "t_total_mes" in f and "share_canada_k_ano" in f
    assert "var_t_total_mes" not in f and "mes_ano_anterior" not in f  # não há 2024
    assert not any(k.startswith("troca_") for k in f)


# --- Validação (igual para todos os provedores) --------------------------------------------


def test_resposta_valida_tem_os_marcadores_trocados(fatos):
    conteudo, erros = boletim.validar(resposta(), fatos)
    assert erros == []
    assert conteudo["titulo"] == "Importações em setembro de 2026"
    assert "1,82 t de soja" in conteudo["paragrafos"][3]
    assert "{" not in json.dumps(conteudo, ensure_ascii=False).replace("{\"", "")  # nenhum marcador sobrou
    assert conteudo["marcadores"]["share_russia_k_ano"] == fatos["share_russia_k_ano"]["valor"]


def test_aceita_json_em_cerca_de_codigo(fatos):
    _, erros = boletim.validar(f"```json\n{resposta()}\n```", fatos)
    assert erros == []


@pytest.mark.parametrize(
    "paragrafo, trecho",
    [
        ("As compras somaram {t_inexistente} no mês.", "marcadores que não existem"),
        ("As compras de 2026 caíram.", "dígito fora de marcador"),
        ("O volume foi de 3,1 milhões de t.", "dígito fora de marcador"),
        ("O frete quase dobrou: o dobro de antes.", "número por extenso"),
        ("Foram três milhões de toneladas.", "número por extenso"),
        ("O frete subiu {var_frete_medio_ytd}%.", "unidade"),
        ("O preço ficou em US$ {preco_cif_total_mes}.", "unidade"),
        ("O frete **subiu** no ano.", "marcação"),
        ("O frete subiu {frete_medio_ytd no ano.", "marcação"),
        ("Recomendamos antecipar as compras.", "termo proibido"),
        ("O frete disparou no ano.", "termo proibido"),
        ("O frete subiu ² vezes.", "dígito fora de marcador"),
        ("O frete subiu ½ sobre o ano anterior.", "dígito fora de marcador"),
        ("O maior volume do século XXI.", "algarismo romano"),
        ("O frete dobrou no ano.", "número por extenso"),
        ("As compras da Rússia triplicaram.", "número por extenso"),
        ("Um terço do volume veio da Rússia.", "número por extenso"),
        ("O Egito foi o quinto fornecedor.", "número por extenso"),
    ],
)
def test_rejeita_numero_literal_marcador_inexistente_e_tom(fatos, paragrafo, trecho):
    texto = resposta(paragrafos=["{t_total_ytd} no acumulado.", "Frete de {frete_medio_ytd}.", "MAP a {troca_map_ultimo}.", paragrafo])
    conteudo, erros = boletim.validar(texto, fatos)
    assert conteudo is None and any(trecho in e for e in erros), erros


def test_rejeita_direcao_que_contradiz_o_sentido(fatos):
    """Um modelo que escreve "alta" para uma queda (ou o contrário) reprova."""
    sentido = fatos["var_t_total_mes_sentido"]["valor"]
    errada, certa = ("alta", "queda") if sentido == "queda" else ("queda", "alta")
    base = ["{t_total_ytd} no acumulado.", "Frete de {frete_medio_ytd}.", "MAP a {troca_map_ultimo}."]
    _, erros = boletim.validar(resposta(paragrafos=[*base, f"As compras tiveram {errada} de {{var_t_total_mes}}."]), fatos)
    assert any("contradiz {var_t_total_mes}" in e for e in erros), erros
    _, erros = boletim.validar(resposta(paragrafos=[*base, f"As compras tiveram {certa} de {{var_t_total_mes}}."]), fatos)
    assert erros == []
    # a direção da média histórica (_posicao) também é conferida
    acima = fatos["desvio_troca_map_posicao"]["valor"].startswith("acima")
    errada = "abaixo" if acima else "acima"
    _, erros = boletim.validar(resposta(paragrafos=[*base, f"O MAP ficou {{desvio_troca_map}} {errada} da média."]), fatos)
    assert any("contradiz {desvio_troca_map}" in e for e in erros), erros
    # palavra de direção em outra frase não conta; sigla MDIC não é algarismo romano
    texto = "O frete teve alta. Dados do Comex Stat/MDIC: {var_t_total_mes_sentido} de {var_t_total_mes}."
    _, erros = boletim.validar(resposta(paragrafos=[*base, texto]), fatos)
    assert erros == []


@pytest.mark.parametrize(
    "texto, trecho",
    [
        ("não é JSON", "JSON inválido"),
        (json.dumps({"titulo": "x", "lead": "y"}), "paragrafos"),
        (resposta(paragrafos=["{t_total_ytd}.", "{frete_medio_ytd}."]), "parágrafos"),
        (resposta(paragrafos=["{t_total_ytd}."] * 7), "parágrafos"),
        (resposta(paragrafos=["Texto sem números."] * 4, lead="Sem números."), "pelo menos"),
        (resposta(titulo="Título {mes_referencia} " + "longo " * 40), "máximo"),
    ],
)
def test_rejeita_estrutura_tamanho_e_falta_de_numeros(fatos, texto, trecho):
    conteudo, erros = boletim.validar(texto, fatos)
    assert conteudo is None and any(trecho in e for e in erros), erros


def test_tenta_de_novo_uma_vez_com_os_erros_no_prompt(fatos):
    ruim = resposta(paragrafos=["Em 2026 o volume caiu.", "{t_total_ytd}.", "{frete_medio_ytd}.", "{troca_map_ultimo}."])
    p = ProvedorFalso(ruim, resposta())
    conteudo = boletim.gerar(fatos, p)
    assert conteudo["titulo"] == "Importações em setembro de 2026" and conteudo["modelo"] == "modelo-falso"
    assert len(p.prompts) == 2
    assert "REPROVADA" not in p.prompts[0]
    assert "REPROVADA" in p.prompts[1] and "dígito fora de marcador" in p.prompts[1] and "Em 2026" in p.prompts[1]


def test_falha_sem_publicar_se_reprovar_duas_vezes(fatos, tmp_path, monkeypatch):
    ruim = resposta(paragrafos=["Em 2026 o volume caiu."] * 4)
    with pytest.raises(boletim.ErroBoletim, match="nada foi publicado"):
        boletim.gerar(fatos, ProvedorFalso(ruim, ruim))
    monkeypatch.setattr(llm, "provedor_do_ambiente", lambda: ProvedorFalso(ruim, ruim))
    assert boletim.main(["--saida", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "provedor, corpo",
    [
        (lambda t: llm.Anthropic("chave-ficticia", transporte=t), lambda txt: {"stop_reason": "end_turn", "content": [{"type": "text", "text": txt}]}),
        (lambda t: llm.OpenAICompativel("chave-ficticia", "m", transporte=t), lambda txt: {"choices": [{"message": {"content": txt}}]}),
        (lambda t: llm.Ollama("m", transporte=t), lambda txt: {"message": {"content": txt}, "done": True}),
    ],
)
def test_mesma_validacao_para_todos_os_provedores(fatos, provedor, corpo):
    """Modelo fraco que inventa número reprova do mesmo jeito em qualquer provedor."""
    inventado = resposta(paragrafos=["O volume caiu 12% no ano."] * 4)
    respostas = [json.dumps(corpo(inventado)).encode()] * 2
    chamadas = []

    def transporte(url, headers, dados, timeout):
        chamadas.append(json.loads(dados))
        return 200, {}, respostas.pop(0)

    with pytest.raises(boletim.ErroBoletim):
        boletim.gerar(fatos, provedor(transporte))
    assert len(chamadas) == 2  # a primeira e uma nova tentativa com os erros


# --- Modo mock e CLI ---------------------------------------------------------------------


def test_mock_de_ponta_a_ponta_sem_nenhuma_variavel(tmp_path):
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    md = (tmp_path / "2026-09.md").read_text(encoding="utf-8")
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert boletim.RODAPE in md and "Fonte: Comex Stat/MDIC" in md and "--mock" in md
    assert latest["mock"] is True and latest["provedor"] == "mock" and latest["arquivo"] == "boletins/2026-09.md"
    assert latest["rodape"] == boletim.RODAPE and latest["fonte"] == "Comex Stat/MDIC"
    assert 4 <= len(latest["paragrafos"]) <= 6
    assert not any("{" in t for t in [latest["titulo"], latest["lead"], *latest["paragrafos"]])


def test_mock_grava_em_boletins_mock_por_padrao(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BOLETINS_DIR", tmp_path / "boletins")
    assert boletim.main(["--mock"]) == 0
    assert (tmp_path / "boletins" / "mock" / "latest.json").exists()
    assert not (tmp_path / "boletins" / "latest.json").exists()


def test_nao_refaz_sem_forcar(tmp_path, monkeypatch):
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    (tmp_path / "2026-09.md").write_text("antigo", encoding="utf-8")
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    assert (tmp_path / "2026-09.md").read_text(encoding="utf-8") == "antigo"
    assert boletim.main(["--mock", "--saida", str(tmp_path), "--forcar"]) == 0
    assert (tmp_path / "2026-09.md").read_text(encoding="utf-8") != "antigo"


def test_sem_chave_falha_com_mensagem_clara(tmp_path, caplog):
    assert boletim.main(["--saida", str(tmp_path)]) == 1
    assert "ANTHROPIC_API_KEY" in caplog.text and "--mock" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_selecao_de_provedor_pelo_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("RADAR_LLM_MODEL", "modelo-local")
    escolhido = []
    original = llm.provedor_do_ambiente

    def espiao():
        p = original()
        escolhido.append(p)
        return ProvedorFalso(resposta())

    monkeypatch.setattr(llm, "provedor_do_ambiente", espiao)
    assert boletim.main(["--saida", str(tmp_path)]) == 0
    assert isinstance(escolhido[0], llm.Ollama) and escolhido[0].modelo == "modelo-local"


def test_fatos_imprime_o_pacote_sem_chamar_ia(capsys):
    assert boletim.main(["--fatos"]) == 0
    pacote = json.loads(capsys.readouterr().out)
    assert pacote["troca_map_ultimo"]["valor"] == "1,82 t de soja"


def test_boletim_e_llm_nao_importam_dependencias_de_terceiros():
    """O job que recebe a chave roda só a biblioteca padrão: sem pandas, pyarrow ou requests."""
    codigo = (
        "import sys, src.boletim\n"
        "proibidos = {'pandas', 'pyarrow', 'requests', 'numpy'} & set(sys.modules)\n"
        "assert not proibidos, proibidos\n"
    )
    subprocess.run([sys.executable, "-c", codigo], check=True, cwd=config.RAIZ)
