"""Relação de troca "no porto": quantas toneladas de soja pagam 1 t de fertilizante.

Para cada mês e produto:

    preço CIF do fertilizante (US$/t) = US$ CIF importado ÷ toneladas importadas do NCM
    preço FOB da soja (US$/t)         = US$ FOB exportado ÷ toneladas exportadas (NCM 12019000)
    relação de troca                  = preço CIF do fertilizante ÷ preço FOB da soja
                                        (toneladas de soja por tonelada de fertilizante)

É uma relação medida na fronteira, com a soja no embarque (FOB) e o fertilizante no
desembarque (CIF). NÃO é o preço ao produtor: fica de fora o frete interno nos dois
sentidos, impostos, armazenagem, margens da revenda e das tradings, prêmios e a
diferença de prazo entre a compra do adubo e a venda da safra.

A média histórica de referência é a média simples das relações mensais de toda a série.

Fonte: Comex Stat/MDIC.
"""

from __future__ import annotations

import pandas as pd

from . import config

# Produtos da relação de troca. NCMs conferidos em /tables/ncm/{código} (outubro de 2026).
PRODUTOS = {
    "kcl": {"ncm": "31042090", "nome": "KCl", "descricao": "cloreto de potássio", "grupo": "K"},
    "ureia": {"ncm": "31021010", "nome": "Ureia", "descricao": "ureia com mais de 45% de N", "grupo": "N"},
    "map": {"ncm": "31054000", "nome": "MAP", "descricao": "fosfato monoamônico", "grupo": "P"},
}
KG_SACA = 60  # saca de soja, só para leitura no dashboard

COLUNAS = [
    "periodo",
    "ano",
    "mes",
    "produto",
    "co_ncm",
    "toneladas_fert",
    "vl_cif_fert",
    "preco_cif_fert_usd_t",
    "toneladas_soja",
    "vl_fob_soja",
    "preco_fob_soja_usd_t",
    "t_soja_por_t",
    "sacas_soja_por_t",
]


def _periodo(df: pd.DataFrame) -> pd.Series:
    return df["ano"].astype(str) + "-" + df["mes"].astype(str).str.zfill(2)


def precos_soja(soja: pd.DataFrame) -> pd.DataFrame:
    """Preço FOB mensal da soja exportada: US$ FOB ÷ toneladas."""
    df = soja.assign(
        toneladas_soja=soja["kg_liquido"].astype("float64") / 1000,
        vl_fob_soja=soja["vl_fob"].astype("float64"),
    )
    df = df.groupby(["ano", "mes"], as_index=False)[["toneladas_soja", "vl_fob_soja"]].sum()
    df["preco_fob_soja_usd_t"] = (df["vl_fob_soja"] / df["toneladas_soja"]).where(df["toneladas_soja"] > 0)
    return df


def relacao_troca(base: pd.DataFrame, soja: pd.DataFrame) -> pd.DataFrame:
    """Relação de troca mensal por produto (formato longo, uma linha por mês × produto).

    `base` são as importações (transform.carregar_base: colunas ano, mes, co_ncm, toneladas e
    vl_cif); `soja` é a exportação de soja (ingest.ler_soja). Meses sem soja ou sem importação
    do produto ficam com a relação vazia.
    """
    ncm_produto = {p["ncm"]: chave for chave, p in PRODUTOS.items()}
    fert = base[base["co_ncm"].isin(ncm_produto)]
    fert = fert.groupby(["ano", "mes", "co_ncm"], as_index=False)[["toneladas", "vl_cif"]].sum()
    fert = fert.rename(columns={"toneladas": "toneladas_fert", "vl_cif": "vl_cif_fert"})
    fert["produto"] = fert["co_ncm"].map(ncm_produto)

    # Todos os meses × produtos da série de importação, mesmo os sem compra do produto.
    meses = base[["ano", "mes"]].drop_duplicates()
    grade = meses.merge(pd.DataFrame({"produto": list(PRODUTOS)}), how="cross")
    grade["co_ncm"] = grade["produto"].map({chave: p["ncm"] for chave, p in PRODUTOS.items()})
    df = grade.merge(fert, on=["ano", "mes", "produto", "co_ncm"], how="left")
    df = df.merge(precos_soja(soja), on=["ano", "mes"], how="left")

    df["preco_cif_fert_usd_t"] = (df["vl_cif_fert"] / df["toneladas_fert"]).where(df["toneladas_fert"] > 0)
    # Sem preço da soja (mês sem exportação ou valor zerado) não há relação: evita divisão por zero.
    df["t_soja_por_t"] = (df["preco_cif_fert_usd_t"] / df["preco_fob_soja_usd_t"]).where(
        df["preco_fob_soja_usd_t"] > 0
    )
    df["sacas_soja_por_t"] = df["t_soja_por_t"] * 1000 / KG_SACA
    df["periodo"] = _periodo(df)
    ordem = {chave: i for i, chave in enumerate(PRODUTOS)}
    df = df.assign(_ordem=df["produto"].map(ordem)).sort_values(["ano", "mes", "_ordem"], kind="mergesort")
    return df[COLUNAS].reset_index(drop=True)


def media_historica(troca: pd.DataFrame) -> pd.Series:
    """Média simples das relações mensais de cada produto, na série inteira."""
    return troca.groupby("produto", sort=False)["t_soja_por_t"].mean()


def bloco_dashboard(troca: pd.DataFrame, periodos: list[str], num) -> dict:
    """Bloco "troca" do dashboard.json, alinhado aos períodos da série mensal.

    `num(valor, casas)` é o formatador do transform (arredonda e troca NaN por None).
    """
    medias = media_historica(troca)
    soja = troca.drop_duplicates("periodo").set_index("periodo").reindex(periodos)
    produtos = {}
    for chave, info in PRODUTOS.items():
        p = troca[troca["produto"] == chave].set_index("periodo").reindex(periodos)
        validos = p["t_soja_por_t"].dropna()
        produtos[chave] = {
            **info,
            "t": [num(v, 0) for v in p["toneladas_fert"]],
            "cif": [num(v, 1) for v in p["preco_cif_fert_usd_t"]],
            "razao": [num(v, 3) for v in p["t_soja_por_t"]],
            "media": num(medias.get(chave), 3),
            "media_periodo": [validos.index[0], validos.index[-1]] if not validos.empty else None,
            "n_meses": int(validos.size),
        }
    return {
        "periodos": periodos,
        "soja_ncm": config.NCM_SOJA,
        "soja_fob": [num(v, 1) for v in soja["preco_fob_soja_usd_t"]],
        "soja_t": [num(v, 0) for v in soja["toneladas_soja"]],
        "kg_saca": KG_SACA,
        "produtos": produtos,
        "notas": [
            "Relação de troca no porto: preço CIF do fertilizante importado ÷ preço FOB da soja"
            " exportada no mesmo mês, em toneladas de soja por tonelada de fertilizante.",
            "Não é o preço ao produtor: não inclui frete interno, impostos, margens nem prêmios.",
            "Média histórica: média simples das relações mensais de toda a série.",
        ],
    }
