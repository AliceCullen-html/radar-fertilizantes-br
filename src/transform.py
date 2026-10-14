"""Fase 2: tabelas analíticas das importações de fertilizantes.

Lê os dados brutos (data/raw/, via ingest.ler_raw) e o grupo de cada NCM
(data/ncm_fertilizantes.csv) e grava em data/processed/:

- precos_mes_grupo_origem.csv: preço implícito US$/t FOB e CIF e frete US$/t
  por mês × grupo × país de origem;
- precos_mes_grupo.csv: o mesmo por mês × grupo, somando todas as origens;
- share_origem_ano.csv e share_urf_ano.csv: share em toneladas por país de
  origem e por URF, por ano e grupo, com variação sobre o mesmo período do ano
  anterior (YoY);
- hhi_ano_grupo.csv: concentração de fornecedores (HHI) por ano e grupo;
- sazonalidade_grupo.csv: média de toneladas por mês do calendário e grupo;
- rotas_ano.csv: toneladas e frete US$/t por ano × grupo × país de origem × URF,
  marcando as rotas com volume relevante (CORTE_ROTA_T);
- troca_mensal.csv: relação de troca "no porto" entre soja exportada e KCl,
  ureia e MAP importados (src/troca.py), se houver dados de soja em
  data/raw_soja/;
- dashboard.json: export compacto para o dashboard estático (fase 3).

Nas tabelas de share, HHI e sazonalidade, o grupo TOTAL soma N, P, K e NPK.

Uso:
    python -m src.transform

Fonte: Comex Stat/MDIC.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

import pandas as pd

from . import config, ingest, troca

log = logging.getLogger(__name__)

GRUPOS = ["N", "P", "K", "NPK"]
TOTAL = "TOTAL"
NOMES_GRUPOS = {
    "N": "Nitrogenados",
    "P": "Fosfatados",
    "K": "Potássicos",
    "NPK": "Compostos (NPK)",
    TOTAL: "Todos os grupos",
}
_ORDEM_GRUPO = {grupo: i for i, grupo in enumerate([*GRUPOS, TOTAL])}
VALORES = ["toneladas", "vl_fob", "vl_cif", "vl_frete", "vl_seguro"]
PAIS = ["co_pais", "no_pais"]
URF = ["co_urf", "no_urf"]
TOP_DASHBOARD = 10  # origens e URFs por ano e grupo no dashboard.json; o resto vira "Outros"
# Rota (país de origem -> URF) com volume relevante: pelo menos 50 mil t no ano, cerca de um
# navio Supramax cheio, proporcional aos meses num ano parcial. Abaixo disso o frete US$/t
# é ruidoso (cargas pequenas, conteinerizadas ou fracionadas) e as rotas somam ~10% do volume.
CORTE_ROTA_T = 50_000
TOP_SANKEY = 7  # origens e URFs por lado no diagrama de fluxo; o resto vira "Outros"...
MIN_SHARE_SANKEY = 2.0  # ...assim como quem tem menos de 2% das toneladas do ano
TOP_ROTAS = 20  # rotas relevantes por ano e grupo na tabela de frete do dashboard


# --- Base ----------------------------------------------------------------------------------


def carregar_base(raw_dir: Path = config.RAW_DIR, ncm_csv: Path = config.NCM_CSV) -> pd.DataFrame:
    """Dados brutos com o grupo de cada NCM e o peso em toneladas."""
    base = ingest.ler_raw(raw_dir)
    if base.empty:
        raise ValueError(f"Sem dados em {raw_dir}. Rode antes: python -m src.ingest --inicio AAAA-MM")
    grupos = ingest.carregar_ncms(ncm_csv).set_index("ncm")["grupo"]
    base["grupo"] = base["co_ncm"].map(grupos)
    sem_grupo = sorted(base.loc[base["grupo"].isna(), "co_ncm"].unique())
    if sem_grupo:
        raise ValueError(f"NCMs sem grupo em {ncm_csv}: {sem_grupo}")
    base[VALORES[1:]] = base[VALORES[1:]].astype("float64")
    base["toneladas"] = base["kg_liquido"].astype("float64") / 1000
    return base


def com_total(base: pd.DataFrame) -> pd.DataFrame:
    """As linhas por grupo mais as mesmas linhas no grupo TOTAL."""
    return pd.concat([base, base.assign(grupo=TOTAL)], ignore_index=True)


def meses_por_ano(base: pd.DataFrame) -> dict[int, list[int]]:
    """Meses presentes nos dados, por ano."""
    return {int(ano): sorted(int(m) for m in meses) for ano, meses in base.groupby("ano")["mes"].unique().items()}


def _somar(df: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    # dropna=False: um país sem código (nome fora da tabela auxiliar) não pode sumir dos totais.
    return df.groupby(chaves, as_index=False, dropna=False)[VALORES].sum()


def _ordenar(df: pd.DataFrame, colunas: list[str], decrescentes: tuple[str, ...] = ()) -> pd.DataFrame:
    """Ordena com os grupos na ordem N, P, K, NPK, TOTAL."""
    temp = df.assign(_grupo=df["grupo"].map(_ORDEM_GRUPO))
    chaves = ["_grupo" if c == "grupo" else c for c in colunas]
    ascendentes = [c not in decrescentes for c in colunas]
    return temp.sort_values(chaves, ascending=ascendentes, kind="mergesort").drop(columns="_grupo").reset_index(drop=True)


# --- Cálculos -----------------------------------------------------------------------------


def preco_por_tonelada(valor: pd.Series, toneladas: pd.Series) -> pd.Series:
    """US$/t = valor ÷ toneladas, isto é, média ponderada pelo peso. Vazio sem tonelagem."""
    return (valor / toneladas).where(toneladas > 0)


def hhi(shares_pct: pd.Series) -> float:
    """Índice Herfindahl-Hirschman: soma dos quadrados dos shares em %, de 0 a 10.000."""
    return float((shares_pct.astype("float64") ** 2).sum())


def precos(base: pd.DataFrame, por_origem: bool = True) -> pd.DataFrame:
    """Preço implícito por mês × grupo (× país de origem): US$/t FOB e CIF e frete US$/t."""
    chaves = ["ano", "mes", "grupo", *(PAIS if por_origem else [])]
    df = _somar(base, chaves).drop(columns="vl_seguro")
    df.insert(0, "periodo", df["ano"].astype(str) + "-" + df["mes"].astype(str).str.zfill(2))
    df["preco_fob_usd_t"] = preco_por_tonelada(df["vl_fob"], df["toneladas"])
    df["preco_cif_usd_t"] = preco_por_tonelada(df["vl_cif"], df["toneladas"])
    df["frete_usd_t"] = preco_por_tonelada(df["vl_frete"], df["toneladas"])
    return _ordenar(df, ["ano", "mes", "grupo", "toneladas"], decrescentes=("toneladas",))


def shares_anuais(base: pd.DataFrame, entidade: list[str]) -> pd.DataFrame:
    """Share em toneladas por ano × grupo × entidade (país ou URF), com variação YoY.

    A comparação é sempre com os mesmos meses do ano anterior: num ano parcial
    (ex.: jan-set/2026) a base é jan-set/2025. Se o ano anterior não tiver todos
    esses meses, a variação fica vazia. Quem importou no período anterior e não
    importou no atual aparece com zero.
    """
    dados = com_total(base)
    meses = meses_por_ano(base)
    chaves = ["ano", "grupo", *entidade]
    df = _somar(dados, chaves)[[*chaves, "toneladas", "vl_fob"]]

    comparaveis = [ano for ano in meses if ano - 1 in meses and set(meses[ano]) <= set(meses[ano - 1])]
    anteriores = []
    for ano in comparaveis:
        periodo = dados[(dados["ano"] == ano - 1) & dados["mes"].isin(meses[ano])]
        anteriores.append(_somar(periodo, chaves)[[*chaves, "toneladas"]].assign(ano=ano))
    if anteriores:
        anterior = pd.concat(anteriores, ignore_index=True)
        df = df.merge(anterior.rename(columns={"toneladas": "toneladas_ano_anterior"}), on=chaves, how="outer")
        df[["toneladas", "vl_fob"]] = df[["toneladas", "vl_fob"]].fillna(0)
        com_anterior = df["ano"].isin(comparaveis)
        df.loc[com_anterior, "toneladas_ano_anterior"] = df.loc[com_anterior, "toneladas_ano_anterior"].fillna(0)
    else:
        df["toneladas_ano_anterior"] = float("nan")

    por = ["ano", "grupo"]
    df["share_t_pct"] = 100 * df["toneladas"] / df.groupby(por)["toneladas"].transform("sum")
    df["share_t_pct_ano_anterior"] = (
        100 * df["toneladas_ano_anterior"] / df.groupby(por)["toneladas_ano_anterior"].transform("sum")
    )
    anterior_t = df["toneladas_ano_anterior"]
    df["var_t_pct"] = (100 * (df["toneladas"] / anterior_t - 1)).where(anterior_t > 0)
    df["var_share_pp"] = df["share_t_pct"] - df["share_t_pct_ano_anterior"]
    df["preco_fob_usd_t"] = preco_por_tonelada(df["vl_fob"], df["toneladas"])
    df.insert(1, "n_meses", df["ano"].map(lambda ano: len(meses[ano])))
    return _ordenar(df, ["ano", "grupo", "toneladas"], decrescentes=("toneladas",))


def concentracao(share_origem: pd.DataFrame) -> pd.DataFrame:
    """HHI, número de origens, país líder e CR3 por ano × grupo (entrada: shares_anuais por país)."""
    linhas = []
    for (ano, grupo), g in share_origem[share_origem["toneladas"] > 0].groupby(["ano", "grupo"]):
        g = g.sort_values("share_t_pct", ascending=False)
        linhas.append(
            {
                "ano": ano,
                "n_meses": g["n_meses"].iloc[0],
                "grupo": grupo,
                "toneladas": g["toneladas"].sum(),
                "n_origens": len(g),
                "hhi": hhi(g["share_t_pct"]),
                "lider_co_pais": g["co_pais"].iloc[0],
                "lider_no_pais": g["no_pais"].iloc[0],
                "lider_share_pct": g["share_t_pct"].iloc[0],
                "cr3_pct": g["share_t_pct"].head(3).sum(),
            }
        )
    if not linhas:
        return pd.DataFrame(columns=["ano", "n_meses", "grupo", "toneladas", "n_origens", "hhi"])
    return _ordenar(pd.DataFrame(linhas), ["ano", "grupo"])


def sazonalidade(base: pd.DataFrame) -> pd.DataFrame:
    """Média de toneladas por mês do calendário e grupo, só com anos completos (12 meses).

    indice_sazonal = média do mês ÷ média dos 12 meses × 100.
    """
    completos = sorted(ano for ano, meses in meses_por_ano(base).items() if len(meses) == 12)
    colunas = ["grupo", "mes", "toneladas_media", "indice_sazonal", "n_anos", "ano_inicial", "ano_final"]
    if not completos:
        log.warning("Sem ano completo nos dados: sazonalidade vazia.")
        return pd.DataFrame(columns=colunas)
    mensal = _somar(com_total(base[base["ano"].isin(completos)]), ["ano", "mes", "grupo"])
    grade = pd.MultiIndex.from_product([completos, range(1, 13), [*GRUPOS, TOTAL]], names=["ano", "mes", "grupo"])
    mensal = mensal.set_index(["ano", "mes", "grupo"])["toneladas"].reindex(grade, fill_value=0).reset_index()
    saz = mensal.groupby(["grupo", "mes"], as_index=False)["toneladas"].mean()
    saz = saz.rename(columns={"toneladas": "toneladas_media"})
    saz["indice_sazonal"] = 100 * saz["toneladas_media"] / saz.groupby("grupo")["toneladas_media"].transform("mean")
    saz = saz.assign(n_anos=len(completos), ano_inicial=completos[0], ano_final=completos[-1])
    return _ordenar(saz[colunas], ["grupo", "mes"])


def rotas_anuais(base: pd.DataFrame) -> pd.DataFrame:
    """Toneladas, valores e frete US$/t por ano × grupo × país de origem × URF.

    `relevante` marca as rotas com pelo menos CORTE_ROTA_T toneladas no ano (num ano
    parcial, o corte é proporcional aos meses com dados: 9 meses -> 37.500 t).
    """
    dados = com_total(base)
    meses = meses_por_ano(base)
    chaves = ["ano", "grupo", *PAIS, *URF]
    df = _somar(dados, chaves).drop(columns="vl_seguro")
    com_carga = dados[dados["toneladas"] > 0].groupby(chaves, dropna=False)["mes"].nunique()
    df = df.merge(com_carga.rename("meses").reset_index(), on=chaves, how="left")
    df["meses"] = df["meses"].fillna(0).astype("int64")
    df["frete_usd_t"] = preco_por_tonelada(df["vl_frete"], df["toneladas"])
    df["frete_pct_cif"] = (100 * df["vl_frete"] / df["vl_cif"]).where(df["vl_cif"] > 0)
    df["share_t_pct"] = 100 * df["toneladas"] / df.groupby(["ano", "grupo"])["toneladas"].transform("sum")
    df.insert(1, "n_meses", df["ano"].map(lambda ano: len(meses[ano])))
    df["corte_t"] = (CORTE_ROTA_T * df["n_meses"] / 12).round()
    df["relevante"] = df["toneladas"] >= df["corte_t"]
    return _ordenar(df, ["ano", "grupo", "toneladas"], decrescentes=("toneladas",))


def _bloco_rotas(g: pd.DataFrame) -> dict:
    """Diagrama de fluxo (top origens -> top URFs, o resto em "Outros") e rotas relevantes de um ano × grupo."""
    g = g[g["toneladas"] > 0]

    def nos(entidade: list[str]) -> tuple[list[list], pd.Series]:
        codigo, nome = entidade
        tot = g.groupby(entidade, dropna=False, sort=False)["toneladas"].sum().sort_values(ascending=False, kind="mergesort")
        top = tot.head(TOP_SANKEY)
        top = top[100 * top >= MIN_SHARE_SANKEY * tot.sum()]
        lista = [[_texto(c), _texto(n), _num(t, 0)] for (c, n), t in top.items()]
        if len(tot) > len(top):
            lista.append([None, "Outros", _num(tot.iloc[len(top):].sum(), 0)])
        indice = {chave: i for i, chave in enumerate(top.index)}
        posicao = pd.Series(
            [indice.get(chave, len(top)) for chave in zip(g[codigo], g[nome])], index=g.index, dtype="int64"
        )
        return lista, posicao

    origens, i_origem = nos(PAIS)
    urfs, i_urf = nos(URF)
    fluxos = (
        g.assign(o=i_origem, u=i_urf)
        .groupby(["o", "u"], as_index=False)[["toneladas", "vl_frete"]]
        .sum()
        .sort_values(["o", "u"])
    )
    fluxos["frete_usd_t"] = preco_por_tonelada(fluxos["vl_frete"], fluxos["toneladas"])
    relevantes = g[g["relevante"]]
    t_relevantes = relevantes["toneladas"].sum()
    rotas = [
        [
            _texto(r["no_pais"]),
            _texto(r["co_urf"]),
            _texto(r["no_urf"]),
            _num(r["toneladas"], 0),
            _num(r["share_t_pct"], 2),
            _num(r["frete_usd_t"], 1),
            _num(r["frete_pct_cif"], 1),
            int(r["meses"]),
        ]
        for _, r in relevantes.head(TOP_ROTAS).iterrows()
    ]
    return {
        "origens": origens,
        "urfs": urfs,
        "fluxos": [
            [int(r["o"]), int(r["u"]), _num(r["toneladas"], 0), _num(r["frete_usd_t"], 1)]
            for _, r in fluxos.iterrows()
        ],
        "rotas": rotas,
        "n_rotas": int(len(g)),
        "n_relevantes": int(len(relevantes)),
        "share_relevantes_pct": _num(relevantes["share_t_pct"].sum(), 1),
        "frete_relevantes": _num(relevantes["vl_frete"].sum() / t_relevantes, 1) if t_relevantes > 0 else None,
        "corte_t": _num(g["corte_t"].iloc[0], 0) if not g.empty else None,
    }


# --- Export para o dashboard -----------------------------------------------------------


def _num(valor, casas: int = 2):
    """Número pronto para JSON: arredondado, e None no lugar de NaN ou infinito."""
    if valor is None or pd.isna(valor) or math.isinf(valor):
        return None
    return int(round(valor)) if casas == 0 else round(float(valor), casas)


def _texto(valor):
    return None if valor is None or pd.isna(valor) else str(valor)


def _ranking(tabela: pd.DataFrame, codigo: str, nome: str, com_preco: bool) -> list[list]:
    """Top N do ano × grupo, mais uma linha "Outros" com o resto."""
    ativos = tabela[tabela["toneladas"] > 0]
    top = ativos.head(TOP_DASHBOARD)
    resto = tabela.drop(top.index)
    linhas = []
    for _, r in top.iterrows():
        linha = [
            _texto(r[codigo]),
            _texto(r[nome]),
            _num(r["toneladas"], 0),
            _num(r["share_t_pct"], 3),
            _num(r["var_t_pct"], 1),
            _num(r["var_share_pp"], 3),
        ]
        linhas.append(linha + ([_num(r["preco_fob_usd_t"], 2)] if com_preco else []))
    if resto["toneladas"].sum() > 0:
        t, t_ant = resto["toneladas"].sum(), resto["toneladas_ano_anterior"].sum(min_count=1)
        linha = [
            None,
            "Outros",
            _num(t, 0),
            _num(resto["share_t_pct"].sum(), 3),
            _num(100 * (t / t_ant - 1), 1) if t_ant and t_ant > 0 else None,
            _num(resto["share_t_pct"].sum() - resto["share_t_pct_ano_anterior"].sum(min_count=1), 3),
        ]
        linhas.append(linha + ([_num(resto["vl_fob"].sum() / t, 2)] if com_preco else []))
    return linhas


def dashboard(tabelas: dict[str, pd.DataFrame]) -> dict:
    """Export compacto (séries mensais, resumo anual, rankings e sazonalidade) para a fase 3."""
    precos_grupo = tabelas["precos_mes_grupo"]
    origens, urfs = tabelas["share_origem_ano"], tabelas["share_urf_ano"]
    conc, saz = tabelas["hhi_ano_grupo"], tabelas["sazonalidade_grupo"]
    periodos = sorted(precos_grupo["periodo"].unique())
    anos = sorted(int(a) for a in origens["ano"].unique())

    mensal = {"periodos": periodos}
    somaveis = ["toneladas", "vl_fob", "vl_cif", "vl_frete"]
    total_mes = precos_grupo.groupby("periodo")[somaveis].sum().reindex(periodos)
    for grupo in GRUPOS:
        g = precos_grupo[precos_grupo["grupo"] == grupo].set_index("periodo").reindex(periodos)
        mensal[grupo] = {
            "t": [_num(v, 0) for v in g["toneladas"]],
            "fob_musd": [_num(v / 1e6, 3) for v in g["vl_fob"]],
            "cif_musd": [_num(v / 1e6, 3) for v in g["vl_cif"]],
            "frete_musd": [_num(v / 1e6, 3) for v in g["vl_frete"]],
            "preco_fob": [_num(v, 1) for v in g["preco_fob_usd_t"]],
            "preco_cif": [_num(v, 1) for v in g["preco_cif_usd_t"]],
            "frete": [_num(v, 1) for v in g["frete_usd_t"]],
        }
    mensal[TOTAL] = {
        "t": [_num(v, 0) for v in total_mes["toneladas"]],
        "fob_musd": [_num(v / 1e6, 3) for v in total_mes["vl_fob"]],
        "cif_musd": [_num(v / 1e6, 3) for v in total_mes["vl_cif"]],
        "frete_musd": [_num(v / 1e6, 3) for v in total_mes["vl_frete"]],
    }

    anual = {"anos": anos, "n_meses": [int(n) for n in origens.groupby("ano")["n_meses"].first().reindex(anos)]}
    for grupo in [*GRUPOS, TOTAL]:
        c = conc[conc["grupo"] == grupo].set_index("ano").reindex(anos)
        valores = origens[origens["grupo"] == grupo].groupby("ano")[["toneladas", "vl_fob"]].sum().reindex(anos)
        anual[grupo] = {
            "t": [_num(v, 0) for v in valores["toneladas"]],
            "fob_musd": [_num(v / 1e6, 2) for v in valores["vl_fob"]],
            "preco_fob": [_num(v, 1) for v in preco_por_tonelada(valores["vl_fob"], valores["toneladas"])],
            "hhi": [_num(v, 0) for v in c["hhi"]],
            "n_origens": [_num(v, 0) for v in c["n_origens"]],
            "lider": [_texto(v) for v in c["lider_no_pais"]],
            "lider_share_pct": [_num(v, 1) for v in c["lider_share_pct"]],
            "cr3_pct": [_num(v, 1) for v in c["cr3_pct"]],
        }

    def rankings(tabela: pd.DataFrame, codigo: str, nome: str, com_preco: bool) -> dict:
        return {
            str(ano): {
                grupo: _ranking(g, codigo, nome, com_preco)
                for grupo, g in tabela[tabela["ano"] == ano].groupby("grupo", sort=False)
            }
            for ano in anos
        }

    colunas = ["codigo", "nome", "t", "share_pct", "var_t_pct", "var_share_pp"]
    sazonal = {}
    for grupo in [*GRUPOS, TOTAL]:
        s = saz[saz["grupo"] == grupo].sort_values("mes")
        sazonal[grupo] = {
            "t_media": [_num(v, 0) for v in s["toneladas_media"]],
            "indice": [_num(v, 1) for v in s["indice_sazonal"]],
        }
    if not saz.empty:
        sazonal["anos"] = [int(saz["ano_inicial"].iloc[0]), int(saz["ano_final"].iloc[0])]

    painel = {
        "meta": {
            "fonte": "Comex Stat/MDIC",
            "periodo": {"inicio": periodos[0], "fim": periodos[-1]},
            "grupos": NOMES_GRUPOS,
            "unidades": {
                "t": "toneladas",
                "fob_musd": "US$ milhões FOB",
                "cif_musd": "US$ milhões CIF",
                "frete_musd": "US$ milhões de frete",
                "preco_fob": "US$/t FOB",
                "preco_cif": "US$/t CIF",
                "frete": "US$/t",
                "share_pct": "% das toneladas do ano e grupo",
                "var_t_pct": "% sobre o mesmo período do ano anterior",
                "var_share_pp": "pontos percentuais sobre o mesmo período do ano anterior",
                "hhi": "Herfindahl-Hirschman, 0 a 10.000, por toneladas e país de origem",
            },
            "notas": [
                "Preços implícitos: valor total ÷ toneladas (média ponderada pelo peso).",
                "Anos com n_meses < 12 são parciais; a variação compara os mesmos meses do ano anterior.",
                "TOTAL soma os grupos N, P, K e NPK.",
            ],
        },
        "mensal": mensal,
        "anual": anual,
        "origens": {"colunas": [*colunas, "preco_fob"], "dados": rankings(origens, "co_pais", "no_pais", True)},
        "urfs": {"colunas": colunas, "dados": rankings(urfs, "co_urf", "no_urf", False)},
        "sazonalidade": sazonal,
    }
    if "rotas_ano" in tabelas:
        rotas = tabelas["rotas_ano"]
        painel["rotas"] = {
            "corte_t": CORTE_ROTA_T,
            "colunas_rotas": ["pais", "co_urf", "urf", "t", "share_pct", "frete_usd_t", "frete_pct_cif", "meses"],
            "dados": {
                str(ano): {grupo: _bloco_rotas(g) for grupo, g in rotas[rotas["ano"] == ano].groupby("grupo", sort=False)}
                for ano in anos
            },
        }
    if "troca_mensal" in tabelas:
        painel["troca"] = troca.bloco_dashboard(tabelas["troca_mensal"], periodos, _num)
    return painel


# --- Gravação -------------------------------------------------------------------------------


def _arredondar(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for coluna in df.columns:
        if coluna.startswith("vl_"):
            df[coluna] = df[coluna].round().astype("Int64")
        elif coluna.startswith("toneladas"):
            df[coluna] = df[coluna].round(3)
        elif coluna.endswith("_usd_t") or coluna in ("hhi", "indice_sazonal"):
            df[coluna] = df[coluna].round(2)
        elif coluna.endswith(("_pct", "_pp", "_por_t")):
            df[coluna] = df[coluna].round(4)
    return df


def gerar(
    base: pd.DataFrame, saida: Path = config.PROCESSED_DIR, soja: pd.DataFrame | None = None
) -> dict[str, pd.DataFrame]:
    """Calcula todas as tabelas e grava os CSVs e o dashboard.json em `saida`.

    Sem `soja` (exportação de soja em grão), a relação de troca fica de fora.
    """
    tabelas = {
        "precos_mes_grupo_origem": precos(base, por_origem=True),
        "precos_mes_grupo": precos(base, por_origem=False),
        "share_origem_ano": shares_anuais(base, PAIS),
        "share_urf_ano": shares_anuais(base, URF),
    }
    tabelas["hhi_ano_grupo"] = concentracao(tabelas["share_origem_ano"])
    tabelas["sazonalidade_grupo"] = sazonalidade(base)
    tabelas["rotas_ano"] = rotas_anuais(base)
    if soja is not None and not soja.empty:
        tabelas["troca_mensal"] = troca.relacao_troca(base, soja)
    saida.mkdir(parents=True, exist_ok=True)
    for nome, df in tabelas.items():
        _arredondar(df).to_csv(saida / f"{nome}.csv", index=False, encoding="utf-8", lineterminator="\n")
    painel = json.dumps(dashboard(tabelas), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (saida / "dashboard.json").write_text(painel + "\n", encoding="utf-8")
    return tabelas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.transform",
        description="Fase 2: tabelas analíticas em data/processed/. Fonte: Comex Stat/MDIC.",
    )
    parser.add_argument("--raw", type=Path, default=config.RAW_DIR, help="dados brutos (padrão: data/raw)")
    parser.add_argument(
        "--raw-soja", type=Path, default=config.RAW_SOJA_DIR, help="exportação de soja (padrão: data/raw_soja)"
    )
    parser.add_argument("--saida", type=Path, default=config.PROCESSED_DIR, help="destino (padrão: data/processed)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        base = carregar_base(args.raw)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    soja = ingest.ler_soja(args.raw_soja)
    if soja.empty:
        log.warning(
            "Sem exportação de soja em %s; a relação de troca fica de fora."
            " Rode: python -m src.ingest --inicio 2015-01 --serie soja",
            args.raw_soja,
        )
    tabelas = gerar(base, args.saida, soja)
    for nome, df in tabelas.items():
        log.info("%s: %d linhas", args.saida / f"{nome}.csv", len(df))
    log.info("%s: %.0f KB", args.saida / "dashboard.json", (args.saida / "dashboard.json").stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
