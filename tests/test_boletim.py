"""Testes do boletim com IA (src/boletim.py). Nenhum teste usa rede nem chave.

Os testes com os dados versionados não fixam valores do último mês: o workflow mensal roda a
suíte logo depois de gravar um mês novo, e um valor fixo derrubaria a atualização.
"""

import copy
import json
import subprocess
import sys

import pandas as pd
import pytest

from src import boletim, config, ingest, llm, transform

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


@pytest.fixture(scope="module")
def ultimo_mes():
    painel, _ = boletim.carregar(config.PROCESSED_DIR)
    return painel["mensal"]["periodos"][-1]


BASE = ["{t_total_ytd} no acumulado.", "Frete de {frete_medio_ytd}.", "No porto, o MAP valeu {troca_map_ultimo}."]


def resposta(titulo="Importações em {mes_referencia}", lead=None, paragrafos=None):
    lead = lead or "O Brasil importou {t_total_mes} em {mes_referencia}, {var_t_total_mes_sentido} de {var_t_total_mes}."
    paragrafos = paragrafos or [
        "No acumulado de {periodo_ytd}, as compras somaram {t_total_ytd}.",
        "O frete médio do ano ficou em {frete_medio_ytd}.",
        "A maior origem dos potássicos respondeu por {share_lider_k_ano} do volume.",
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


def test_pacote_de_fatos_com_dados_versionados(fatos, ultimo_mes):
    assert fatos.periodo == ultimo_mes
    for chave in ["troca_map_ultimo", "share_lider_k_ano", "frete_medio_ytd", "t_total_mes", "hhi_k_ano", "rota_1_ano"]:
        assert chave in fatos, chave
    assert all(isinstance(v["valor"], str) and v["valor"] and v["descricao"] for v in fatos.values())
    _, troca = boletim.carregar(config.PROCESSED_DIR)
    linha = next(r for r in troca if r["produto"] == "map" and r["periodo"] == ultimo_mes)
    assert fatos["troca_map_ultimo"]["valor"] == boletim.fmt_razao(float(linha["t_soja_por_t"]))
    assert fatos["mes_referencia"]["valor"] == boletim.mes_extenso(ultimo_mes)
    assert "não é o preço ao produtor" in fatos["troca_map_ultimo"]["descricao"]
    sentidos = {v["valor"] for k, v in fatos.items() if k.endswith("_sentido")}
    assert sentidos <= {"alta", "queda", "estabilidade"}
    # variações vão sem sinal: a direção fica no _sentido
    assert not any(v["valor"].startswith(("-", "−", "+")) for v in fatos.values())


def test_pacote_de_um_mes_anterior(fatos):
    painel, troca = boletim.carregar(config.PROCESSED_DIR)
    antigo = boletim.montar_fatos(painel, troca, "2016-01")
    assert antigo.periodo == "2016-01" and antigo["periodo_ytd"]["valor"] == "janeiro de 2016"
    # origens, HHI, portos e rotas do dashboard.json cobrem 2016 inteiro: não valem para janeiro
    assert not any(k.startswith(("lider_", "share_lider_", "hhi_", "cr3_", "porto_", "rota_", "n_rotas")) for k in antigo)
    assert "t_total_ytd" in antigo and "troca_map_ultimo" in antigo
    dezembro = boletim.montar_fatos(painel, troca, "2016-12")
    assert "lider_k_ano" in dezembro and "hhi_k_ano" in dezembro
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
    assert conteudo["titulo"] == f"Importações em {fatos['mes_referencia']['valor']}"
    assert fatos["troca_map_ultimo"]["valor"] in conteudo["paragrafos"][3]
    assert "{" not in json.dumps(conteudo, ensure_ascii=False).replace("{\"", "")  # nenhum marcador sobrou
    assert conteudo["marcadores"]["share_lider_k_ano"] == fatos["share_lider_k_ano"]["valor"]


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
        # achados da revisão adversarial da fase 5
        ("A fatia da China avançou meio ponto percentual no ano.", "número por extenso"),
        ("O frete ficou uma vez e meia o do ano anterior.", "número por extenso"),
        ("As compras passaram de um bi de dólares.", "número por extenso"),
        ("O volume da Nigéria caiu a zero.", "número por extenso"),
        ("O frete quintuplicou no ano.", "número por extenso"),
        ("As compras caíram sessenta por cento.", "número por extenso"),
        ("As compras somaram {t_total_mes} no mês, cerca de cinquenta porcento do ano.", "número por extenso"),
        ("Em agosto, o Brasil importou {t_total_mes}.", "mês ou período"),
        ("No primeiro semestre, as compras somaram {t_total_ytd}.", "mês ou período"),
        ("É o maior volume da década, com {t_total_ytd}.", "mês ou período"),
        ("É o maior volume do século xx, com {t_total_ytd}.", "mês ou período"),
        ("O volume de {t_total_mes} compara com o mês anterior.", "mês anterior"),
        ("É o terceiro mês seguido com {t_total_mes} importadas.", "contagem"),
        ("Pela primeira vez, o Turcomenistão ficou entre as maiores origens.", "contagem"),
        ("É o segundo ano consecutivo com {frete_medio_ytd} de frete.", "contagem"),
        ("O volume de {t_total_ytd} é recorde.", "contagem"),
        ("Vale a pena comprar ureia agora, com o volume de {t_total_mes}.", "termo proibido"),
        ("O momento é favorável para antecipar as compras de potássio.", "termo proibido"),
        ("Num cenário crítico e preocupante, o volume foi de {t_total_mes}.", "termo proibido"),
        ("Os preços tiveram um movimento vertiginoso no ano.", "termo proibido"),
        ("O volume teve {var_t_total_mes} pontos percentuais.", "unidade"),
        ("O preço foi de {preco_cif_total_mes}; segundo o ＭＤＩＣ, o frete ｄｏｂｒｏｕ.", "número por extenso"),
    ],
)
def test_rejeita_numero_literal_marcador_inexistente_e_tom(fatos, paragrafo, trecho):
    texto = resposta(paragrafos=[*BASE, paragrafo])
    conteudo, erros = boletim.validar(texto, fatos)
    assert conteudo is None and any(trecho in e for e in erros), erros


def valida(fatos, *paragrafos):
    """Erros de uma resposta com BASE mais os parágrafos dados."""
    return boletim.validar(resposta(paragrafos=[*BASE, *paragrafos]), fatos)[1]


# --- Direção: só pelos marcadores _sentido e _posicao --------------------------------------
# A IA não escreve a direção: toda palavra de direção ou comparação fora dos marcadores reprova,
# e o _sentido (ou a _posicao) vai colado à sua variação. Assim nenhum teste depende de os
# dados do último mês serem de alta, de queda ou de estabilidade.


@pytest.mark.parametrize(
    "texto",
    [
        "As importações subiram {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "As importações caíram, com {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "As importações de potássicos superaram as de {mes_ano_anterior}, {var_t_k_mes_sentido} de {var_t_k_mes}.",
        "As importações ultrapassaram as do ano anterior: {var_t_total_ytd_sentido} de {var_t_total_ytd}.",
        "As compras foram maiores, com {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "As compras foram menores que em {mes_ano_anterior}, {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "As compras tiveram {var_t_total_mes} a mais que em {mes_ano_anterior}, {var_t_total_mes_sentido}.",
        "O frete médio desceu para {frete_medio_ytd}.",
        "O frete médio teve um alívio: {var_frete_medio_ytd_sentido} de {var_frete_medio_ytd}.",
        "O frete cortou {var_frete_medio_ytd_sentido} de {var_frete_medio_ytd}.",
        "O preço afundou, {var_preco_cif_k_mes_sentido} de {var_preco_cif_k_mes}.",
        "As compras de compostos minguaram, {var_t_npk_mes_sentido} de {var_t_npk_mes}.",
        "As compras aceleraram, {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "As compras não caíram em {mes_referencia}: {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "A participação teve variação positiva, {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "O preço ficou mais caro, {var_preco_cif_total_mes_sentido} de {var_preco_cif_total_mes}.",
        "No porto, a relação do MAP ficou aquém da média, {desvio_troca_map} {desvio_troca_map_posicao}.",
        "No porto, a relação do MAP ficou acima da média, {desvio_troca_map} {desvio_troca_map_posicao}.",
        "No porto, a relação do MAP melhorou, {desvio_troca_map} {desvio_troca_map_posicao}.",
        "No porto, a relação do MAP está favorável, {desvio_troca_map} {desvio_troca_map_posicao}.",
        "As importações de potássicos cresceram em {mes_referencia}.",
        "O HHI dos potássicos recuou de {hhi_k_ano_anterior} para {hhi_k_ano}.",
        "A relação de troca no porto, que não é o preço ao produtor, subiu para {troca_map_ultimo}.",
    ],
)
def test_palavra_de_direcao_fora_dos_marcadores_reprova(fatos, texto):
    erros = valida(fatos, texto)
    assert any("palavra de direção" in e for e in erros), (texto, erros)


@pytest.mark.parametrize(
    "texto",
    [
        "As importações somaram {t_total_mes} em {mes_referencia}, {var_t_total_mes_sentido} de {var_t_total_mes} sobre {mes_ano_anterior}.",
        "O preço médio CIF dos nitrogenados ficou em {preco_cif_n_mes}, com {var_preco_cif_n_mes_sentido} de {var_preco_cif_n_mes} na comparação anual.",
        "O frete médio de {periodo_ytd} foi de {frete_medio_ytd}, {var_frete_medio_ytd_sentido} de {var_frete_medio_ytd} sobre {periodo_ytd_anterior}.",
        "Os potássicos tiveram {var_t_k_mes} ({var_t_k_mes_sentido}) sobre {mes_ano_anterior}, e os nitrogenados, {var_t_n_mes_sentido} de {var_t_n_mes}.",
        "A maior origem dos potássicos respondeu por {share_lider_k_ano} do volume.",
        "A segunda maior parte do volume, {share_n_ytd}, foi de nitrogenados.",
        "O HHI dos potássicos ficou em {hhi_k_ano}, {hhi_k_ano_posicao} de alta concentração, de {limiar_hhi}, contra {hhi_k_ano_anterior} no ano anterior inteiro.",
        "No porto, uma tonelada de MAP equivalia a {troca_map_ultimo}, {desvio_troca_map} {desvio_troca_map_posicao}, de {troca_map_media}.",
        "No porto, uma tonelada de KCl valia {troca_kcl_ultimo}, ou {sacas_kcl_ultimo}; não é o preço ao produtor.",
        "A rota de menor frete entre as maiores foi {rota_frete_min_ano}, a {frete_rota_min_ano}.",
        "O preço FOB da soja exportada ficou em {soja_fob_ultimo}, com {var_soja_fob_ano_sentido} de {var_soja_fob_ano} — sobre o mesmo mês do ano anterior.",
        "Este boletim não traz recomendação de compra ou venda; o volume foi de {t_total_mes}.",
    ],
)
def test_frases_corretas_com_a_direcao_pelos_marcadores_passam(fatos, texto):
    assert valida(fatos, texto) == []


def test_validacao_nao_depende_da_direcao_dos_dados(fatos):
    """Regressão: com o último mês em estabilidade, a mesma resposta continua valendo."""
    estavel = copy.deepcopy(fatos)
    for chave in [k for k in estavel if k.endswith("_sentido")]:
        estavel[chave] = {**estavel[chave], "valor": "estabilidade"}
    assert boletim.validar(resposta(), estavel)[1] == []
    assert boletim.validar(resposta(), fatos)[1] == []


@pytest.mark.parametrize(
    "texto, trecho",
    [
        ("As compras tiveram {var_t_k_mes_sentido} de {var_t_n_mes} e {var_t_n_mes_sentido} de {var_t_k_mes}.", "colado"),
        ("Os nitrogenados variaram {var_t_n_mes} em {mes_referencia}.", "vai sem sinal"),
        ("A participação teve {var_t_total_mes_sentido} em {mes_referencia} e chegou a {share_n_ytd}.", "colado"),
        ("No porto, a relação do MAP ficou {desvio_troca_map} de {troca_map_media}.", "vai sem sinal"),
        ("O HHI dos potássicos ficou {hhi_k_ano_posicao}.", "colado"),
    ],
)
def test_sentido_e_posicao_colados_a_propria_variacao(fatos, texto, trecho):
    erros = valida(fatos, texto)
    assert any(trecho in e for e in erros), erros


@pytest.mark.parametrize("sinal", ["+", "-", "−", "± ", "+ ", "menos ", "mais ", "˗", "⁻", "&minus;"])
def test_rejeita_sinal_antes_de_variacao(fatos, sinal):
    texto = f"As toneladas tiveram {{var_t_total_mes_sentido}} de {sinal}{{var_t_total_mes}} sobre {{mes_ano_anterior}}."
    assert valida(fatos, texto), texto
    # hífen entre marcadores sem direção e travessão com espaço continuam valendo
    texto = "Entre {ano_anterior}-{ano_referencia}, o frete médio de {periodo_ytd_anterior} foi de {frete_medio_ytd_anterior}."
    assert valida(fatos, texto) == []


@pytest.mark.parametrize(
    "texto, trecho",
    [
        ("O produtor precisou de {troca_kcl_ultimo} para pagar uma tonelada de KCl.", "no porto"),
        ("No porto, o produtor precisou de {troca_kcl_ultimo} por tonelada de KCl, ou {sacas_kcl_ultimo}.", "Não fale em produtor"),
        ("No porto, a relação do KCl foi de {troca_kcl_ultimo}, e não há dúvida de que é isso que o produtor paga.", "Não fale em produtor"),
        ("No porto, a tonelada de KCl custou {troca_kcl_ultimo} ao sojicultor.", "Não fale em produtor"),
        ("Na porteira, a tonelada de KCl custou {sacas_kcl_ultimo} ao cooperado, medido no porto.", "Não fale em produtor"),
        ("A relação de troca do MAP ficou {desvio_troca_map} {desvio_troca_map_posicao}.", "no porto"),
        ("A relação de troca do produtor de soja é medida com a soja a {soja_fob_ultimo}.", "no porto"),
    ],
)
def test_relacao_de_troca_exige_no_porto_e_nao_vira_preco_ao_produtor(fatos, texto, trecho):
    erros = valida(fatos, texto)
    assert any(trecho in e for e in erros), erros


@pytest.mark.parametrize(
    "lead",
    [
        "O Brasil importou {t_total_mes}.\n\nEm {mes_referencia} houve {var_t_total_mes_sentido} de {var_t_total_mes}.",
        "Destaques de {periodo_ytd}:\n- potássicos com {share_k_ytd}\n- nitrogenados com {share_n_ytd}",
        "O Brasil importou ‮{t_total_mes}‬ em {mes_referencia}.",
        "O volume do​brou em {mes_referencia}, para {t_total_mes}.",
        "O Brasil importou {t_total_mes} em {mes_referencia}\u0007.",
        "As compras somaram trê️s mil em {mes_referencia}, ou {t_total_mes}.",  # seletor de variação
        "Em ago͏sto, o Brasil importou {t_total_mes}.",  # combining grapheme joiner
        "Volume ▲ em {mes_referencia}: {t_total_mes}.",
        "O frete chegou a 💯 em {mes_referencia}, com {frete_medio_ytd}.",
        "O volume dоbrou: {t_total_mes} em {mes_referencia}.",  # "о" cirílico
        "As compras somaram ƻ,Ʒ Mt em {mes_referencia}, com {t_total_mes}.",  # ƻ e Ʒ
        "O peso do frete foi de {frete_pct_cif_ytd}, &half; do preço de {preco_fob_total_mes}.",
        "Detalhes com @radar sobre {t_total_mes} em {mes_referencia}.",
    ],
)
def test_rejeita_caractere_fora_do_permitido(fatos, lead):
    _, erros = boletim.validar(resposta(lead=lead), fatos)
    assert any("caractere fora do permitido" in e or "dígito" in e for e in erros), erros


@pytest.mark.parametrize(
    "texto, trecho",
    [
        ("Detalhes em https://exemplo.invalid sobre {t_total_mes}.", "link"),
        ("Veja www.exemplo.invalid com {t_total_mes}.", "link"),
        ("- Destaques de {periodo_ytd}, com {t_total_ytd}.", "lista"),
        ("O frete médio foi de USD lOO por tonelada, ou {frete_medio_ytd}.", "letras no lugar"),
        ("O Egito foi a vigésima origem dos fosfatados, com {share_p_ytd}.", "por extenso"),
        ("O frete é o quíntuplo do de {ano_anterior}, ou {frete_medio_ytd}.", "por extenso"),
        ("As compras somaram um milhar de navios, ou {t_total_mes}.", "por extenso"),
        ("O volume de {t_total_ytd} é o maior da série histórica.", "série"),
        ("Nunca antes o Brasil importou tanto, {t_k_ytd} no ano.", "contagem"),
        ("O volume de {t_total_ytd} não tem precedentes.", "contagem"),
        ("Na comparação mensal, as compras tiveram {var_t_total_mes_sentido} de {var_t_total_mes}.", "mês anterior"),
        ("Sobre o mês precedente, as compras tiveram {var_t_total_mes_sentido} de {var_t_total_mes}.", "mês anterior"),
        ("No último biênio, as compras somaram {t_total_ytd}.", "período"),
        ("O momento é propício: as compras somaram {t_total_mes}.", "termo proibido"),
        ("Convém travar o preço com o frete a {frete_medio_ytd}.", "termo proibido"),
    ],
)
def test_rejeita_links_listas_numeros_disfarcados_recordes_e_periodos(fatos, texto, trecho):
    erros = valida(fatos, texto)
    assert any(trecho in e for e in erros), erros


def test_maximo_da_serie_so_com_a_chave_que_o_traz(fatos):
    assert valida(fatos, "No porto, o máximo da série do MAP foi {troca_map_max}, em {troca_map_max_mes}.") == []


@pytest.mark.parametrize(
    "texto, trecho",
    [
        ("As compras somaram {n_origens_k_ano}{var_frete_medio_ytd} no ano.", "colados"),
        ("O frete médio das rotas foi de USD {hhi_n_ano} por tonelada.", "unidade inventada"),
        ("O Brasil importou {n_rotas_ano} toneladas de compostos.", "unidade inventada"),
    ],
)
def test_rejeita_numero_composto_ou_com_unidade_inventada(fatos, texto, trecho):
    erros = valida(fatos, texto)
    assert any(trecho in e for e in erros), erros
    assert valida(fatos, "Os potássicos vieram de {n_origens_k_ano} países, em {n_rotas_ano} rotas no ano.") == []


def test_nomes_de_pais_porto_grupo_produto_e_periodo_ligados_aos_numeros(fatos):
    lider = fatos["lider_k_ano"]["valor"]
    outro = next(n for n in sorted(set().union(*fatos.entidades.values())) if n != lider and n not in fatos["rota_1_ano"]["valor"])
    assert valida(fatos, f"{lider} liderou os potássicos, com {{share_lider_k_ano}}.") == []
    erros = valida(fatos, f"{outro} liderou os potássicos, com {{share_lider_k_ano}}.")
    assert any(f"cita '{outro}'" in e for e in erros), erros
    fora = sorted(fatos.outros_nomes)[0]
    erros = valida(fatos, f"{fora} também vendeu potássicos ao Brasil.")
    assert any("não está no pacote" in e for e in erros), erros
    erros = valida(fatos, "As importações de nitrogenados tiveram {var_t_k_mes_sentido} de {var_t_k_mes}.")
    assert any("nitrogenados" in e for e in erros), erros
    erros = valida(fatos, "As importações de ureia somaram {t_n_mes} em {mes_referencia}.")
    assert any("ureia" in e for e in erros), erros
    erros = valida(fatos, "Em {mes_ano_anterior}, o Brasil importou {t_total_mes}.")
    assert any("período anterior" in e for e in erros), erros


def test_log_da_tentativa_reprovada_fica_numa_linha(fatos, caplog):
    """Texto da IA com '::comando::' não vira comando do runner do Actions no log."""
    ruim = resposta(titulo="Importações {mes_referencia} {\n::warning title=x::injetado}")
    with pytest.raises(boletim.ErroBoletim):
        boletim.gerar(fatos, ProvedorFalso(ruim, ruim))
    assert not any(linha.lstrip().startswith("::") for r in caplog.records for linha in r.getMessage().splitlines())


@pytest.mark.parametrize("mes", ["2015-01", "2015-07", "2016-01", "2020-06"])
def test_mock_passa_em_qualquer_mes_dos_dados(tmp_path, mes):
    assert boletim.main(["--mock", "--mes", mes, "--saida", str(tmp_path)]) == 0
    assert (tmp_path / f"{mes}.md").exists()


def test_nota_fixa_da_relacao_de_troca_no_boletim(tmp_path, ultimo_mes):
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    md = (tmp_path / f"{ultimo_mes}.md").read_text(encoding="utf-8")
    assert latest["nota"] == boletim.NOTA_TROCA and boletim.NOTA_TROCA in md


def test_rejeita_paragrafo_so_com_marcador(fatos):
    assert any("só marcadores" in e for e in valida(fatos, "{t_total_mes}"))


def test_raciocinio_em_think_antes_do_json_e_ignorado(fatos):
    texto = "<think>Vou abrir com {t_total_mes} e citar {mes_referencia}.</think>\n" + resposta()
    _, erros = boletim.validar(texto, fatos)
    assert erros == [], erros
    # rascunho fora de <think>: vale o último objeto com titulo/lead/paragrafos
    rascunho = json.dumps({"titulo": "Rascunho de {mes_referencia}", "lead": "x", "paragrafos": []})
    conteudo, erros = boletim.validar(f"Rascunho: {rascunho}\nFinal: {resposta()}", fatos)
    assert erros == [] and conteudo["titulo"].startswith("Importações em")


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
    assert conteudo["titulo"] == f"Importações em {fatos['mes_referencia']['valor']}" and conteudo["modelo"] == "modelo-falso"
    assert len(p.prompts) == 2
    assert "REPROVADA" not in p.prompts[0]
    assert "REPROVADA" in p.prompts[1] and "dígito fora de marcador" in p.prompts[1] and "Em 2026" in p.prompts[1]


@pytest.mark.parametrize(
    "devolvido, esperado",
    [
        ("claude-sonnet-5-5", "claude-sonnet-5-5"),
        ("meta-llama/Llama-3.3-70B-Instruct", "meta-llama/Llama-3.3-70B-Instruct"),
        ("gpt</sub>\n\n## Ureia sobe para US$ 999/t\n\n[Leia](https://exemplo.invalid)", "modelo-falso"),
        ("", "modelo-falso"),
    ],
)
def test_nome_do_modelo_devolvido_so_entra_se_for_nome_de_modelo(fatos, devolvido, esperado):
    class Provedor(ProvedorFalso):
        def gerar(self, sistema, usuario, *, esquema=None):
            return llm.Resposta(resposta(), devolvido)

    assert boletim.gerar(fatos, Provedor())["modelo"] == esperado


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


def test_mock_de_ponta_a_ponta_sem_nenhuma_variavel(tmp_path, ultimo_mes):
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    md = (tmp_path / f"{ultimo_mes}.md").read_text(encoding="utf-8")
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert boletim.RODAPE in md and "Fonte: Comex Stat/MDIC" in md and "--mock" in md
    assert latest["mock"] is True and latest["provedor"] == "mock" and latest["arquivo"] == f"boletins/{ultimo_mes}.md"
    assert latest["rodape"] == boletim.RODAPE and latest["fonte"] == "Comex Stat/MDIC"
    assert 4 <= len(latest["paragrafos"]) <= 6
    assert not any("{" in t for t in [latest["titulo"], latest["lead"], *latest["paragrafos"]])


def test_mock_grava_em_boletins_mock_por_padrao(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BOLETINS_DIR", tmp_path / "boletins")
    assert boletim.main(["--mock"]) == 0
    assert (tmp_path / "boletins" / "mock" / "latest.json").exists()
    assert not (tmp_path / "boletins" / "latest.json").exists()


def test_nao_refaz_sem_forcar(tmp_path, ultimo_mes):
    arquivo = tmp_path / f"{ultimo_mes}.md"
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    arquivo.write_text("antigo", encoding="utf-8")
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    assert arquivo.read_text(encoding="utf-8") == "antigo"
    assert boletim.main(["--mock", "--saida", str(tmp_path), "--forcar"]) == 0
    assert arquivo.read_text(encoding="utf-8") != "antigo"


def test_mes_antigo_nao_tira_o_mais_recente_do_latest(tmp_path, ultimo_mes):
    assert boletim.main(["--mock", "--saida", str(tmp_path)]) == 0
    assert boletim.main(["--mock", "--mes", "2024-03", "--saida", str(tmp_path)]) == 0
    assert (tmp_path / "2024-03.md").exists()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["mes"] == ultimo_mes


def test_links_apontam_para_o_repositorio_em_uso(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert boletim._enderecos() == (boletim.REPOSITORIO, boletim.DASHBOARD)
    env = {"GITHUB_REPOSITORY": "Fulana/radar-fork", "GITHUB_SERVER_URL": "https://github.com"}
    assert boletim._enderecos(env) == ("https://github.com/Fulana/radar-fork", "https://fulana.github.io/radar-fork/")
    assert boletim._enderecos({"GITHUB_REPOSITORY": "x/y](javascript:1)"}) == (boletim.REPOSITORIO, boletim.DASHBOARD)


def test_boletim_funciona_quando_entra_um_mes_novo(tmp_path):
    """Regressão: o mês seguinte entra nos dados (como no workflow mensal) e o boletim continua
    saindo; para o mês que deixou de ser o último do ano, os fatos anuais saem do pacote."""
    base = transform.carregar_base(config.RAW_DIR)
    soja = ingest.ler_soja(config.RAW_SOJA_DIR)
    ano, mes = (int(v) for v in base.sort_values(["ano", "mes"]).iloc[-1][["ano", "mes"]])
    novo_ano, novo_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    def com_mes_novo(df):
        ultimo = df[(df["ano"].astype(int) == ano) & (df["mes"].astype(int) == mes)].copy()
        ultimo["ano"], ultimo["mes"] = novo_ano, novo_mes
        return pd.concat([df, ultimo], ignore_index=True)

    transform.gerar(com_mes_novo(base), tmp_path / "proc", com_mes_novo(soja))
    novo, anterior = f"{novo_ano}-{novo_mes:02d}", f"{ano}-{mes:02d}"
    assert boletim.main(["--mock", "--dados", str(tmp_path / "proc"), "--saida", str(tmp_path / "b")]) == 0
    assert (tmp_path / "b" / f"{novo}.md").exists()
    assert boletim.main(["--mock", "--dados", str(tmp_path / "proc"), "--saida", str(tmp_path / "b"), "--mes", anterior]) == 0
    painel, troca = boletim.carregar(tmp_path / "proc")
    f = boletim.montar_fatos(painel, troca, anterior)
    assert ("lider_k_ano" in f) == (novo_ano != ano)  # mesmo ano: o bloco anual já inclui o mês novo



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


def test_fatos_imprime_o_pacote_sem_chamar_ia(capsys, fatos):
    assert boletim.main(["--fatos"]) == 0
    pacote = json.loads(capsys.readouterr().out)
    assert pacote["troca_map_ultimo"]["valor"] == fatos["troca_map_ultimo"]["valor"]


def test_boletim_e_llm_nao_importam_dependencias_de_terceiros():
    """O job que recebe a chave roda só a biblioteca padrão: sem pandas, pyarrow ou requests."""
    codigo = (
        "import sys, src.boletim\n"
        "proibidos = {'pandas', 'pyarrow', 'requests', 'numpy'} & set(sys.modules)\n"
        "assert not proibidos, proibidos\n"
    )
    subprocess.run([sys.executable, "-c", codigo], check=True, cwd=config.RAIZ)
