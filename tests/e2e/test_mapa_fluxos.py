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


def _abrir(navegador, servidor, largura, tema, reduzido=False, hash_="", altura=900):
    ctx = navegador.new_context(viewport={"width": largura, "height": altura}, color_scheme=tema, reduced_motion="reduce" if reduzido else "no-preference")
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


def _mi(t):
    return f"{t / 1e6:.1f}".replace(".", ",")


@pytest.mark.parametrize("largura", [1440, 390])
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_mapa_de_fluxos_sem_erros_e_sem_rolagem_lateral(navegador, servidor, largura, tema):
    ctx, pg, erros = _abrir(navegador, servidor, largura, tema)
    dados = pg.evaluate("radarDados.mapa")
    ano = pg.evaluate("Number(document.getElementById('fluxos-ano-valor').firstChild.textContent)")
    r = dados["dados"][str(ano)]["TOTAL"]
    # resumo: total do ano e grupo, e o ranking das origens no painel
    assert pg.evaluate("document.getElementById('fluxos-total').textContent") == _mi(r["t"])
    assert str(ano) in pg.evaluate("document.getElementById('fluxos-resumo-kicker').textContent")
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == "Principais origens"
    maior = r["origens"][0]
    nome = dados["paises"][maior[0]][0]
    assert pg.evaluate("document.querySelector('#fluxos-lista .linha .nome').textContent") == nome
    # visão geral: um arco por origem até o Brasil, as mesmas do ranking
    linhas = pg.evaluate("document.querySelectorAll('#fluxos-lista .linha[data-chave^=\"o:\"]').length")
    assert 5 <= linhas <= 8
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .fluxo').length") == linhas
    # clique na maior origem: painel com os números do dashboard.json e um arco por rota acima do corte
    pg.evaluate(CLIQUE, f"#fluxos-svg .marcador.origem[aria-label^='{nome}:']")
    pg.wait_for_timeout(1800)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == nome
    assert pg.evaluate("location.hash").endswith(f"origem={maior[0]}")
    rotas = [x for x in r["rotas"] if x[0] == maior[0]]
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .fluxo').length") == len(rotas)
    assert nome in pg.evaluate("document.getElementById('fluxos-anuncio').textContent")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(1500)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == "Principais origens"
    assert "origem=" not in pg.evaluate("location.hash")
    assert pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0
    assert erros == []
    ctx.close()


def test_linha_do_tempo_ranking_e_filtro_de_grupo_da_pagina(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    pg.evaluate(CLIQUE, "#fluxos-grupo button[data-grupo='K']")
    assert pg.evaluate("document.querySelector('#filtro-grupo .chip[aria-pressed=true]').dataset.grupo") == "K"
    assert pg.evaluate("document.getElementById('fluxos-app').dataset.grupo") == "K"
    dados = pg.evaluate("radarDados.mapa")
    for ano in (2021, 2022, 2023):
        pg.evaluate(f"() => {{ const f = document.getElementById('fluxos-ano'); f.value = String(radarDados.mapa.anos.indexOf({ano})); f.dispatchEvent(new Event('input')); }}")
        pg.wait_for_timeout(900)
        r = dados["dados"][str(ano)]["K"]
        assert pg.evaluate("document.getElementById('fluxos-total').textContent") == _mi(r["t"])
        nomes = pg.evaluate("[...document.querySelectorAll('#fluxos-lista .linha[data-chave] .nome')].map(e => e.textContent)")
        assert nomes == [dados["paises"][o[0]][0] for o in r["origens"][: len(nomes)]]
        assert "grupo=K" in pg.evaluate("location.hash") and f"ano={ano}" in pg.evaluate("location.hash")
        assert pg.evaluate("document.querySelector('#fluxos-marcas li.atual').dataset.ano") == str(ano)
    # aba de portos e clique num porto: a visão se inverte para as origens que chegam nele
    pg.evaluate(CLIQUE, "#fluxos-abas button[data-visao='portos']")
    pg.wait_for_timeout(600)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == "Principais portos"
    pg.evaluate(CLIQUE, "#fluxos-lista .linha")
    pg.wait_for_timeout(1800)
    assert pg.evaluate("document.getElementById('fluxos-painel-kicker').textContent").startswith("Porto")
    assert "porto=" in pg.evaluate("location.hash")
    assert erros == []
    ctx.close()


def test_destaque_ao_passar_o_mouse_no_ranking(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    pg.hover("#fluxos-lista li:nth-child(2) .linha")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.getElementById('fluxos-svg').classList.contains('com-destaque')")
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .fluxo.destacado').length") == 1
    pg.mouse.move(2, 2)
    pg.wait_for_timeout(300)
    assert not pg.evaluate("document.getElementById('fluxos-svg').classList.contains('com-destaque')")
    assert erros == []
    ctx.close()


def test_teclado_e_movimento_reduzido(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True, hash_="#grupo=K&ano=2025")
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .navio').length") == 0  # sem cometas
    assert pg.evaluate("document.getElementById('fluxos-svg').getAnimations({subtree: true}).length") == 0  # nem anéis pulsando
    # um só ponto de parada do Tab no mapa; as setas andam entre os marcadores
    assert pg.evaluate("document.querySelectorAll('#fluxos-svg .marcador[tabindex=\"0\"]').length") == 1
    pg.focus("#fluxos-svg .marcador[tabindex='0']")
    primeiro = pg.evaluate("document.activeElement.getAttribute('aria-label')")
    pg.keyboard.press("ArrowRight")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") != primeiro
    pg.focus("#fluxos-svg .marcador.origem")
    rotulo = pg.evaluate("document.activeElement.getAttribute('aria-label')")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.getElementById('fluxos-painel-titulo').textContent") == rotulo.split(":")[0]
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == rotulo  # o foco continua no marcador
    assert pg.evaluate("document.getElementById('fluxos-anuncio').getAttribute('aria-live')") == "polite"
    assert rotulo.split(":")[0] in pg.evaluate("document.getElementById('fluxos-anuncio').textContent")
    # as linhas do painel também são botões
    pg.focus("#fluxos-lista .linha")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.getElementById('fluxos-painel-kicker').textContent").startswith("Porto")
    assert pg.evaluate("document.activeElement.id") == "fluxos-painel-titulo"  # o foco não cai no <body>
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.activeElement.tagName") != "BODY"
    assert erros == []
    ctx.close()


def test_tabela_alternativa_tem_as_origens_do_ano(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    linhas = pg.evaluate("document.querySelectorAll('#tabela-fluxos table:first-of-type tbody tr').length")
    dados = pg.evaluate("radarDados.mapa")
    ano = pg.evaluate("Number(document.getElementById('fluxos-ano-valor').firstChild.textContent)")
    assert linhas == len(dados["dados"][str(ano)]["TOTAL"]["origens"]) + 1  # + "Outros"
    assert json.dumps(erros) == "[]"
    ctx.close()


def test_play_percorre_a_serie_e_ano_parcial_mostra_o_corte_proporcional(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    dados = pg.evaluate("radarDados.mapa")
    anos, meses = dados["anos"], dados["n_meses"]
    pg.evaluate(CLIQUE, "#fluxos-play")
    pg.wait_for_timeout(200)
    assert pg.evaluate("Number(document.getElementById('fluxos-ano-valor').firstChild.textContent)") == anos[0]
    pg.evaluate(CLIQUE, "#fluxos-play")  # pausa
    # ano parcial: o texto das rotas usa o corte proporcional do bloco, não 50 mil t
    if meses[-1] < 12:
        pg.evaluate(f"() => {{ const f = document.getElementById('fluxos-ano'); f.value = '{len(anos) - 1}'; f.dispatchEvent(new Event('input')); }}")
        pg.evaluate(CLIQUE, "#fluxos-lista .linha")
        pg.wait_for_timeout(300)
        corte = dados["corte_rota_t"][-1] / 1000
        titulo = pg.evaluate("document.getElementById('fluxos-lista-titulo').textContent")
        assert f"{corte:g}".replace(".", ",") + " mil t em jan" in titulo
    assert erros == []
    ctx.close()


def test_menu_rola_suave_ate_a_secao_e_revela_o_titulo(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    pg.evaluate("window.scrollTo(0, 0)")
    pg.wait_for_timeout(300)
    ys = pg.evaluate("""() => new Promise(r => { const ys = []; const t0 = performance.now();
      document.querySelector('#nav-secoes a[data-secao=precos]').click();
      const tic = () => { ys.push(scrollY); if (performance.now() - t0 < 1800) requestAnimationFrame(tic); else r(ys); }; tic(); })""")
    assert all(b >= a for a, b in zip(ys, ys[1:])), "a rolagem anda sempre para baixo, sem trancos para trás"
    assert len(set(ys)) > 10, "a rolagem é animada, não um salto"
    topo = pg.evaluate("document.getElementById('precos').getBoundingClientRect().top")
    assert 40 <= topo <= 90  # parou no começo da seção, abaixo da barra
    pg.wait_for_timeout(1200)
    assert pg.evaluate("document.querySelector('#nav-secoes a[aria-current]').dataset.secao") == "precos"
    assert pg.evaluate("document.querySelector('#precos .kicker').textContent").endswith("Preços")
    assert pg.evaluate("document.activeElement.id") == "t-precos"
    assert float(pg.evaluate("document.querySelector('.barra-progresso').style.transform").strip("scaleX()")) > 0.3
    assert erros == []
    ctx.close()
    # com reduzir movimento, nada fica escondido à espera da entrada
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    assert not pg.evaluate("document.documentElement.classList.contains('movimento')")
    assert pg.evaluate("document.querySelectorAll('.preparado').length") == 0
    assert erros == []
    ctx.close()


# corte à direita do clip-path inline ("inset(0%)" = inteiro; sem estilo = sem animação)
CORTES = """() => [...document.querySelectorAll('.grafico, .sankey, #fluxos-palco')].map((g) => {
  const m = g.style.clipPath.match(/inset\\(([^)]*)\\)/);
  if (!m) return null;
  const partes = m[1].replace(/ round .*/, '').split(' ').map(parseFloat);
  return partes.length > 1 ? partes[1] : partes[0];
})"""


def test_rolagem_abre_os_graficos_e_nada_fica_cortado_no_fim_da_pagina(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    topo = "document.getElementById('grafico-toneladas').parentElement.getBoundingClientRect().top + scrollY"
    pg.evaluate(f"window.scrollTo(0, {topo} - innerHeight + 40)")
    pg.wait_for_timeout(1500)
    assert pg.evaluate(CORTES)[0] > 80, "o gráfico que mal entrou na tela ainda está quase todo fechado"
    pg.evaluate(f"window.scrollTo(0, {topo} - innerHeight * 0.3)")
    pg.wait_for_function(f"({CORTES})()[0] < 0.5", timeout=15_000)
    # o hero sai de cena com a rolagem: o texto some e o mapa cresce
    pg.evaluate("window.scrollTo(0, document.querySelector('.hero').offsetHeight * 0.6)")
    pg.wait_for_function("getComputedStyle(document.querySelector('.hero-conteudo')).opacity < 0.7", timeout=15_000)
    assert "matrix" in pg.evaluate("getComputedStyle(document.getElementById('mapa-hero')).transform")
    assert erros == []
    ctx.close()
    # numa tela alta, os últimos gráficos não chegam a 65% da altura: abrem inteiros na rolagem máxima
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", altura=1600)
    pg.evaluate("""async () => { const fim = document.documentElement.scrollHeight;
      for (let y = 0; y <= fim; y += 200) { scrollTo(0, y); await new Promise((r) => setTimeout(r, 30)); } scrollTo(0, fim); }""")
    pg.wait_for_function(f"({CORTES})().every((c) => c !== null && c < 0.5)", timeout=20_000)
    assert erros == []
    ctx.close()
    # com reduzir movimento, nenhum corte nem deslocamento ligado à rolagem
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    pg.evaluate("window.scrollTo(0, 1200)")
    pg.wait_for_timeout(400)
    assert pg.evaluate(CORTES) == [None] * len(pg.evaluate(CORTES))
    assert pg.evaluate("document.querySelector('.fundo-pontos').style.transform") == ""
    assert erros == []
    ctx.close()
