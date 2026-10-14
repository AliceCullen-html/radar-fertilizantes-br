"""Fase 6: Brasil x mercado internacional.

Para cada mês e produto, compara o preço implícito das importações brasileiras (Comex Stat)
com a referência internacional do Banco Mundial (Pink Sheet) e converte o CIF para reais pela
PTAX média do mês (BCB, SGS 3698):

    FOB Brasil (US$/t)    = US$ FOB ÷ toneladas importadas do(s) NCM(s) do produto
    CIF Brasil (US$/t)    = US$ CIF ÷ toneladas
    spread FOB (US$/t)    = FOB Brasil − benchmark       só quando o benchmark é FOB (FOB×FOB)
    spread FOB (%)        = spread FOB ÷ benchmark × 100
    custo posto (US$/t)   = CIF Brasil − benchmark       todos os meses
    custo posto (%)       = custo posto ÷ benchmark × 100
    spread comparável     = spread FOB nos meses FOB×FOB; custo posto nos meses CIF×CFR, em que
                            o benchmark já é um preço no destino (KCl desde 2020, TSP)
    CIF Brasil (R$/t)     = CIF Brasil (US$/t) × PTAX venda média do mês

A tabela PRODUTOS liga cada NCM à série do Banco Mundial; BASES diz a base de cada série em
cada período (as quebras ficam marcadas). O MAP não tem série própria na Pink Sheet: usa o DAP
como aproximação, e isso vai escrito em toda saída. Meses com menos de CORTE_AMOSTRA_T
toneladas importadas ficam marcados como amostra pequena: um único navio decide o preço.

O benchmark é uma referência de outro lugar e de outro momento de contrato, então o spread
mistura origem, frete até o ponto de referência, prazo de contrato e defasagem entre o negócio
e o embarque: não é "ágio" puro. A relação de troca soja/fertilizante (src/troca.py) é
US$/US$ e não muda com o câmbio, por isso não há "troca em reais". O CIF em reais usa a média
do mês, e não o câmbio da data de registro de cada declaração.

Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX.
"""

from __future__ import annotations

import pandas as pd

# Produto -> NCMs do Brasil (conferidos em data/ncm_fertilizantes.csv e nos dados de 2015 a
# 2026) e série da Pink Sheet. DAP: o NCM 310530 foi desdobrado até 2019 (31053010 e 31053090)
# e voltou a ser um só (31053000). TSP: 31031030 (P2O5 acima de 45%) até 2017 e 31031100 (35%
# ou mais) depois, que pode incluir um pouco de outros superfosfatos.
PRODUTOS = {
    "kcl": {"nome": "KCl", "descricao": "cloreto de potássio", "grupo": "K", "ncms": ["31042090"], "serie": "kcl", "proxy": False},
    "ureia": {"nome": "Ureia", "descricao": "ureia com mais de 45% de N", "grupo": "N", "ncms": ["31021010"], "serie": "ureia", "proxy": False},
    "map": {"nome": "MAP", "descricao": "fosfato monoamônico", "grupo": "P", "ncms": ["31054000"], "serie": "dap", "proxy": True},
    "dap": {"nome": "DAP", "descricao": "fosfato diamônico", "grupo": "P", "ncms": ["31053000", "31053010", "31053090"], "serie": "dap", "proxy": False},
    "tsp": {"nome": "TSP", "descricao": "superfosfato triplo", "grupo": "P", "ncms": ["31031030", "31031100"], "serie": "tsp", "proxy": False},
}
# Nome da série no xlsx do Banco Mundial (aba Monthly Prices).
SERIES_PINK = {"kcl": "Potassium chloride", "ureia": "Urea", "dap": "DAP", "tsp": "TSP", "rocha_fosfatica": "Phosphate rock"}
# Base de cada série por período (início inclusive), segundo a aba Description da Pink Sheet
# (edição de outubro de 2026). "FOB": preço na origem, comparado com o FOB Brasil. "CFR": preço
# no destino, comparado com o CIF Brasil.
BASES = {
    "kcl": [("1960-01", "FOB Vancouver", "FOB"), ("2020-01", "CFR Brasil (granulado)", "CFR")],
    "ureia": [("1960-01", "FOB Mar Negro", "FOB"), ("2022-03", "FOB Oriente Médio", "FOB")],
    "dap": [("1960-01", "FOB Golfo dos EUA", "FOB")],
    "tsp": [("1960-01", "importação no Golfo dos EUA", "CFR")],
}
COMPARACAO = {"FOB": "FOB×FOB", "CFR": "CIF×CFR"}
CORTE_AMOSTRA_T = 10_000

COLUNAS = [
    "periodo",
    "ano",
    "mes",
    "produto",
    "grupo",
    "ncms",
    "serie_bm",
    "base_bm",
    "comparacao",
    "quebra",
    "proxy",
    "toneladas",
    "amostra_pequena",
    "fob_brasil_usd_t",
    "cif_brasil_usd_t",
    "benchmark_usd_t",
    "spread_fob_usd_t",
    "spread_fob_pct",
    "custo_posto_usd_t",
    "custo_posto_pct",
    "spread_usd_t",
    "spread_pct",
    "ptax_brl_usd",
    "cif_brl_t",
]


def base_do_mes(serie: str, periodo: str) -> tuple[str, str]:
    """(descrição da base, "FOB" ou "CFR") da série do Banco Mundial no mês."""
    vigente = [b for b in BASES[serie] if b[0] <= periodo][-1]
    return vigente[1], vigente[2]


def comparar(base: pd.DataFrame, pink: pd.DataFrame, ptax: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por mês × produto, de todos os meses das importações.

    `base`: importações (transform.carregar_base: ano, mes, co_ncm, toneladas, vl_fob, vl_cif);
    `pink`: benchmark.ler_pink() (mes e uma coluna por série, em US$/t);
    `ptax`: benchmark.ler_ptax() (mes e brl_por_usd). Mês sem benchmark ou sem PTAX fica vazio.
    """
    ncm_produto = {ncm: chave for chave, p in PRODUTOS.items() for ncm in p["ncms"]}
    imp = base[base["co_ncm"].isin(ncm_produto)].assign(produto=lambda d: d["co_ncm"].map(ncm_produto))
    imp = imp.groupby(["ano", "mes", "produto"], as_index=False)[["toneladas", "vl_fob", "vl_cif"]].sum()

    meses = base[["ano", "mes"]].drop_duplicates()
    grade = meses.merge(pd.DataFrame({"produto": list(PRODUTOS)}), how="cross")
    df = grade.merge(imp, on=["ano", "mes", "produto"], how="left")
    df["toneladas"] = df["toneladas"].fillna(0.0)
    df["periodo"] = df["ano"].astype(int).astype(str) + "-" + df["mes"].astype(int).astype(str).str.zfill(2)

    info = pd.DataFrame.from_dict(PRODUTOS, orient="index")
    df["grupo"] = df["produto"].map(info["grupo"])
    df["ncms"] = df["produto"].map(info["ncms"].map("|".join))
    df["serie_bm"] = df["produto"].map(info["serie"])
    df["proxy"] = df["produto"].map(info["proxy"]).astype(bool)
    bases = df.apply(lambda r: base_do_mes(r["serie_bm"], r["periodo"]), axis=1, result_type="expand")
    df["base_bm"], tipo = bases[0], bases[1]
    df["comparacao"] = tipo.map(COMPARACAO)

    pink_longo = pink.melt(id_vars="mes", var_name="serie_bm", value_name="benchmark_usd_t").rename(columns={"mes": "periodo"})
    pink_longo["benchmark_usd_t"] = pd.to_numeric(pink_longo["benchmark_usd_t"], errors="coerce").astype("float64")
    cambio = ptax.rename(columns={"mes": "periodo", "brl_por_usd": "ptax_brl_usd"})
    cambio["ptax_brl_usd"] = pd.to_numeric(cambio["ptax_brl_usd"], errors="coerce").astype("float64")
    df = df.merge(pink_longo, on=["periodo", "serie_bm"], how="left")
    df = df.merge(cambio, on="periodo", how="left")

    com_volume = df["toneladas"] > 0
    df["fob_brasil_usd_t"] = (df["vl_fob"] / df["toneladas"]).where(com_volume)
    df["cif_brasil_usd_t"] = (df["vl_cif"] / df["toneladas"]).where(com_volume)
    df["amostra_pequena"] = com_volume & (df["toneladas"] < CORTE_AMOSTRA_T)
    bm = df["benchmark_usd_t"].where(df["benchmark_usd_t"] > 0)
    fob_fob = df["comparacao"].eq("FOB×FOB")
    df["spread_fob_usd_t"] = (df["fob_brasil_usd_t"] - bm).where(fob_fob)
    df["spread_fob_pct"] = (100 * df["spread_fob_usd_t"] / bm).where(fob_fob)
    df["custo_posto_usd_t"] = df["cif_brasil_usd_t"] - bm
    df["custo_posto_pct"] = 100 * df["custo_posto_usd_t"] / bm
    df["spread_usd_t"] = df["spread_fob_usd_t"].where(fob_fob, df["custo_posto_usd_t"])
    df["spread_pct"] = df["spread_fob_pct"].where(fob_fob, df["custo_posto_pct"])
    df["cif_brl_t"] = df["cif_brasil_usd_t"] * df["ptax_brl_usd"]

    ordem = {chave: i for i, chave in enumerate(PRODUTOS)}
    df = df.assign(_ordem=df["produto"].map(ordem)).sort_values(["ano", "mes", "_ordem"], kind="mergesort")
    anterior = df.groupby("produto", sort=False)["base_bm"].shift()
    df["quebra"] = anterior.notna() & (anterior != df["base_bm"])
    return df[COLUNAS].reset_index(drop=True)


def quebras(base_periodos: list[str]) -> list[dict]:
    """Mudanças de base das séries do Banco Mundial dentro do período dos dados."""
    if not base_periodos:
        return []
    inicio, fim = base_periodos[0], base_periodos[-1]
    saida = []
    for produto, p in PRODUTOS.items():
        bases = BASES[p["serie"]]
        for (_, de, _), (desde, para, tipo) in zip(bases, bases[1:]):
            if inicio < desde <= fim:
                saida.append({"produto": produto, "periodo": desde, "de": de, "para": para, "comparacao": COMPARACAO[tipo]})
    return saida


def bloco_dashboard(df: pd.DataFrame, periodos: list[str], num, fonte_pink: dict | None = None) -> dict:
    """Bloco "benchmark" do dashboard.json, alinhado aos períodos da série mensal.

    `num(valor, casas)` é o formatador do transform (arredonda e troca NaN por None).
    """
    produtos = {}
    for chave, info in PRODUTOS.items():
        p = df[df["produto"] == chave].set_index("periodo").reindex(periodos)
        bases = []
        for desde, descricao, tipo in BASES[info["serie"]]:
            if desde <= periodos[-1]:
                bases.append({"desde": max(desde, periodos[0]), "base": descricao, "comparacao": COMPARACAO[tipo]})
        bases = [b for i, b in enumerate(bases) if i == len(bases) - 1 or bases[i + 1]["desde"] > b["desde"]]
        produtos[chave] = {
            **{k: info[k] for k in ("nome", "descricao", "grupo", "ncms", "proxy")},
            "serie": SERIES_PINK[info["serie"]],
            "bases": bases,
            "t": [num(v, 0) for v in p["toneladas"]],
            "fob": [num(v, 1) for v in p["fob_brasil_usd_t"]],
            "cif": [num(v, 1) for v in p["cif_brasil_usd_t"]],
            "bm": [num(v, 1) for v in p["benchmark_usd_t"]],
            "spread_usd_t": [num(v, 1) for v in p["spread_usd_t"]],
            "spread_pct": [num(v, 2) for v in p["spread_pct"]],
            "custo_posto_usd_t": [num(v, 1) for v in p["custo_posto_usd_t"]],
            "cif_brl_t": [num(v, 0) for v in p["cif_brl_t"]],
            "amostra_pequena": [bool(v) if pd.notna(v) else False for v in p["amostra_pequena"]],
        }
    ptax = df.drop_duplicates("periodo").set_index("periodo").reindex(periodos)["ptax_brl_usd"]
    return {
        "periodos": periodos,
        "produtos": produtos,
        "ptax": [num(v, 4) for v in ptax],
        "corte_amostra_t": CORTE_AMOSTRA_T,
        "quebras": quebras(periodos),
        "pink_atualizado_em": (fonte_pink or {}).get("atualizado_em"),
        "fonte": "Comex Stat/MDIC; Banco Mundial (Pink Sheet, CC BY 4.0); BCB/PTAX (SGS 3698, ODbL)",
        "notas": [
            "Spread comparável: FOB Brasil − benchmark quando o benchmark é FOB (FOB×FOB); CIF Brasil − benchmark"
            " quando o benchmark já é um preço no destino (CIF×CFR: KCl desde 2020 e TSP).",
            "O benchmark é uma referência de outro lugar e de outro momento de contrato: o spread mistura origem,"
            " frete até o ponto de referência, prazo de contrato e defasagem. Não é ágio puro.",
            "MAP comparado com o DAP do Banco Mundial (a Pink Sheet não tem série de MAP).",
            f"Meses com menos de {CORTE_AMOSTRA_T // 1000} mil t importadas: amostra pequena, um navio decide o preço.",
            "CIF em reais pela PTAX venda média do mês, e não pelo câmbio da data de cada declaração.",
        ],
    }
