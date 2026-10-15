"""Testes do custo por nutriente ("custo por molécula", src/nutrientes.py) e da visão por estado
(share por UF no transform): sem rede."""

import json

import pandas as pd
import pytest

from src import config, nutrientes, transform
from tests.test_transform import linha


def _produto(ano, mes, ncm, t, cif, uf=("PR", "Paraná")):
    r = linha(ano, mes, t=t, fob=cif * t * 1000, frete=0.0)  # vl_* em US$; t em toneladas
    r.update({"co_ncm": ncm, "vl_cif": float(cif * t * 1000), "vl_fob": float(cif * t * 1000), "sg_uf": uf[0], "no_uf": uf[1]})
    return r


@pytest.fixture
def mes_sintetico():
    # 2025-01: ureia a US$ 460/t (US$ 1.000 por t de N), MAP a US$ 700/t, KCl a US$ 360/t,
    # sulfato de amônio com só 5 mil t (amostra pequena) e TSP sem importação.
    linhas = [
        _produto(2025, 1, "31021010", 20.0, 460.0),
        _produto(2025, 1, "31054000", 20.0, 700.0),
        _produto(2025, 1, "31042090", 20.0, 360.0),
        _produto(2025, 1, "31022100", 5.0, 210.0),
    ]
    base = pd.DataFrame(linhas)
    base["toneladas"] = base["toneladas"] * 1000  # 20 mil t por produto (5 mil t de sulfato)
    ptax = pd.DataFrame({"mes": ["2025-01"], "brl_por_usd": [5.0]})
    return nutrientes.custo_mensal(base, ptax).set_index("produto")


def test_custo_do_nutriente_e_cif_dividido_pelo_teor(mes_sintetico):
    d = mes_sintetico
    assert d.loc["ureia", "cif_usd_t"] == pytest.approx(460)
    assert d.loc["ureia", "custo_nutriente_usd_t"] == pytest.approx(1000)  # 460 ÷ 0,46
    assert d.loc["kcl", "custo_nutriente_usd_t"] == pytest.approx(600)  # 360 ÷ 0,60
    assert d.loc["kcl", "custo_nutriente_brl_t"] == pytest.approx(3000)  # × PTAX 5,0
    assert d.loc["ureia", "nutriente"] == "N" and d.loc["kcl", "nutriente"] == "K2O" and d.loc["map", "nutriente"] == "P2O5"


def test_map_com_credito_de_n_pela_ureia_do_mes(mes_sintetico):
    m = mes_sintetico.loc["map"]
    assert m["custo_n_ureia_usd_t"] == pytest.approx(1000)
    assert m["credito_n_usd_t"] == pytest.approx(110)  # 11% de N × US$ 1.000 por t de N
    assert m["custo_nutriente_usd_t"] == pytest.approx((700 - 110) / 0.52)
    assert m["custo_sem_credito_usd_t"] == pytest.approx(700 / 0.52)
    assert m["custo_ponto_usd_t"] == pytest.approx(700 / 0.63)  # 11 + 52 pontos
    # produto de um nutriente só não tem crédito: o custo é o simples
    assert pd.isna(mes_sintetico.loc["kcl", "credito_n_usd_t"])
    assert mes_sintetico.loc["kcl", "custo_ponto_usd_t"] == pytest.approx(600)


def test_amostra_pequena_e_mes_sem_importacao(mes_sintetico):
    d = mes_sintetico
    assert bool(d.loc["sulfato_amonio", "amostra_pequena"]) and not bool(d.loc["ureia", "amostra_pequena"])
    assert d.loc["sulfato_amonio", "custo_nutriente_usd_t"] == pytest.approx(1000)  # 210 ÷ 0,21: calculado, mas marcado
    assert d.loc["tsp", "toneladas"] == 0 and pd.isna(d.loc["tsp", "cif_usd_t"]) and pd.isna(d.loc["tsp", "custo_nutriente_usd_t"])
    assert not bool(d.loc["tsp", "amostra_pequena"])


def test_sem_ptax_o_custo_em_reais_fica_vazio(mes_sintetico):
    base = pd.DataFrame([_produto(2025, 1, "31042090", 20.0, 360.0)])
    base["toneladas"] = base["toneladas"] * 1000
    d = nutrientes.custo_mensal(base).set_index("produto")
    assert d.loc["kcl", "custo_nutriente_usd_t"] == pytest.approx(600) and pd.isna(d.loc["kcl", "custo_nutriente_brl_t"])


def test_bloco_do_dashboard(mes_sintetico):
    df = mes_sintetico.reset_index()
    bloco = nutrientes.bloco_dashboard(df, ["2025-01"], transform._num)
    assert set(bloco["produtos"]) == set(nutrientes.PRODUTOS)
    m, u = bloco["produtos"]["map"], bloco["produtos"]["ureia"]
    assert m["credito_n"] and not u["credito_n"]
    assert m["custo"][0] == pytest.approx(590 / 0.52, abs=1e-6) and m["custo_sem_credito"][0] == pytest.approx(700 / 0.52, abs=1e-6)
    assert "custo_sem_credito" not in u and "custo_ponto" not in u  # iguais ao custo nos produtos de um nutriente
    assert bloco["produtos"]["sulfato_amonio"]["amostra_pequena"] == [True]
    assert bloco["ptax"] == [5.0] and "Comex Stat/MDIC" in bloco["fonte"] and "BCB/PTAX" in bloco["fonte"]


# --- Dados versionados ---------------------------------------------------------------------------


def _painel():
    return json.loads((config.PROCESSED_DIR / "dashboard.json").read_text(encoding="utf-8"))


def test_custo_por_nutriente_versionado_bate_com_o_cif():
    df = pd.read_csv(config.PROCESSED_DIR / "custo_nutriente_mensal.csv")
    assert set(df["produto"]) == set(nutrientes.PRODUTOS)
    ok = df.dropna(subset=["custo_nutriente_usd_t"])
    simples = ok[ok["credito_n_usd_t"].isna()]
    teor = simples.apply(lambda r: nutrientes.PRODUTOS[r["produto"]]["teores"][r["nutriente"]], axis=1) / 100
    # o CSV guarda 2 casas: o CIF arredondado ÷ 0,21 amplia o erro para até ~0,03
    assert (simples["custo_nutriente_usd_t"] - simples["cif_usd_t"] / teor).abs().max() < 0.05
    credito = ok[ok["credito_n_usd_t"].notna()]
    assert set(credito["produto"]) <= {"map", "dap"}
    assert (credito["custo_nutriente_usd_t"] < credito["custo_sem_credito_usd_t"]).all(), "o crédito de N baixa o custo do P2O5"
    painel = _painel()["nutrientes"]
    assert painel["periodos"] == _painel()["mensal"]["periodos"]
    assert all(len(p["custo"]) == len(painel["periodos"]) for p in painel["produtos"].values())


def test_estados_versionados_batem_com_o_total_de_cada_ano():
    painel = _painel()
    estados, anual = painel["estados"], painel["anual"]
    assert {"PR", "RS", "MT", "SP"} <= set(estados["ufs"]) and estados["ufs"]["PR"] == "Paraná"
    share = pd.read_csv(config.PROCESSED_DIR / "share_uf_ano.csv")
    for i, ano in enumerate(anual["anos"]):
        for grupo, linhas in estados["dados"][str(ano)].items():
            total = anual[grupo]["t"][i]
            assert sum(x[1] for x in linhas) == pytest.approx(total, abs=len(linhas) + 1)
            assert [x[1] for x in linhas] == sorted((x[1] for x in linhas), reverse=True)
            s = share[(share["ano"] == ano) & (share["grupo"] == grupo)].set_index("sg_uf")
            for uf, t, share_pct, var, t_ant in linhas:
                if var is not None:
                    assert var == pytest.approx(100 * (s.loc[uf, "toneladas"] / s.loc[uf, "toneladas_ano_anterior"] - 1), abs=0.06)
                    assert t_ant == pytest.approx(s.loc[uf, "toneladas_ano_anterior"], abs=1)
