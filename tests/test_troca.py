"""Testes da relação de troca soja x fertilizante (src/troca.py)."""

import json

import pandas as pd
import pytest

from src import config, ingest, transform, troca

KCL, UREIA, MAP = "31042090", "31021010", "31054000"


def fert(ano, mes, ncm=KCL, t=100.0, cif=32_000.0, grupo="K"):
    """Uma linha de importação no formato de transform.carregar_base."""
    return {
        "ano": ano,
        "mes": mes,
        "co_ncm": ncm,
        "grupo": grupo,
        "co_pais": "149",
        "no_pais": "Canadá",
        "sg_uf": "PR",
        "no_uf": "Paraná",
        "co_via": "01",
        "no_via": "MARITIMA",
        "co_urf": "0917800",
        "no_urf": "PORTO DE PARANAGUA",
        "toneladas": float(t),
        "kg_liquido": float(t) * 1000,
        "vl_fob": float(cif) * 0.9,
        "vl_frete": float(cif) * 0.1,
        "vl_seguro": 0.0,
        "vl_cif": float(cif),
    }


def soja(ano, mes, t=1_000.0, fob=400_000.0):
    return {"ano": ano, "mes": mes, "co_ncm": config.NCM_SOJA, "vl_fob": int(fob), "kg_liquido": int(t * 1000)}


def test_ncms_dos_produtos():
    assert {p: info["ncm"] for p, info in troca.PRODUTOS.items()} == {"kcl": KCL, "ureia": UREIA, "map": MAP}
    assert [info["grupo"] for info in troca.PRODUTOS.values()] == ["K", "N", "P"]


def test_relacao_e_cif_do_fertilizante_sobre_fob_da_soja():
    df = troca.relacao_troca(pd.DataFrame([fert(2025, 1)]), pd.DataFrame([soja(2025, 1)]))
    kcl = df[df["produto"] == "kcl"].iloc[0]
    assert kcl["preco_cif_fert_usd_t"] == pytest.approx(320)  # 32.000 / 100 t
    assert kcl["preco_fob_soja_usd_t"] == pytest.approx(400)  # 400.000 / 1.000 t
    assert kcl["t_soja_por_t"] == pytest.approx(0.8)
    assert kcl["sacas_soja_por_t"] == pytest.approx(800 / 60)
    assert list(df.columns) == troca.COLUNAS


def test_precos_sao_ponderados_pelo_peso_e_ignoram_outros_ncms():
    base = pd.DataFrame(
        [
            fert(2025, 1, t=100, cif=30_000),  # 300 US$/t
            fert(2025, 1, t=300, cif=120_000),  # 400 US$/t
            fert(2025, 1, ncm="31042010", t=1_000, cif=10_000),  # outro KCl: fora da relação
        ]
    )
    sj = pd.DataFrame([soja(2025, 1, t=1_000, fob=300_000), soja(2025, 1, t=3_000, fob=1_500_000)])
    kcl = troca.relacao_troca(base, sj).query("produto == 'kcl'").iloc[0]
    assert kcl["toneladas_fert"] == pytest.approx(400)
    assert kcl["preco_cif_fert_usd_t"] == pytest.approx(375)  # 150.000 / 400, não a média de 300 e 400
    assert kcl["preco_fob_soja_usd_t"] == pytest.approx(450)  # 1.800.000 / 4.000
    assert kcl["t_soja_por_t"] == pytest.approx(375 / 450)


def test_mes_sem_compra_ou_sem_soja_fica_vazio():
    base = pd.DataFrame([fert(2025, 1), fert(2025, 2), fert(2025, 2, ncm=UREIA, grupo="N", cif=50_000)])
    df = troca.relacao_troca(base, pd.DataFrame([soja(2025, 2)]))
    assert len(df) == 6  # 2 meses x 3 produtos, mesmo sem compra
    razao = df.set_index(["periodo", "produto"])["t_soja_por_t"]
    assert pd.isna(razao[("2025-01", "kcl")])  # sem soja em janeiro
    assert pd.isna(razao[("2025-02", "map")])  # sem MAP em fevereiro
    assert razao[("2025-02", "ureia")] == pytest.approx(500 / 400)


def test_media_historica_e_a_media_simples_das_relacoes_mensais():
    base = pd.DataFrame([fert(2025, 1, cif=40_000), fert(2025, 2, t=900, cif=720_000), fert(2025, 3)])
    sj = pd.DataFrame([soja(2025, 1), soja(2025, 2), soja(2025, 3, fob=0)])
    df = troca.relacao_troca(base, sj)
    medias = troca.media_historica(df)
    # 1,0 em jan e 2,0 em fev; em março a soja tem preço zero: sem relação, fora da média
    assert medias["kcl"] == pytest.approx(1.5)
    assert pd.isna(medias["map"])


def test_bloco_dashboard_alinhado_aos_periodos():
    base = pd.DataFrame([fert(2025, 1), fert(2025, 2, cif=48_000)])
    df = troca.relacao_troca(base, pd.DataFrame([soja(2025, 1), soja(2025, 2)]))
    bloco = troca.bloco_dashboard(df, ["2024-12", "2025-01", "2025-02"], transform._num)
    kcl = bloco["produtos"]["kcl"]
    assert bloco["periodos"] == ["2024-12", "2025-01", "2025-02"]
    assert kcl["razao"] == [None, 0.8, 1.2] and kcl["cif"] == [None, 320.0, 480.0]
    assert kcl["media"] == pytest.approx(1.0) and kcl["media_periodo"] == ["2025-01", "2025-02"]
    assert bloco["soja_fob"] == [None, 400.0, 400.0] and bloco["kg_saca"] == 60
    assert bloco["produtos"]["map"]["media"] is None and bloco["produtos"]["map"]["n_meses"] == 0
    json.dumps(bloco, allow_nan=False)


def test_gerar_inclui_a_troca_so_com_soja(tmp_path):
    base = pd.DataFrame([fert(2025, m) for m in range(1, 13)])
    transform.gerar(base, tmp_path / "sem")
    assert "troca" not in json.loads((tmp_path / "sem" / "dashboard.json").read_text(encoding="utf-8"))
    assert not (tmp_path / "sem" / "troca_mensal.csv").exists()

    transform.gerar(base, tmp_path / "com", pd.DataFrame([soja(2025, m) for m in range(1, 13)]))
    painel = json.loads((tmp_path / "com" / "dashboard.json").read_text(encoding="utf-8"))
    assert painel["troca"]["periodos"] == painel["mensal"]["periodos"]
    assert painel["troca"]["produtos"]["kcl"]["razao"][0] == 0.8
    csv = pd.read_csv(tmp_path / "com" / "troca_mensal.csv", dtype={"co_ncm": str})
    assert len(csv) == 36 and csv["t_soja_por_t"].dropna().eq(0.8).all()


def test_dados_versionados_da_soja():
    if not any(config.RAW_SOJA_DIR.glob(f"ano=*/{config.RAW_SOJA_ARQUIVO}")):
        pytest.skip("sem dados em data/raw_soja")
    sj = ingest.ler_soja()
    precos = troca.precos_soja(sj)
    assert (sj["co_ncm"] == config.NCM_SOJA).all() and not sj.duplicated(["ano", "mes"]).any()
    # soja em grão exportada: entre US$ 200 e 800/t em qualquer mês desde 2015
    assert precos["preco_fob_soja_usd_t"].between(200, 800).all()
