"""Testes da comparação Brasil x mercado internacional (src/comparacao.py). Sem rede."""

import json

import pandas as pd
import pytest

from src import comparacao, config, ingest, transform


def importacao(ano, mes, ncm, t, fob, cif, grupo="K"):
    """Uma linha da base do transform (valores em US$ e toneladas)."""
    return {
        "ano": ano, "mes": mes, "co_ncm": ncm, "grupo": grupo, "co_pais": "149", "no_pais": "Canadá",
        "sg_uf": "PR", "no_uf": "Paraná", "co_via": "01", "no_via": "MARITIMA", "co_urf": "0917800",
        "no_urf": "PORTO DE PARANAGUA", "toneladas": float(t), "kg_liquido": t * 1000.0, "vl_fob": float(fob),
        "vl_frete": float(cif - fob), "vl_seguro": 0.0, "vl_cif": float(cif),
    }


def pink(**series):
    """{mes: {serie: valor}} -> DataFrame no formato de benchmark.ler_pink()."""
    meses = sorted({m for valores in series.values() for m in valores})
    return pd.DataFrame(
        {"mes": meses, **{s: [series.get(s, {}).get(m) for m in meses] for s in ["ureia", "dap", "tsp", "kcl", "rocha_fosfatica"]}}
    )


PTAX = pd.DataFrame({"mes": ["2019-12", "2020-01", "2022-02", "2022-03"], "brl_por_usd": [4.1, 4.15, 5.2, 5.0]})


@pytest.fixture
def kcl_na_quebra():
    base = pd.DataFrame(
        [
            importacao(2019, 12, "31042090", 100_000, 30_000_000, 33_000_000),  # FOB 300, CIF 330
            importacao(2020, 1, "31042090", 100_000, 29_000_000, 32_000_000),  # FOB 290, CIF 320
        ]
    )
    return comparacao.comparar(base, pink(kcl={"2019-12": 280.0, "2020-01": 300.0}), PTAX)


def linha(df, produto, periodo):
    return df[(df["produto"] == produto) & (df["periodo"] == periodo)].iloc[0]


def test_ncms_da_tabela_existem_e_sao_do_grupo_do_produto():
    ncms = ingest.carregar_ncms(config.NCM_CSV).set_index("ncm")["grupo"]
    for chave, p in comparacao.PRODUTOS.items():
        for ncm in p["ncms"]:
            assert ncms.get(ncm) == p["grupo"], (chave, ncm)
        assert p["serie"] in config.PINK_SERIES.values() and p["serie"] in comparacao.BASES
    assert comparacao.PRODUTOS["map"]["serie"] == "dap" and comparacao.PRODUTOS["map"]["proxy"]


def test_kcl_e_fob_x_fob_ate_2019_e_cif_x_cfr_desde_2020(kcl_na_quebra):
    dez, jan = linha(kcl_na_quebra, "kcl", "2019-12"), linha(kcl_na_quebra, "kcl", "2020-01")
    assert (dez["comparacao"], dez["base_bm"], dez["quebra"]) == ("FOB×FOB", "FOB Vancouver", False)
    assert (jan["comparacao"], jan["base_bm"], jan["quebra"]) == ("CIF×CFR", "CFR Brasil (granulado)", True)
    # FOB×FOB: spread FOB = 300 − 280; custo posto = 330 − 280
    assert dez["spread_fob_usd_t"] == pytest.approx(20) and dez["spread_fob_pct"] == pytest.approx(100 * 20 / 280)
    assert dez["custo_posto_usd_t"] == pytest.approx(50) and dez["spread_usd_t"] == pytest.approx(20)
    # CIF×CFR: não há spread FOB (o benchmark já está no destino); o comparável é CIF − CFR
    assert pd.isna(jan["spread_fob_usd_t"]) and pd.isna(jan["spread_fob_pct"])
    assert jan["custo_posto_usd_t"] == pytest.approx(20) and jan["spread_usd_t"] == pytest.approx(20)
    assert jan["spread_pct"] == pytest.approx(100 * 20 / 300)


def test_cif_em_reais_pela_ptax_do_mes(kcl_na_quebra):
    dez = linha(kcl_na_quebra, "kcl", "2019-12")
    assert dez["ptax_brl_usd"] == 4.1 and dez["cif_brl_t"] == pytest.approx(330 * 4.1)
    # a relação de troca é US$/US$: não existe "troca em reais"
    assert not any("troca" in c for c in comparacao.COLUNAS)


def test_ureia_muda_de_base_em_marco_de_2022_e_segue_fob_x_fob():
    base = pd.DataFrame(
        [importacao(2022, m, "31021010", 50_000, 30_000_000, 32_000_000, "N") for m in (2, 3)]
    )
    df = comparacao.comparar(base, pink(ureia={"2022-02": 744.2, "2022-03": 872.5}), PTAX)
    fev, mar = linha(df, "ureia", "2022-02"), linha(df, "ureia", "2022-03")
    assert (fev["base_bm"], mar["base_bm"], mar["quebra"]) == ("FOB Mar Negro", "FOB Oriente Médio", True)
    assert fev["comparacao"] == mar["comparacao"] == "FOB×FOB"


def test_map_usa_o_dap_e_dap_soma_os_ncms_desdobrados():
    base = pd.DataFrame(
        [
            importacao(2019, 1, "31054000", 200_000, 80_000_000, 86_000_000, "P"),
            importacao(2019, 1, "31053010", 6_000, 2_400_000, 2_600_000, "P"),
            importacao(2019, 1, "31053090", 3_000, 1_200_000, 1_300_000, "P"),
            importacao(2019, 1, "31031100", 20_000, 6_000_000, 6_400_000, "P"),
        ]
    )
    df = comparacao.comparar(base, pink(dap={"2019-01": 380.0}, tsp={"2019-01": 310.0}), PTAX)
    map_, dap, tsp = (linha(df, p, "2019-01") for p in ("map", "dap", "tsp"))
    assert map_["serie_bm"] == "dap" and map_["proxy"] and map_["benchmark_usd_t"] == 380.0
    assert dap["toneladas"] == 9_000 and dap["fob_brasil_usd_t"] == pytest.approx(3_600_000 / 9_000)
    assert dap["amostra_pequena"] and not map_["amostra_pequena"]
    assert tsp["comparacao"] == "CIF×CFR" and pd.isna(tsp["spread_fob_usd_t"])
    assert tsp["spread_usd_t"] == pytest.approx(320 - 310)


def test_corte_de_amostra_pequena_e_mes_sem_importacao():
    base = pd.DataFrame(
        [
            importacao(2019, 1, "31042090", comparacao.CORTE_AMOSTRA_T - 1, 3_000_000, 3_200_000),
            importacao(2019, 2, "31042090", comparacao.CORTE_AMOSTRA_T, 3_000_000, 3_200_000),
            importacao(2019, 3, "31021010", 50_000, 15_000_000, 16_000_000, "N"),  # mês sem KCl
        ]
    )
    df = comparacao.comparar(base, pink(kcl={"2019-01": 300.0, "2019-02": 300.0, "2019-03": 300.0}), PTAX)
    assert linha(df, "kcl", "2019-01")["amostra_pequena"] and not linha(df, "kcl", "2019-02")["amostra_pequena"]
    sem = linha(df, "kcl", "2019-03")
    assert sem["toneladas"] == 0 and not sem["amostra_pequena"] and pd.isna(sem["fob_brasil_usd_t"]) and pd.isna(sem["spread_pct"])


def test_mes_sem_benchmark_ou_sem_ptax_fica_vazio():
    base = pd.DataFrame([importacao(2026, 9, "31042090", 100_000, 35_000_000, 39_000_000)])
    df = comparacao.comparar(base, pink(kcl={"2026-08": 386.9}), PTAX)
    k = linha(df, "kcl", "2026-09")
    assert pd.isna(k["benchmark_usd_t"]) and pd.isna(k["spread_pct"]) and pd.isna(k["ptax_brl_usd"]) and pd.isna(k["cif_brl_t"])
    assert k["cif_brasil_usd_t"] == pytest.approx(390)


def test_quebras_dentro_do_periodo():
    assert comparacao.quebras(["2015-01", "2026-09"]) == [
        {"produto": "kcl", "periodo": "2020-01", "de": "FOB Vancouver", "para": "CFR Brasil (granulado)", "comparacao": "CIF×CFR"},
        {"produto": "ureia", "periodo": "2022-03", "de": "FOB Mar Negro", "para": "FOB Oriente Médio", "comparacao": "FOB×FOB"},
    ]
    assert comparacao.quebras(["2023-01", "2026-09"]) == []


def _gerar(destino, base, pink_df, ptax_df):
    return transform.gerar(base, destino, None, pink_df, ptax_df, {"atualizado_em": "2026-10-02"})


def test_transform_grava_csv_e_bloco_do_dashboard_deterministicos(tmp_path):
    base = pd.DataFrame(
        [importacao(2019, m, "31042090", 100_000 + m, 30_000_000, 33_000_000) for m in (11, 12)]
        + [importacao(2020, 1, "31042090", 90_000, 29_000_000, 32_000_000)]
    )
    p = pink(kcl={"2019-11": 270.0, "2019-12": 280.0, "2020-01": 300.0}, dap={"2019-11": 300.0, "2019-12": 300.0, "2020-01": 300.0})
    _gerar(tmp_path / "a", base, p, PTAX)
    _gerar(tmp_path / "b", base, p, PTAX)
    for arquivo in ["benchmark_mensal.csv", "dashboard.json"]:
        assert (tmp_path / "a" / arquivo).read_bytes() == (tmp_path / "b" / arquivo).read_bytes(), arquivo
    csv = pd.read_csv(tmp_path / "a" / "benchmark_mensal.csv", dtype={"ncms": str})
    assert list(csv.columns) == comparacao.COLUNAS and len(csv) == 3 * len(comparacao.PRODUTOS)
    bloco = json.loads((tmp_path / "a" / "dashboard.json").read_text(encoding="utf-8"))["benchmark"]
    assert bloco["periodos"] == ["2019-11", "2019-12", "2020-01"]
    k = bloco["produtos"]["kcl"]
    assert all(len(k[c]) == 3 for c in ["t", "fob", "cif", "bm", "spread_pct", "spread_usd_t", "custo_posto_usd_t", "cif_brl_t", "amostra_pequena"])
    assert k["bases"] == [
        {"desde": "2019-11", "base": "FOB Vancouver", "comparacao": "FOB×FOB"},
        {"desde": "2020-01", "base": "CFR Brasil (granulado)", "comparacao": "CIF×CFR"},
    ]
    assert bloco["quebras"][0]["periodo"] == "2020-01" and bloco["pink_atualizado_em"] == "2026-10-02"
    assert bloco["ptax"][0] is None and bloco["ptax"][1] == 4.1  # sem PTAX de 2019-11
    assert "Comex Stat/MDIC" in bloco["fonte"] and "Banco Mundial" in bloco["fonte"] and "BCB" in bloco["fonte"]
    assert any("ágio" in n for n in bloco["notas"]) and any("DAP" in n for n in bloco["notas"])


def test_transform_sem_pink_sheet_nao_cria_o_bloco(tmp_path):
    base = pd.DataFrame([importacao(2019, 12, "31042090", 100_000, 30_000_000, 33_000_000)])
    transform.gerar(base, tmp_path)
    assert not (tmp_path / "benchmark_mensal.csv").exists()
    assert "benchmark" not in json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))


def test_dados_versionados_tem_as_quebras_certas():
    """Com os dados do repositório (se já houver Pink Sheet gravada), as invariantes valem."""
    arquivo = config.PROCESSED_DIR / "benchmark_mensal.csv"
    if not arquivo.exists():
        pytest.skip("benchmark_mensal.csv ainda não foi gerado")
    df = pd.read_csv(arquivo, dtype={"ncms": str, "periodo": str})
    assert set(df["produto"]) == set(comparacao.PRODUTOS)
    assert set(map(tuple, df[df["quebra"]][["periodo", "produto"]].values)) <= {("2020-01", "kcl"), ("2022-03", "ureia")}
    fob = df["comparacao"] == "FOB×FOB"
    assert df.loc[~fob, "spread_fob_usd_t"].isna().all()
    assert (df.loc[~fob, "spread_usd_t"] - df.loc[~fob, "custo_posto_usd_t"]).abs().max() < 0.01 or df.loc[~fob, "spread_usd_t"].isna().all()
