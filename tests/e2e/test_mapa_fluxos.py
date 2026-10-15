"""Cenário de navegador do mapa de fluxos (só em desenvolvimento).

Roda com Playwright e Chromium, servindo o repositório em localhost. As bibliotecas do CDN
(Chart.js e anime.js) vêm de cópias locais, porque os testes não acessam a rede:

    RADAR_E2E_LIBS=/caminho/com/chart.umd.min.js/e/anime.umd.min.js python -m pytest tests/e2e

Sem o Playwright ou sem RADAR_E2E_LIBS, o teste é pulado (como no CI).
"""

import functools
import http.server
import json
import os
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
LIBS = os.environ.get("RADAR_E2E_LIBS")
if not LIBS:
    pytest.skip("defina RADAR_E2E_LIBS com chart.umd.min.js e anime.umd.min.js", allow_module_level=True)

RAIZ = Path(__file__).resolve().parents[2]
CHROMIUM = os.environ.get("RADAR_E2E_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
CLIQUE = "(sel) => document.querySelector(sel).dispatchEvent(new MouseEvent('click', {bubbles: true}))"


@pytest.fixture(scope="module")
def servidor():
    manipulador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RAIZ))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manipulador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/dashboard/"
    srv.shutdown()


@pytest.fixture(scope="module")
def navegador():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM if Path(CHROMIUM).exists() else None)
        yield b
        b.close()


def _cdn(rota):
    for nome, arquivo in (("chart.js", "chart.umd.min.js"), ("animejs", "anime.umd.min.js")):
        if nome in rota.request.url:
            return rota.fulfill(path=str(Path(LIBS) / arquivo), content_type="application/javascript", headers={"Access-Control-Allow-Origin": "*"})
    return rota.abort()


def _abrir(navegador, servidor, largura, tema, reduzido=False, hash_=""):
    ctx = navegador.new_context(viewport={"width": largura, "height": 900}, color_scheme=tema, reduced_motion="reduce" if reduzido else "no-preference")
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("console", lambda m: erros.append(m.text) if m.type == "error" and "404" not in m.text else None)
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pg.goto(servidor + hash_)
    pg.wait_for_function("window.anime && window.radarDados", timeout=20_000)
    pg.evaluate("document.getElementById('fluxos').scrollIntoView({block: 'start'})")
    pg.wait_for_function("document.querySelectorAll('#fluxos-svg .arco').length > 0", timeout=20_000)
    pg.wait_for_timeout(300 if reduzido else 1800)
    return ctx, pg, erros


@pytest.mark.parametrize("largura", [1440, 390])
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_mapa_de_fluxos_sem_erros_e_sem_rolagem_lateral(navegador, servidor, largura, tema):
    ctx, pg, erros = _abrir(navegador, servidor, largura, tema)
    dados = pg.evaluate("radarDados.mapa")
    ano = pg.evaluate("Number(document.getElementById('fluxos-ano-valor').textContent.slice(0, 4))")
    total = dados["dados"][str(ano)]["TOTAL"]["t"]
    assert pg.evaluate("document.getElementById('fluxos-chip-brasil').textContent").endswith(f"importadas em {ano}")
    assert f"{total / 1e6:.1f}".replace(".", ",") in pg.evaluate("document.getElementById('fluxos-chip-brasil').textContent")
    # clique na maior origem: painel com os números do dashboard.json
    maior = dados["dados"][str(ano)]["TOTAL"]["origens"][0]
    nome = dados["paises"][maior[0]][0]
    pg.evaluate(CLIQUE, f"#fluxos-svg .marcador.origem[aria-label^='{nome}:']")
    pg.wait_for_timeout(1800)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == nome
    assert pg.evaluate("location.hash").endswith(f"origem={maior[0]}")
    rotas = [r for r in dados["dados"][str(ano)]["TOTAL"]["rotas"] if r[0] == maior[0]]
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .arco').length") == len(rotas)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(1500)
    assert pg.evaluate("document.getElementById('fluxos-painel').hidden")
    assert pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0
    assert erros == []
    ctx.close()


def test_linha_do_tempo_e_filtro_de_grupo_da_pagina(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    pg.evaluate(CLIQUE, "#fluxos-grupo button[data-grupo='K']")
    assert pg.evaluate("document.querySelector('#filtro-grupo .chip[aria-pressed=true]').dataset.grupo") == "K"
    dados = pg.evaluate("radarDados.mapa")
    for ano in (2021, 2022, 2023):
        pg.evaluate(f"() => {{ const f = document.getElementById('fluxos-ano'); f.value = String(radarDados.mapa.anos.indexOf({ano})); f.dispatchEvent(new Event('input')); }}")
        pg.wait_for_timeout(900)
        chip = pg.evaluate("document.getElementById('fluxos-chip-brasil').textContent")
        assert f"{dados['dados'][str(ano)]['K']['t'] / 1e6:.1f}".replace(".", ",") in chip and str(ano) in chip
        assert "grupo=K" in pg.evaluate("location.hash") and f"ano={ano}" in pg.evaluate("location.hash")
    # porto: a visão se inverte para as origens que chegam nele
    pg.evaluate(CLIQUE, "#fluxos-svg .marcador.porto")
    pg.wait_for_timeout(1800)
    assert pg.evaluate("document.getElementById('fluxos-painel-kicker').textContent").startswith("Porto")
    assert erros == []
    ctx.close()


def test_teclado_e_movimento_reduzido(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True, hash_="#grupo=K&ano=2025")
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .navio').length") == 0  # sem navios
    pg.focus("#fluxos-svg .marcador.origem")
    rotulo = pg.evaluate("document.activeElement.getAttribute('aria-label')")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == rotulo.split(":")[0]
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") is not None  # o foco continua no marcador
    assert pg.evaluate("document.getElementById('fluxos-painel').getAttribute('aria-live')") == "polite"
    assert erros == []
    ctx.close()


def test_tabela_alternativa_tem_as_origens_do_ano(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    linhas = pg.evaluate("document.querySelectorAll('#tabela-fluxos table:first-of-type tbody tr').length")
    dados = pg.evaluate("radarDados.mapa")
    ano = pg.evaluate("Number(document.getElementById('fluxos-ano-valor').textContent.slice(0, 4))")
    assert linhas == len(dados["dados"][str(ano)]["TOTAL"]["origens"]) + 1  # + "Outros"
    assert json.dumps(erros) == "[]"
    ctx.close()
