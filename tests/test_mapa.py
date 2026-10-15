"""Testes do gerador do mapa (src/mapa.py) e das tabelas de referência (data/ref): sem rede."""

import json

import pandas as pd
import pytest

from src import config, mapa, transform

RAIZ = config.RAIZ


def _quadrado(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def test_rasterizar_poligono_com_buraco_e_sem_antartida():
    geo = {
        "type": "FeatureCollection",
        "features": [
            # 6 x 6 graus com um buraco de 1,5 x 1,5 no meio da segunda linha de células
            {"properties": {"ISO_A3_EH": "XXX", "LABEL_X": 3, "LABEL_Y": 3, "CONTINENT": "South America"},
             "geometry": {"type": "Polygon", "coordinates": [_quadrado(0, 0, 6, 6), _quadrado(1.5, 3.0, 3.0, 4.5)]}},
            {"properties": {"ISO_A3_EH": "ATA", "CONTINENT": "Antarctica"},
             "geometry": {"type": "Polygon", "coordinates": [_quadrado(0, -60, 10, -50)]}},
            # França e Noruega vêm com ISO_A3 = -99 no Natural Earth: vale o ISO_A3_EH
            {"properties": {"ISO_A3": "-99", "ISO_A3_EH": "FRA", "CONTINENT": "Europe"},
             "geometry": {"type": "MultiPolygon", "coordinates": [[_quadrado(9, 0, 10.5, 1.5)]]}},
        ],
    }
    m = mapa.rasterizar(geo, passo=1.5, oeste=0, norte=6, sul=0)
    assert (m["colunas"], m["linhas"]) == (240, 4)
    assert m["paises"] == ["XXX", "FRA"] and m["rotulos"]["XXX"] == [3.0, 3.0]
    assert m["continentes"] == ["SA", "EU"]  # um por país, na ordem de paises
    assert m["pontos"] == 16 - 1 + 1  # 4 x 4 células, menos a do buraco, mais a da França
    assert m["grade"][1] == [0, 1, 0, 2, 2, 0]  # linha do buraco: coluna 0, buraco na 1, colunas 2 e 3
    pontos = mapa.expandir(m)
    assert len(pontos) == m["pontos"] and (6, 3, "FRA") in pontos and (1, 1, "XXX") not in pontos


def test_base_do_mapa_versionada_pequena_e_consistente():
    caminho = RAIZ / "dashboard" / "assets" / "mapa-pontos.json"
    assert caminho.stat().st_size < 40_000
    m = json.loads(caminho.read_text(encoding="utf-8"))
    assert "Natural Earth" in m["fonte"] and "domínio público" in m["fonte"]
    assert len(m["grade"]) == m["linhas"]
    total = 0
    for trincas in m["grade"]:
        assert len(trincas) % 3 == 0
        for i in range(0, len(trincas), 3):
            inicio, n, pais = trincas[i : i + 3]
            assert 0 <= inicio and inicio + n <= m["colunas"] and 0 <= pais < len(m["paises"])
            total += n
    assert total == m["pontos"] > 5000
    assert all(len(p) == 3 and p.isupper() for p in m["paises"]), "códigos ISO3"
    assert "BRA" in m["paises"] and "BRA" in m["rotulos"] and "ATA" not in m["paises"]
    # continente de cada país (Natural Earth): todos com um dos seis códigos
    assert len(m["continentes"]) == len(m["paises"]) and set(m["continentes"]) == set(mapa.NOMES_CONTINENTES)
    cont = dict(zip(m["paises"], m["continentes"]))
    assert (cont["BRA"], cont["RUS"], cont["CHN"], cont["CAN"], cont["MAR"]) == ("SA", "EU", "AS", "NA", "AF")


def test_coordenadas_das_urfs_pela_tabela_e_erro_para_urf_nova():
    portos = {"features": [{"properties": {"name": "Paranagua"}, "geometry": {"coordinates": [-48.5, -25.5]}}]}
    cidades = {"features": [{"properties": {"name": "Manaus", "adm0name": "Brazil", "adm1name": "Amazonas"},
                             "geometry": {"coordinates": [-60.0, -3.1]}}]}
    urfs = pd.DataFrame({"co_urf": ["0917800", "0227600"], "no_urf": ["PORTO DE PARANAGUA", "PORTO DE MANAUS"]})
    df = mapa.coordenadas_urfs(urfs, portos, cidades).set_index("co_urf")
    assert df.loc["0917800", ["lon", "lat", "porto"]].tolist() == [-48.5, -25.5, "Paranaguá"]
    assert df.loc["0227600", "fonte"].endswith("Populated Places")
    with pytest.raises(KeyError, match="sem lugar em URF_LOCAL"):
        mapa.coordenadas_urfs(pd.DataFrame({"co_urf": ["9999999"], "no_urf": ["NOVA"]}), portos, cidades)


# --- Cobertura das tabelas de referência sobre os dados versionados -------------------------
# Toda URF e todo país com volume relevante precisam de coordenada. Se um porto ou uma origem
# nova passar a ter volume, o teste aponta qual falta e como corrigir.


def test_toda_urf_relevante_tem_coordenada_no_brasil():
    urfs = mapa.ler_urfs().set_index("co_urf")
    faltam = sorted(set(mapa.urfs_relevantes()["co_urf"]) - set(urfs.index))
    assert not faltam, f"URFs sem coordenada: {faltam}. Acrescente em URF_LOCAL (src/mapa.py) e rode python -m src.mapa urfs"
    assert urfs["lon"].between(-74, -28).all() and urfs["lat"].between(-34, 6).all()
    assert urfs["porto"].str.len().gt(0).all()


def test_todo_pais_com_volume_relevante_tem_iso3_e_centroide():
    share = pd.read_csv(config.PROCESSED_DIR / "share_origem_ano.csv", dtype={"co_pais": str})
    rotas = pd.read_csv(config.PROCESSED_DIR / "rotas_ano.csv", dtype={"co_pais": str, "co_urf": str})
    relevantes = set(share.loc[share["toneladas"] >= 10_000, "co_pais"]) | set(rotas.loc[rotas["relevante"], "co_pais"])
    paises = mapa.ler_paises().set_index("co_pais")
    sem = sorted(c for c in relevantes if c not in paises.index or not paises.loc[c, "iso3"] or pd.isna(paises.loc[c, "lon"]))
    assert not sem, f"Países sem ISO3 ou centróide: {sem}. Rode python -m src.mapa paises --manter"
    sem_continente = sorted(c for c in relevantes if paises.loc[c, "continente"] not in mapa.NOMES_CONTINENTES)
    assert not sem_continente, f"Países sem continente: {sem_continente}. Rode python -m src.mapa paises --manter"
    assert paises.loc[list(relevantes), "lon"].between(-180, 180).all() and paises.loc[list(relevantes), "lat"].between(-60, 85).all()
    assert paises.loc["676", "iso3"] == "RUS" and paises.loc["105", "iso3"] == "BRA"


# --- Bloco "mapa" do dashboard.json -----------------------------------------------------------


def _bloco_versionado():
    return json.loads((config.PROCESSED_DIR / "dashboard.json").read_text(encoding="utf-8"))["mapa"]


def test_bloco_do_mapa_bate_com_as_tabelas_processadas():
    bloco = _bloco_versionado()
    share = pd.read_csv(config.PROCESSED_DIR / "share_origem_ano.csv", dtype={"co_pais": str})
    rotas = pd.read_csv(config.PROCESSED_DIR / "rotas_ano.csv", dtype={"co_pais": str, "co_urf": str})
    urfs = mapa.ler_urfs().set_index("co_urf")
    assert bloco["anos"] == sorted(int(a) for a in share["ano"].unique())
    for ano in bloco["anos"]:
        for grupo, d in bloco["dados"][str(ano)].items():
            s = share[(share["ano"] == ano) & (share["grupo"] == grupo)]
            # toda tonelada do ano está em alguma origem ou em "outros"
            assert sum(o[1] for o in d["origens"]) + d["outros_t"] == pytest.approx(s["toneladas"].sum(), abs=len(s) + 1)
            assert d["t"] == pytest.approx(s["toneladas"].sum(), abs=1)
            assert all(o[1] >= mapa.MINIMO_ORIGEM_T and o[0] in bloco["paises"] for o in d["origens"])
            # só rotas acima do corte ganham arco, e a soma por porto não passa do total do porto
            r = rotas[(rotas["ano"] == ano) & (rotas["grupo"] == grupo) & rotas["relevante"]]
            assert sum(x[2] for x in d["rotas"]) == pytest.approx(r[r["co_urf"].isin(urfs.index)]["toneladas"].sum(), abs=len(r) + 1)
            total_porto = {p: t for p, t in d["portos"]}
            for i, t in total_porto.items():
                assert sum(x[2] for x in d["rotas"] if x[1] == i) <= t + 1
    nomes = [p[0] for p in bloco["portos"]]
    assert len(nomes) == len(set(nomes)) and "Paranaguá" in nomes


def test_continentes_do_bloco_somam_as_origens_de_cada_continente():
    bloco = _bloco_versionado()
    share = pd.read_csv(config.PROCESSED_DIR / "share_origem_ano.csv", dtype={"co_pais": str})
    cont = mapa.ler_paises().drop_duplicates("co_pais").set_index("co_pais")["continente"]
    share = share.assign(cont=share["co_pais"].map(cont))
    assert bloco["continentes"] == mapa.NOMES_CONTINENTES
    assert all(p[4] in mapa.NOMES_CONTINENTES for p in bloco["paises"].values()), "toda origem do mapa tem continente"
    for ano in bloco["anos"]:
        for grupo, d in bloco["dados"][str(ano)].items():
            s = share[(share["ano"] == ano) & (share["grupo"] == grupo)]
            t = [c[1] for c in d["continentes"]]
            assert t == sorted(t, reverse=True)
            for codigo, toneladas, share_pct, var in d["continentes"]:
                c = s[s["cont"] == codigo]
                assert toneladas == pytest.approx(c["toneladas"].sum(), abs=1)
                assert share_pct == pytest.approx(100 * c["toneladas"].sum() / s["toneladas"].sum(), abs=0.06)
                ant = c["toneladas_ano_anterior"]
                if ant.notna().any() and ant.sum() > 0:
                    assert var == pytest.approx(100 * (c["toneladas"].sum() / ant.sum() - 1), abs=0.06)
                else:
                    assert var is None
            assert sum(c[2] for c in d["continentes"]) <= 100.3


def test_bloco_do_mapa_com_dados_sinteticos(tmp_path):
    from tests.test_transform import dois_anos  # mesmo conjunto dos testes do transform

    tabelas = transform.gerar(dois_anos(), tmp_path)
    paises = pd.DataFrame({"co_pais": ["149", "676", "160"], "no_pais": ["Canadá", "Rússia", "China"], "iso3": ["CAN", "RUS", "CHN"],
                           "lon": [-100.0, 45.0, 104.0], "lat": [56.0, 58.0, 35.0], "fonte": ["x"] * 3})
    urfs = pd.DataFrame({"co_urf": ["0917800"], "no_urf": ["PORTO DE PARANAGUA"], "porto": ["Paranaguá"], "uf": ["PR"],
                         "local_ne": ["Paranagua"], "lon": [-48.5], "lat": [-25.5], "fonte": ["x"], "observacao": [""]})
    bloco = mapa.bloco_dashboard(tabelas, paises, urfs, transform._num)
    assert bloco["dados"]["2025"]["K"]["continentes"] == []  # tabela de países sem a coluna: nenhum continente
    bloco = mapa.bloco_dashboard(tabelas, paises.assign(continente=["NA", "EU", "AS"]), urfs, transform._num)
    # K: Canadá 720 -> 600 t, Rússia 480 -> 600 t; N: China 120 -> 360 t, Rússia 0 -> 120 t
    assert bloco["dados"]["2025"]["K"]["continentes"] == [["EU", 600, 50.0, 25.0], ["NA", 600, 50.0, -16.7]]
    assert bloco["dados"]["2025"]["N"]["continentes"] == [["AS", 360, 75.0, 200.0], ["EU", 120, 25.0, None]]
    assert bloco["dados"]["2025"]["TOTAL"]["continentes"] == [["EU", 720, 42.9, 50.0], ["NA", 600, 35.7, -16.7], ["AS", 360, 21.4, 200.0]]
    assert all(c[3] is None for c in bloco["dados"]["2024"]["TOTAL"]["continentes"]), "primeiro ano: sem variação"
    assert all(p[4] in {"NA", "EU", "AS"} for p in bloco["paises"].values()) and bloco["colunas"]["paises"][4] == "continente"
    k = bloco["dados"]["2025"]["K"]
    assert [o[0] for o in k["origens"]] == sorted([o[0] for o in k["origens"]], key=lambda c: -dict((o[0], o[1]) for o in k["origens"])[c])
    assert bloco["portos"][0][:2] == ["Paranaguá", "PR"] and bloco["portos"][0][4] == ["0917800"]
    assert all(x[1] == 0 for x in k["rotas"])  # índice do único porto
    assert set(bloco["paises"]) <= {"149", "676", "160"}
