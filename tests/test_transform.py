"""Testes dos cálculos da fase 2: preço por tonelada, shares, YoY, HHI e sazonalidade."""

import json

import pandas as pd
import pytest

from src import config, ingest, transform

CANADA = ("149", "Canadá")
RUSSIA = ("676", "Rússia")
CHINA = ("160", "China")
PAIS_X = ("999", "País X")
SANTOS = ("0817800", "PORTO DE SANTOS")
PARANAGUA = ("0917800", "PORTO DE PARANAGUA")


def linha(ano, mes, grupo="K", pais=CANADA, t=100.0, fob=30_000.0, frete=2_000.0, urf=PARANAGUA):
    """Uma linha no formato de transform.carregar_base."""
    return {
        "ano": ano,
        "mes": mes,
        "co_ncm": "31042090",
        "grupo": grupo,
        "co_pais": pais[0],
        "no_pais": pais[1],
        "sg_uf": "PR",
        "no_uf": "Paraná",
        "co_via": "01",
        "no_via": "MARITIMA",
        "co_urf": urf[0],
        "no_urf": urf[1],
        "toneladas": float(t),
        "kg_liquido": float(t) * 1000,
        "vl_fob": float(fob),
        "vl_frete": float(frete),
        "vl_seguro": 0.0,
        "vl_cif": float(fob + frete),
    }


def base(linhas):
    return pd.DataFrame(linhas)


def dois_anos():
    """2024 e 2025 completos. K: Canadá e Rússia; N: China (e Rússia em 2025)."""
    linhas = []
    for mes in range(1, 13):
        linhas += [
            linha(2024, mes, pais=CANADA, t=60),
            linha(2024, mes, pais=RUSSIA, t=40, urf=SANTOS),
            linha(2024, mes, grupo="N", pais=CHINA, t=10),
            linha(2025, mes, pais=CANADA, t=50),
            linha(2025, mes, pais=RUSSIA, t=50, urf=SANTOS),
            linha(2025, mes, grupo="N", pais=CHINA, t=30),
            linha(2025, mes, grupo="N", pais=RUSSIA, t=10, urf=SANTOS),
        ]
    return base(linhas)


# --- Preço por tonelada ---------------------------------------------------------------------


def test_preco_por_tonelada_divide_o_valor_pelo_peso_e_ignora_peso_zero():
    preco = transform.preco_por_tonelada(pd.Series([300_000.0, 50.0, 0.0]), pd.Series([1_000.0, 0.0, 0.0]))
    assert preco.iloc[0] == pytest.approx(300)
    assert preco.iloc[1:].isna().all()


def test_preco_mensal_e_media_ponderada_pelo_peso():
    df = transform.precos(
        base(
            [
                linha(2025, 1, t=100, fob=30_000, frete=1_000),  # 300 US$/t
                linha(2025, 1, t=300, fob=120_000, frete=5_000),  # 400 US$/t
            ]
        )
    )
    assert len(df) == 1
    r = df.iloc[0]
    assert r["periodo"] == "2025-01"
    assert r["preco_fob_usd_t"] == pytest.approx(375)  # 150.000 / 400 t, e não (300 + 400) / 2
    assert r["frete_usd_t"] == pytest.approx(15)  # 6.000 / 400 t
    assert r["preco_cif_usd_t"] == pytest.approx(390)  # 156.000 / 400 t


def test_precos_por_grupo_e_origem():
    linhas = [
        linha(2025, 1, pais=CANADA, t=10, fob=3_000),
        linha(2025, 1, pais=RUSSIA, t=10, fob=2_500),
        linha(2025, 1, grupo="N", pais=RUSSIA, t=10, fob=4_000),
    ]
    por_origem = transform.precos(base(linhas)).set_index(["grupo", "no_pais"])["preco_fob_usd_t"]
    assert por_origem.to_dict() == {("K", "Canadá"): 300, ("K", "Rússia"): 250, ("N", "Rússia"): 400}
    por_grupo = transform.precos(base(linhas), por_origem=False).set_index("grupo")["preco_fob_usd_t"]
    assert por_grupo.to_dict() == {"K": 275, "N": 400}


# --- Shares e YoY ---------------------------------------------------------------------------


@pytest.mark.parametrize("entidade", [transform.PAIS, transform.URF])
def test_shares_somam_100_por_ano_e_grupo(entidade):
    df = transform.shares_anuais(dois_anos(), entidade)
    somas = df.groupby(["ano", "grupo"])["share_t_pct"].sum()
    assert set(somas.index.get_level_values("grupo")) == {"K", "N", "TOTAL"}
    assert somas.to_numpy() == pytest.approx(100)
    anteriores = df[df["ano"] == 2025].groupby("grupo")["share_t_pct_ano_anterior"].sum()
    assert anteriores.to_numpy() == pytest.approx(100)


def test_share_e_variacao_sobre_o_ano_anterior():
    df = transform.shares_anuais(dois_anos(), transform.PAIS)
    k = df[(df["ano"] == 2025) & (df["grupo"] == "K")].set_index("no_pais")
    assert k.loc["Rússia", "share_t_pct"] == pytest.approx(50)
    assert k.loc["Rússia", "var_share_pp"] == pytest.approx(10)  # 40% -> 50%
    assert k.loc["Rússia", "var_t_pct"] == pytest.approx(25)  # 480 t -> 600 t
    total = df[(df["ano"] == 2025) & (df["grupo"] == "TOTAL")].set_index("no_pais")
    assert total.loc["Rússia", "share_t_pct"] == pytest.approx(100 * 720 / 1680)  # K + N
    assert df.loc[df["ano"] == 2024, "var_t_pct"].isna().all()  # primeiro ano: sem comparação


def test_ano_parcial_compara_com_os_mesmos_meses_do_ano_anterior():
    linhas = [linha(2025, m, t=100) for m in range(1, 13)] + [linha(2026, m, t=150) for m in range(1, 4)]
    df = transform.shares_anuais(base(linhas), transform.PAIS)
    r = df[(df["ano"] == 2026) & (df["grupo"] == "K")].iloc[0]
    assert r["n_meses"] == 3
    assert r["toneladas_ano_anterior"] == pytest.approx(300)  # jan-mar/2025, não o ano inteiro
    assert r["var_t_pct"] == pytest.approx(50)


def test_ano_anterior_incompleto_fica_sem_comparacao():
    linhas = [linha(2015, m, t=100) for m in range(6, 13)] + [linha(2016, m, t=100) for m in range(1, 13)]
    df = transform.shares_anuais(base(linhas), transform.PAIS)
    assert df["var_t_pct"].isna().all() and df["var_share_pp"].isna().all()


def test_quem_saiu_aparece_com_zero_e_quem_entrou_sem_variacao_percentual():
    linhas = []
    for mes in range(1, 13):
        linhas += [linha(2021, mes, pais=CANADA, t=50), linha(2021, mes, pais=PAIS_X, t=50)]
        linhas += [linha(2022, mes, pais=CANADA, t=80), linha(2022, mes, pais=CHINA, t=20)]
    df = transform.shares_anuais(base(linhas), transform.PAIS)
    k = df[(df["ano"] == 2022) & (df["grupo"] == "K")].set_index("no_pais")
    assert k.loc["País X", "toneladas"] == 0 and k.loc["País X", "share_t_pct"] == 0
    assert k.loc["País X", "var_t_pct"] == pytest.approx(-100)
    assert k.loc["País X", "var_share_pp"] == pytest.approx(-50)
    assert pd.isna(k.loc["China", "var_t_pct"])
    assert k.loc["China", "var_share_pp"] == pytest.approx(20)
    assert k["share_t_pct"].sum() == pytest.approx(100)


# --- Concentração (HHI) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "shares, esperado",
    [([100], 10_000), ([50, 50], 5_000), ([60, 30, 10], 4_600), ([25, 25, 25, 25], 2_500)],
)
def test_hhi(shares, esperado):
    assert transform.hhi(pd.Series(shares)) == pytest.approx(esperado)


def test_concentracao_por_ano_e_grupo():
    shares = transform.shares_anuais(dois_anos(), transform.PAIS)
    conc = transform.concentracao(shares).set_index(["ano", "grupo"])
    assert conc.loc[(2024, "N"), "hhi"] == pytest.approx(10_000)  # só a China
    assert conc.loc[(2024, "K"), "hhi"] == pytest.approx(60**2 + 40**2)
    assert conc.loc[(2024, "K"), "lider_no_pais"] == "Canadá"
    assert conc.loc[(2025, "K"), "hhi"] == pytest.approx(5_000)
    assert conc.loc[(2025, "N"), "hhi"] == pytest.approx(75**2 + 25**2)
    assert conc.loc[(2025, "N"), "n_origens"] == 2
    assert conc.loc[(2025, "N"), "cr3_pct"] == pytest.approx(100)


# --- Sazonalidade ---------------------------------------------------------------------------


def test_sazonalidade_usa_so_anos_completos():
    linhas = [linha(2024, m, t=100 if m <= 6 else 300) for m in range(1, 13)]
    linhas += [linha(2025, m, t=10_000) for m in range(1, 4)]  # ano parcial: fica de fora
    saz = transform.sazonalidade(base(linhas))
    k = saz[saz["grupo"] == "K"].set_index("mes")
    assert k.loc[1, "toneladas_media"] == pytest.approx(100)
    assert k.loc[12, "toneladas_media"] == pytest.approx(300)
    assert k["indice_sazonal"].mean() == pytest.approx(100)
    assert (saz["n_anos"] == 1).all() and (saz["ano_final"] == 2024).all()
    n = saz[saz["grupo"] == "N"]  # grupo sem importação entra com zero
    assert len(n) == 12 and (n["toneladas_media"] == 0).all()


# --- Rotas origem -> URF ---------------------------------------------------------------------


def test_rotas_frete_ponderado_share_e_meses_com_carga():
    b = base(
        [
            linha(2025, 1, t=40_000, frete=800_000),  # Canadá -> Paranaguá: 20 US$/t
            linha(2025, 2, t=20_000, frete=800_000),  # 40 US$/t
            linha(2025, 3, pais=RUSSIA, t=40_000, frete=1_200_000, urf=SANTOS),  # 30 US$/t, abaixo do corte
            *[linha(2025, m, t=0, frete=0) for m in range(4, 13)],  # meses sem carga não contam
        ]
    )
    r = transform.rotas_anuais(b)
    k = r[r["grupo"] == "K"].set_index("no_pais")
    assert k.loc["Canadá", "toneladas"] == 60_000 and k.loc["Canadá", "meses"] == 2
    assert k.loc["Canadá", "frete_usd_t"] == pytest.approx(1_600_000 / 60_000)  # não a média de 20 e 40
    assert k.loc["Canadá", "share_t_pct"] == pytest.approx(60)
    assert bool(k.loc["Canadá", "relevante"]) and not bool(k.loc["Rússia", "relevante"])
    assert r.groupby(["ano", "grupo"])["share_t_pct"].sum().to_numpy() == pytest.approx(100)
    assert set(r["grupo"]) == {"K", "TOTAL"}


def test_corte_de_rota_e_proporcional_no_ano_parcial():
    b = base([linha(2026, m, t=4_500) for m in range(1, 10)])  # 40.500 t em jan-set
    r = transform.rotas_anuais(b)
    assert r["corte_t"].iloc[0] == 37_500 and r["relevante"].all()


def test_bloco_de_rotas_agrupa_o_resto_em_outros(monkeypatch):
    monkeypatch.setattr(transform, "TOP_SANKEY", 1)
    monkeypatch.setattr(transform, "TOP_ROTAS", 2)
    b = base(
        [
            linha(2025, 1, pais=CANADA, t=300_000, frete=6_000_000),
            linha(2025, 1, pais=RUSSIA, t=200_000, frete=6_000_000, urf=SANTOS),
            linha(2025, 1, pais=CHINA, t=100_000, frete=1_000_000, urf=SANTOS),
            linha(2025, 1, pais=PAIS_X, t=1_000, frete=500_000),
        ]
    )
    r = transform.rotas_anuais(b)
    bloco = transform._bloco_rotas(r[(r["ano"] == 2025) & (r["grupo"] == "K")])
    assert bloco["origens"] == [["149", "Canadá", 300_000], [None, "Outros", 301_000]]
    assert bloco["urfs"] == [["0917800", "PORTO DE PARANAGUA", 301_000], [None, "Outros", 300_000]]
    # Canadá -> Paranaguá; Outros (País X) -> Paranaguá; Outros (Rússia e China) -> Outros (Santos)
    assert bloco["fluxos"] == [[0, 0, 300_000, 20.0], [1, 0, 1_000, 500.0], [1, 1, 300_000, 23.3]]
    assert sum(f[2] for f in bloco["fluxos"]) == 601_000
    # só rotas acima do corte, das maiores para as menores, até TOP_ROTAS
    assert [(x[0], x[3]) for x in bloco["rotas"]] == [("Canadá", 300_000), ("Rússia", 200_000)]
    assert bloco["n_rotas"] == 4 and bloco["n_relevantes"] == 3
    assert bloco["share_relevantes_pct"] == pytest.approx(99.8, abs=0.05)
    assert bloco["frete_relevantes"] == pytest.approx(13_000_000 / 600_000, abs=0.05)


# --- Base, gravação e dashboard.json --------------------------------------------------------


def test_carregar_base_junta_o_grupo_e_converte_para_toneladas(tmp_path):
    ncm = tmp_path / "ncm.csv"
    ingest.salvar_ncms(
        pd.DataFrame({"ncm": ["31042090"], "descricao": ["KCl"], "grupo": ["K"], "sh4": ["3104"], "sh6": ["310420"]}),
        ncm,
    )
    bruto = pd.DataFrame([{k: v for k, v in linha(2025, 1, t=2).items() if k in ["ano", *ingest.COLUNAS_RAW]}])
    ingest.gravar_ano(bruto, 2025, tmp_path / "raw")
    b = transform.carregar_base(tmp_path / "raw", ncm)
    assert b.loc[0, "grupo"] == "K" and b.loc[0, "toneladas"] == pytest.approx(2)

    ingest.salvar_ncms(
        pd.DataFrame({"ncm": ["31021010"], "descricao": ["Ureia"], "grupo": ["N"], "sh4": ["3102"], "sh6": ["310210"]}),
        ncm,
    )
    with pytest.raises(ValueError, match="sem grupo"):
        transform.carregar_base(tmp_path / "raw", ncm)


def test_gerar_grava_as_tabelas_e_um_dashboard_json_valido(tmp_path, monkeypatch):
    monkeypatch.setattr(transform, "TOP_DASHBOARD", 1)  # força a linha "Outros"
    tabelas = transform.gerar(dois_anos(), tmp_path)

    for nome in tabelas:
        assert (tmp_path / f"{nome}.csv").exists()
    painel = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
    assert painel["meta"]["fonte"] == "Comex Stat/MDIC"
    assert painel["meta"]["periodo"] == {"inicio": "2024-01", "fim": "2025-12"}
    # números do hero do dashboard: NCMs com importação nos dados e os monitorados na tabela
    assert painel["meta"]["ncms"]["com_importacao"] == dois_anos()["co_ncm"].nunique()
    assert painel["meta"]["ncms"]["monitorados"] == len(pd.read_csv(config.NCM_CSV, dtype=str))
    assert len(painel["mensal"]["periodos"]) == 24 and len(painel["mensal"]["K"]["preco_fob"]) == 24
    # valores somáveis por mês: o dashboard calcula US$/t de qualquer recorte a partir deles
    for grupo in [*transform.GRUPOS, transform.TOTAL]:
        assert {"t", "fob_musd", "cif_musd", "frete_musd"} <= set(painel["mensal"][grupo])
    k = painel["mensal"]["K"]
    assert k["frete_musd"][0] * 1e6 / k["t"][0] == pytest.approx(k["frete"][0], abs=0.1)
    assert painel["anual"]["anos"] == [2024, 2025] and painel["anual"]["n_meses"] == [12, 12]
    ranking = painel["origens"]["dados"]["2025"]["TOTAL"]
    assert [linha_[1] for linha_ in ranking] == ["Rússia", "Outros"]
    assert sum(linha_[3] for linha_ in ranking) == pytest.approx(100, abs=0.05)
    rotas = painel["rotas"]
    assert rotas["corte_t"] == transform.CORTE_ROTA_T and set(rotas["dados"]) == {"2024", "2025"}
    assert set(rotas["dados"]["2025"]) == {"N", "K", "TOTAL"}
    total = rotas["dados"]["2025"]["TOTAL"]
    assert sum(f[2] for f in total["fluxos"]) == painel["anual"]["TOTAL"]["t"][1]


def test_dados_versionados_shares_somam_100(tmp_path):
    if not any(config.RAW_DIR.glob(f"ano=*/{config.RAW_ARQUIVO}")):
        pytest.skip("sem dados em data/raw")
    tabelas = transform.gerar(transform.carregar_base(), tmp_path)
    for nome in ["share_origem_ano", "share_urf_ano"]:
        somas = tabelas[nome].groupby(["ano", "grupo"])["share_t_pct"].sum()
        assert somas.to_numpy() == pytest.approx(100)
    assert tabelas["hhi_ano_grupo"]["hhi"].between(0, 10_000).all()
    rotas = tabelas["rotas_ano"]
    assert rotas.groupby(["ano", "grupo"])["share_t_pct"].sum().to_numpy() == pytest.approx(100)
    totais = rotas.groupby(["ano", "grupo"])["toneladas"].sum()
    origens = tabelas["share_origem_ano"].groupby(["ano", "grupo"])["toneladas"].sum()
    assert totais.to_numpy() == pytest.approx(origens.reindex(totais.index).to_numpy())
