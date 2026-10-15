"""Testes das peças estáticas do dashboard (sem rede e sem navegador): fontes self-hosted e
referências externas da página. O mapa de pontos é testado em test_mapa.py."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DASH = RAIZ / "dashboard"


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


def test_anime_js_com_versao_fixa_e_sri():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<script src="https://cdn\.jsdelivr\.net/npm/animejs@([^/"]+)/[^"]+"([^>]*)>', html)
    assert tag and re.fullmatch(r"4\.\d+\.\d+", tag.group(1)), "anime.js v4 com versão fixa"
    assert 'integrity="sha384-' in tag.group(2) and 'crossorigin="anonymous"' in tag.group(2)
