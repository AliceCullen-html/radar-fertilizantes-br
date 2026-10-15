"""Testes das peças estáticas do dashboard (sem rede e sem navegador): mapa de pontos gerado
do Natural Earth, fontes self-hosted e referências externas da página."""

import importlib.util
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DASH = RAIZ / "dashboard"


def _script_mapa():
    spec = importlib.util.spec_from_file_location("gerar_mapa_pontos", RAIZ / "scripts" / "gerar_mapa_pontos.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _quadrado(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def test_rasterizar_poligono_com_buraco_e_sem_antartida():
    geo = {
        "type": "FeatureCollection",
        "features": [
            # 6 x 6 graus com um buraco de 1,5 x 1,5 no meio da segunda linha de células
            {"properties": {"ISO_A2_EH": "XX", "LABEL_X": 3, "LABEL_Y": 3, "NAME": "Xis"},
             "geometry": {"type": "Polygon", "coordinates": [_quadrado(0, 0, 6, 6), _quadrado(1.5, 3.0, 3.0, 4.5)]}},
            {"properties": {"ISO_A2_EH": "AQ", "CONTINENT": "Antarctica"},
             "geometry": {"type": "Polygon", "coordinates": [_quadrado(0, -60, 10, -50)]}},
        ],
    }
    m = _script_mapa().rasterizar(geo, passo=1.5, oeste=0, norte=6, sul=0)
    assert (m["colunas"], m["linhas"]) == (240, 4)
    assert m["paises"] == ["XX"] and m["rotulos"]["XX"] == [3.0, 3.0]
    assert m["pontos"] == 16 - 1  # 4 x 4 células, menos a do buraco
    assert m["grade"][1] == [0, 1, 0, 2, 2, 0]  # linha do buraco: coluna 0, buraco na 1, colunas 2 e 3


def test_mapa_versionado_e_pequeno_e_consistente():
    caminho = DASH / "assets" / "mapa-pontos.json"
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
    assert "BR" in m["paises"] and "BR" in m["rotulos"] and "AQ" not in m["paises"]


def test_fontes_self_hosted_com_licenca():
    fontes = DASH / "assets" / "fontes"
    css = (DASH / "assets" / "radar.css").read_text(encoding="utf-8")
    for arquivo in re.findall(r'url\("(fontes/[^"]+\.woff2)"\)', css):
        assert (DASH / "assets" / arquivo).exists(), arquivo
    assert len(re.findall(r"@font-face", css)) == 5
    for licenca in ("OFL-InterTight.txt", "OFL-JetBrainsMono.txt"):
        assert "SIL Open Font License" in (fontes / licenca).read_text(encoding="utf-8")


def test_nenhuma_chamada_a_terceiros_alem_do_jsdelivr():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    # carregamentos (src/href de script, link e @import/url no CSS): só o próprio site e o jsDelivr
    carregados = re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https?://[^"]+)"', html)
    assert carregados and all(u.startswith("https://cdn.jsdelivr.net/npm/") for u in carregados), carregados
    for arquivo in (DASH / "assets").glob("*.css"):
        assert not re.search(r"(?:url\(|@import)\s*[\"']?https?://", arquivo.read_text(encoding="utf-8")), arquivo
    for arquivo in (DASH / "assets").glob("*.js"):
        assert not re.search(r"fetch\(\s*[\"'`]https?://", arquivo.read_text(encoding="utf-8")), arquivo


def test_pagina_cita_as_fontes_e_a_base_cartografica():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    assert "Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX" in html
    assert "Base cartográfica: Natural Earth" in html
    assert "no porto" in html and "não é o preço ao produtor" in html.lower()
