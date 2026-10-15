"""Custo por nutriente ("custo por molécula"): quanto custa, no porto, a tonelada de N, de P2O5
e de K2O em cada fertilizante importado.

    custo do nutriente (US$/t)   = CIF do produto (US$/t) ÷ teor do nutriente principal
    MAP e DAP, P2O5 com crédito  = (CIF − teor de N × custo do N da ureia no mês) ÷ teor de P2O5
                                   (o N que vem junto vale o que custaria comprado na ureia)
    sem crédito (MAP e DAP)      = CIF ÷ teor de P2O5 (todo o preço vai para o P2O5)
    custo por ponto (US$/t)      = CIF ÷ (N + P2O5 + K2O), a tonelada de qualquer nutriente
    em reais (R$/t)              = custo em US$ × PTAX venda média do mês (BCB, SGS 3698)

Teores: o grau comercial usual de cada produto (PRODUTOS), porque o Comex Stat não traz o teor
de cada carga. Um teor diferente muda o custo na mesma proporção (ureia com 45% em vez de 46%
de N: custo do N 2,2% maior). O sulfato de amônio também leva enxofre (cerca de 24% de S), que
não é descontado. Meses com menos de CORTE_AMOSTRA_T toneladas do produto ficam marcados como
amostra pequena: um navio decide o preço, e o mês fica fora dos gráficos.

É o custo no porto (CIF: produto, frete e seguro até o Brasil), antes de impostos, frete
interno e margens: não é o preço ao produtor.

Fonte: Comex Stat/MDIC; BCB/PTAX.
"""

from __future__ import annotations

import pandas as pd

from .comparacao import CORTE_AMOSTRA_T

# Produto -> NCMs (os mesmos de src/comparacao.py quando o produto se repete) e teores em % do
# peso, no grau comercial usual: ureia 46-00-00, sulfato de amônio 21-00-00, nitrato de amônio
# 34-00-00, MAP 11-52-00, DAP 18-46-00, TSP 00-46-00 e KCl 00-00-60.
PRODUTOS = {
    "ureia": {"nome": "Ureia", "ncms": ["31021010"], "nutriente": "N", "teores": {"N": 46}},
    "sulfato_amonio": {"nome": "Sulfato de amônio", "ncms": ["31022100"], "nutriente": "N", "teores": {"N": 21}},
    "nitrato_amonio": {"nome": "Nitrato de amônio", "ncms": ["31023000"], "nutriente": "N", "teores": {"N": 34}},
    "map": {"nome": "MAP", "ncms": ["31054000"], "nutriente": "P2O5", "teores": {"N": 11, "P2O5": 52}},
    "dap": {"nome": "DAP", "ncms": ["31053000", "31053010", "31053090"], "nutriente": "P2O5", "teores": {"N": 18, "P2O5": 46}},
    "tsp": {"nome": "TSP", "ncms": ["31031030", "31031100"], "nutriente": "P2O5", "teores": {"P2O5": 46}},
    "kcl": {"nome": "KCl", "ncms": ["31042090"], "nutriente": "K2O", "teores": {"K2O": 60}},
}
NUTRIENTES = {"N": "N", "P2O5": "P₂O₅", "K2O": "K₂O"}
REFERENCIA_N = "ureia"  # o crédito de N do MAP e do DAP usa o custo do N da ureia no mesmo mês

COLUNAS = [
    "periodo",
    "ano",
    "mes",
    "produto",
    "nutriente",
    "ncms",
    "teor_n_pct",
    "teor_p2o5_pct",
    "teor_k2o_pct",
    "toneladas",
    "amostra_pequena",
    "cif_usd_t",
    "custo_sem_credito_usd_t",
    "custo_n_ureia_usd_t",
    "credito_n_usd_t",
    "custo_nutriente_usd_t",
    "custo_ponto_usd_t",
    "ptax_brl_usd",
    "custo_nutriente_brl_t",
]


def custo_mensal(base: pd.DataFrame, ptax: pd.DataFrame | None = None) -> pd.DataFrame:
    """Uma linha por mês × produto, de todos os meses das importações.

    `base`: importações (transform.carregar_base: ano, mes, co_ncm, toneladas, vl_cif);
    `ptax`: benchmark.ler_ptax() (mes e brl_por_usd). Sem PTAX, o custo em reais fica vazio.
    """
    ncm_produto = {ncm: chave for chave, p in PRODUTOS.items() for ncm in p["ncms"]}
    imp = base[base["co_ncm"].isin(ncm_produto)].assign(produto=lambda d: d["co_ncm"].map(ncm_produto))
    imp = imp.groupby(["ano", "mes", "produto"], as_index=False)[["toneladas", "vl_cif"]].sum()

    meses = base[["ano", "mes"]].drop_duplicates()
    df = meses.merge(pd.DataFrame({"produto": list(PRODUTOS)}), how="cross").merge(imp, on=["ano", "mes", "produto"], how="left")
    df["toneladas"] = df["toneladas"].fillna(0.0)
    df["periodo"] = df["ano"].astype(int).astype(str) + "-" + df["mes"].astype(int).astype(str).str.zfill(2)

    info = pd.DataFrame.from_dict(PRODUTOS, orient="index")
    df["nutriente"] = df["produto"].map(info["nutriente"])
    df["ncms"] = df["produto"].map(info["ncms"].map("|".join))
    for chave, coluna in (("N", "teor_n_pct"), ("P2O5", "teor_p2o5_pct"), ("K2O", "teor_k2o_pct")):
        df[coluna] = df["produto"].map(lambda p, c=chave: float(PRODUTOS[p]["teores"].get(c, 0)))
    principal = df.apply(lambda r: PRODUTOS[r["produto"]]["teores"][r["nutriente"]], axis=1).astype("float64") / 100
    pontos = (df["teor_n_pct"] + df["teor_p2o5_pct"] + df["teor_k2o_pct"]) / 100

    com_volume = df["toneladas"] > 0
    df["cif_usd_t"] = (df["vl_cif"].astype("float64") / df["toneladas"]).where(com_volume)
    df["amostra_pequena"] = com_volume & (df["toneladas"] < CORTE_AMOSTRA_T)
    df["custo_sem_credito_usd_t"] = df["cif_usd_t"] / principal
    df["custo_ponto_usd_t"] = df["cif_usd_t"] / pontos

    # Crédito de N (MAP e DAP): o N do produto vale o custo do N da ureia no mesmo mês.
    n_ureia = df[df["produto"] == REFERENCIA_N].set_index(["ano", "mes"])["custo_sem_credito_usd_t"]
    df["custo_n_ureia_usd_t"] = pd.MultiIndex.from_frame(df[["ano", "mes"]]).map(n_ureia.to_dict().get).astype("float64")
    com_credito = (df["nutriente"] != "N") & (df["teor_n_pct"] > 0)
    df["credito_n_usd_t"] = (df["teor_n_pct"] / 100 * df["custo_n_ureia_usd_t"]).where(com_credito)
    df["custo_nutriente_usd_t"] = ((df["cif_usd_t"] - df["credito_n_usd_t"]) / principal).where(com_credito, df["custo_sem_credito_usd_t"])

    if ptax is not None and not ptax.empty:
        cambio = ptax.rename(columns={"mes": "periodo", "brl_por_usd": "ptax_brl_usd"})[["periodo", "ptax_brl_usd"]]
        cambio["ptax_brl_usd"] = pd.to_numeric(cambio["ptax_brl_usd"], errors="coerce").astype("float64")
        df = df.merge(cambio, on="periodo", how="left")
    else:
        df["ptax_brl_usd"] = float("nan")
    df["custo_nutriente_brl_t"] = df["custo_nutriente_usd_t"] * df["ptax_brl_usd"]

    ordem = {chave: i for i, chave in enumerate(PRODUTOS)}
    df = df.assign(_ordem=df["produto"].map(ordem)).sort_values(["ano", "mes", "_ordem"], kind="mergesort")
    return df[COLUNAS].reset_index(drop=True)


def bloco_dashboard(df: pd.DataFrame, periodos: list[str], num) -> dict:
    """Bloco "nutrientes" do dashboard.json, alinhado aos períodos da série mensal.

    Seis casas nos custos: o dashboard arredonda uma vez só, na exibição.
    """
    produtos = {}
    for chave, info in PRODUTOS.items():
        p = df[df["produto"] == chave].set_index("periodo").reindex(periodos)
        credito = info["nutriente"] != "N" and info["teores"].get("N", 0) > 0
        produtos[chave] = {
            "nome": info["nome"],
            "nutriente": info["nutriente"],
            "teores": info["teores"],
            "ncms": info["ncms"],
            "credito_n": credito,
            "t": [num(v, 0) for v in p["toneladas"]],
            "cif": [num(v, 6) for v in p["cif_usd_t"]],
            "custo": [num(v, 6) for v in p["custo_nutriente_usd_t"]],
            "custo_brl": [num(v, 6) for v in p["custo_nutriente_brl_t"]],
            "amostra_pequena": [bool(v) if pd.notna(v) else False for v in p["amostra_pequena"]],
        }
        if credito:  # nos produtos de um nutriente só, os dois são iguais ao custo
            produtos[chave]["custo_sem_credito"] = [num(v, 6) for v in p["custo_sem_credito_usd_t"]]
            produtos[chave]["custo_ponto"] = [num(v, 6) for v in p["custo_ponto_usd_t"]]
    ptax = df.drop_duplicates("periodo").set_index("periodo").reindex(periodos)["ptax_brl_usd"]
    return {
        "periodos": periodos,
        "nutrientes": NUTRIENTES,
        "produtos": produtos,
        "ptax": [num(v, 4) for v in ptax],
        "referencia_credito": REFERENCIA_N,
        "corte_amostra_t": CORTE_AMOSTRA_T,
        "fonte": "Comex Stat/MDIC; BCB/PTAX (SGS 3698, ODbL)",
        "notas": [
            "Custo do nutriente = preço CIF do produto ÷ teor do nutriente principal, no grau comercial usual"
            " (ureia 46% de N, sulfato de amônio 21% de N, nitrato de amônio 34% de N, MAP 11-52, DAP 18-46,"
            " TSP 46% de P2O5, KCl 60% de K2O). O Comex Stat não traz o teor de cada carga.",
            "MAP e DAP: o N que vem junto é descontado pelo custo do N da ureia no mesmo mês (crédito de N),"
            " e o resto do preço vai para o P2O5.",
            "O sulfato de amônio também leva enxofre (cerca de 24% de S), que não é descontado.",
            "É o custo no porto (CIF: produto, frete e seguro), antes de impostos, frete interno e margens:"
            " não é o preço ao produtor.",
            f"Meses com menos de {CORTE_AMOSTRA_T // 1000} mil t do produto: amostra pequena, fora dos gráficos.",
            "Em reais pela PTAX venda média do mês, e não pelo câmbio da data de cada declaração.",
        ],
    }
