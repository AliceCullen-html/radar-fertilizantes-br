"""Cenários de navegador do dashboard: mapa de fluxos, hero, painéis e cartões (só em desenvolvimento).

Roda com Playwright e Chromium, servindo o repositório em localhost. As bibliotecas do CDN
(Chart.js e anime.js) vêm de cópias locais, porque os testes não acessam a rede:

    RADAR_E2E_LIBS=/caminho/com/chart.umd.min.js/e/anime.umd.min.js python -m pytest tests/e2e

Sem o Playwright ou sem RADAR_E2E_LIBS, o teste é pulado (como no CI).
"""

import functools
import http.server
import json
import math
import os
import re
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


# corte à direita do clip-path inline do palco do mapa de fluxos ("inset(0%)" = inteiro; sem estilo = sem animação)
CORTES = """() => [...document.querySelectorAll('#fluxos-palco')].map((g) => {
  const m = g.style.clipPath.match(/inset\\(([^)]*)\\)/);
  if (!m) return null;
  const partes = m[1].replace(/ round .*/, '').split(' ').map(parseFloat);
  return partes.length > 1 ? partes[1] : partes[0];
})"""


def test_graficos_se_desenham_ao_aparecer_e_nada_fica_por_desenhar_no_fim_da_pagina(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    entrada = "document.getElementById('grafico-toneladas').dataset.entrada"
    topo = "document.getElementById('grafico-toneladas').parentElement.getBoundingClientRect().top + scrollY"
    pg.evaluate(f"window.scrollTo(0, {topo} - innerHeight + 30)")
    pg.wait_for_timeout(600)
    assert pg.evaluate(entrada) == "pendente", "o gráfico que mal entrou na tela ainda não se desenhou"
    assert pg.evaluate("window.radarDados && document.querySelectorAll('canvas[data-entrada]').length") >= 8
    pg.evaluate(f"window.scrollTo(0, {topo} - innerHeight * 0.3)")
    pg.wait_for_function(f"{entrada} === 'feita'", timeout=10_000)
    # trocar o grupo atualiza o gráfico no lugar (o mesmo objeto do Chart.js), sem refazer a entrada
    antes = pg.evaluate("Chart.getChart('grafico-toneladas').id")
    pg.click("#filtro-grupo .chip[data-grupo=K]")
    pg.wait_for_timeout(300)
    assert pg.evaluate("Chart.getChart('grafico-toneladas').id") == antes
    assert pg.evaluate("Chart.getChart('grafico-toneladas').data.datasets.map((d) => d.label)") == ["Potássicos"]
    # o hero sai de cena com a rolagem: o texto some e o mapa cresce
    pg.evaluate("window.scrollTo(0, document.querySelector('.hero').offsetHeight * 0.6)")
    pg.wait_for_function("getComputedStyle(document.querySelector('.hero-conteudo')).opacity < 0.7", timeout=15_000)
    assert "matrix" in pg.evaluate("getComputedStyle(document.getElementById('mapa-hero')).transform")
    assert erros == []
    ctx.close()
    # numa tela alta, rolando até o fim, todos os gráficos se desenham
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", altura=1600)
    pg.evaluate("""async () => { const fim = document.documentElement.scrollHeight;
      for (let y = 0; y <= fim; y += 200) { scrollTo(0, y); await new Promise((r) => setTimeout(r, 30)); } scrollTo(0, fim); }""")
    pg.wait_for_function("[...document.querySelectorAll('canvas[data-entrada]')].every((c) => c.dataset.entrada === 'feita' || c.closest('.oculto'))", timeout=20_000)
    pg.wait_for_function(f"({CORTES})().every((c) => c !== null && c < 0.5)", timeout=20_000)  # o palco do mapa abriu inteiro
    assert erros == []
    ctx.close()
    # com reduzir movimento, nada espera para aparecer nem se desloca com a rolagem
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light", reduzido=True)
    pg.evaluate("window.scrollTo(0, 1200)")
    pg.wait_for_timeout(400)
    assert pg.evaluate("document.querySelectorAll('canvas[data-entrada]').length") == 0
    assert pg.evaluate(CORTES) == [None]
    assert pg.evaluate("document.querySelector('.fundo-pontos').style.transform") == ""
    assert erros == []
    ctx.close()


def _pagina(navegador, servidor, largura=1440, tema="light", reduzido=False, altura=900):
    ctx = navegador.new_context(viewport={"width": largura, "height": altura}, color_scheme=tema, reduced_motion="reduce" if reduzido else "no-preference")
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("console", lambda m: erros.append(m.text) if m.type == "error" and "404" not in m.text else None)
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pg.goto(servidor)
    pg.wait_for_function("window.anime && window.radarDados", timeout=20_000)
    return ctx, pg, erros


# as maiores origens do último ano completo, pelos dados (nunca fixas no teste)
MAIORES = """() => { const d = window.radarDados, a = d.anual; let i = a.anos.length - 1;
  while (a.n_meses[i] !== 12) i--;
  const ano = a.anos[i];
  return { ano, nomes: d.mapa.dados[String(ano)].TOTAL.origens.slice(0, 6).map((o) => d.mapa.paises[o[0]][0]) }; }"""
ROTAS = """() => ({ rotas: [...document.querySelectorAll('#rotas-hero .rota')].map((r) => [r.getAttribute('stroke-dasharray'), r.getAttribute('stroke-dashoffset')]),
  pulsos: [...document.querySelectorAll('#rotas-hero .rastro')].map((p) => p.getAttribute('stroke-dasharray') && p.getAttribute('stroke-dashoffset')),
  rotulos: [...document.querySelectorAll('#rotas-hero .rotulo-rota .n')].map((t) => t.textContent),
  legenda: document.getElementById('legenda-rotas-hero').textContent })"""


def test_rotas_do_hero_se_desenham_das_maiores_origens_e_tem_cometas(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    maiores = pg.evaluate(MAIORES)
    pg.wait_for_function("document.querySelectorAll('#rotas-hero .rastro[stroke-dashoffset]').length > 0", timeout=15_000)
    r = pg.evaluate(ROTAS)
    assert len(r["rotas"]) == 6
    assert all(d in (None, "1000 0") and o in (None, "0") for d, o in r["rotas"]), "todos os arcos terminam inteiros"
    assert r["rotulos"] == maiores["nomes"][:3]
    assert r["legenda"] == f"6 maiores origens em {maiores['ano']}"
    antes = pg.evaluate(ROTAS)["pulsos"]
    pg.wait_for_timeout(500)
    assert pg.evaluate(ROTAS)["pulsos"] != antes, "os cometas correm pelos arcos"
    # fora da tela, os cometas param
    pg.evaluate("window.scrollTo(0, document.querySelector('.hero').offsetHeight + 400)")
    pg.wait_for_timeout(500)
    parado = pg.evaluate(ROTAS)["pulsos"]
    pg.wait_for_timeout(500)
    assert pg.evaluate(ROTAS)["pulsos"] == parado
    assert erros == []
    ctx.close()
    # celular: menos arcos e sem rótulos; reduzir movimento: arcos prontos, sem cometas
    ctx, pg, erros = _pagina(navegador, servidor, largura=390, reduzido=True)
    pg.wait_for_function("document.querySelectorAll('#rotas-hero .rota').length > 0", timeout=15_000)
    pg.wait_for_timeout(600)
    r = pg.evaluate(ROTAS)
    assert len(r["rotas"]) == 4 and r["rotulos"] == []
    assert all(d is None for d, _ in r["rotas"]) and all(p is None for p in r["pulsos"])
    assert erros == []
    ctx.close()


SANKEY = """() => { const s = document.querySelector('#sankey svg');
  return { mascara: !!s?.querySelector('g[mask]'), escondidos: [...(s?.querySelectorAll('rect, text') ?? [])].filter((e) => e.style.opacity === '0' || e.style.transform.includes('scaleY(0)')).length }; }"""


def test_fitas_do_diagrama_de_rotas_escorrem_ao_aparecer_e_ao_trocar_o_ano(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    pg.wait_for_function("!!document.querySelector('#sankey svg')", timeout=15_000)
    assert pg.evaluate(SANKEY)["mascara"], "longe da tela, o fluxo espera (fitas atrás da máscara)"
    pg.evaluate("document.getElementById('sankey').scrollIntoView({block: 'center'})")
    pg.wait_for_function(f"(() => {{ const e = ({SANKEY})(); return !e.mascara && e.escondidos === 0; }})()", timeout=15_000)
    # trocar o ano refaz o fluxo; redimensionar a caixa, não
    pg.select_option("#filtro-ano-rotas", index=1)
    assert pg.evaluate(SANKEY)["mascara"]
    pg.wait_for_function(f"!({SANKEY})().mascara", timeout=15_000)
    pg.set_viewport_size({"width": 1200, "height": 900})
    pg.wait_for_timeout(500)
    assert pg.evaluate(SANKEY) == {"mascara": False, "escondidos": 0}
    assert erros == []
    ctx.close()
    ctx, pg, erros = _pagina(navegador, servidor, reduzido=True)
    pg.wait_for_function("!!document.querySelector('#sankey svg')", timeout=15_000)
    assert pg.evaluate(SANKEY) == {"mascara": False, "escondidos": 0}
    assert erros == []
    ctx.close()


def test_rotulos_do_mapa_na_fonte_do_site_e_sem_contorno(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "dark")
    estilos = pg.evaluate("""() => [...document.querySelectorAll('#fluxos-svg .rotulo')].filter((t) => t.style.display !== 'none').map((t) => {
      const c = getComputedStyle(t), n = getComputedStyle(t.querySelector('.nome')), v = t.querySelector('.valor');
      return { stroke: c.stroke, filtro: c.filter, nome: n.fontFamily, valor: v ? getComputedStyle(v).fontFamily : n.fontFamily }; })""")
    assert len(estilos) >= 3
    for e in estilos:
        assert e["stroke"] == "none" and "fluxos-halo" in e["filtro"]
        assert e["nome"].startswith('"Inter Tight"') and e["valor"].startswith('"Inter Tight"')
    assert erros == []
    ctx.close()


# o que o painel do continente deve mostrar, pelos dados (último ano completo, bloco "mapa")
ESPERADO = """(cod) => { const d = window.radarDados, a = d.anual, m = d.mapa; let i = a.anos.length - 1;
  while (a.n_meses[i] !== 12) i--;
  const ano = a.anos[i], dados = m.dados[String(ano)], linha = dados.TOTAL.continentes.find((c) => c[0] === cod);
  const compacto = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 });
  const um = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return { ano, nome: m.continentes[cod], t: compacto.format(linha[1]) + ' t', share: um.format(linha[2]) + '%',
    paises: dados.TOTAL.origens.filter((o) => m.paises[o[0]][4] === cod).slice(0, 5).map((o) => m.paises[o[0]][0]),
    ordem: dados.TOTAL.continentes.map((c) => c[0]) }; }"""
PAINEL = """() => ({ aberto: window.radarMapaHero.continenteAberto(), visivel: !document.getElementById('painel-continente').hidden,
  titulo: document.getElementById('pc-titulo').textContent, t: document.getElementById('pc-t').textContent,
  share: document.getElementById('pc-share').textContent, var: document.getElementById('pc-var').textContent,
  grupos: [...document.querySelectorAll('#pc-grupos li b')].map((b) => parseFloat(b.textContent.replace(',', '.'))),
  paises: [...document.querySelectorAll('#pc-paises .nome')].map((n) => n.textContent),
  rotas: [...document.querySelectorAll('#rotas-hero .rotulo-rota .n')].map((n) => n.textContent),
  legenda: document.getElementById('legenda-rotas-hero').textContent, textoOculto: document.querySelector('.hero-texto').hidden,
  foco: document.activeElement.id })"""


def _ponto(pg, cod):
    pg.wait_for_function(f"!!window.radarMapaHero?.pontoDoContinente('{cod}')", timeout=15_000)
    return pg.evaluate(f"window.radarMapaHero.pontoDoContinente('{cod}')")


def test_continente_acende_no_cursor_e_o_clique_abre_os_dados_dele(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    eu, esperado = _ponto(pg, "EU"), pg.evaluate(f"({ESPERADO})('EU')")
    pg.mouse.move(eu["x"] - 40, eu["y"] + 20)
    pg.mouse.move(eu["x"], eu["y"], steps=6)
    pg.wait_for_function("!document.getElementById('dica-continente').hidden", timeout=5_000)
    assert pg.evaluate("document.getElementById('dica-continente').textContent").startswith(f"{esperado['nome']} · {esperado['t']} em {esperado['ano']}")
    assert pg.evaluate("document.getElementById('inicio').classList.contains('sobre-continente')")
    pg.mouse.click(eu["x"], eu["y"])
    pg.wait_for_function(f"document.getElementById('pc-titulo').textContent === {esperado['nome']!r} && document.getElementById('pc-t').textContent === {esperado['t']!r}"
                         f" && document.getElementById('pc-share').textContent === {esperado['share']!r}", timeout=10_000)  # a contagem termina
    r = pg.evaluate(PAINEL)
    assert r["aberto"] == "EU" and r["visivel"] and r["textoOculto"]
    assert r["share"] == esperado["share"] and r["paises"] == esperado["paises"]
    assert len(r["grupos"]) == 4 and abs(sum(r["grupos"]) - 100) < 0.5
    assert set(r["rotas"]) <= set(esperado["paises"]) and "da Europa" in r["legenda"], "as rotas viram as do continente"
    # abas trocam o continente; clicar no continente aberto fecha
    pg.click("#pc-abas button[data-cont=AS]")
    asia = pg.evaluate(f"({ESPERADO})('AS')")
    pg.wait_for_function(f"document.getElementById('pc-titulo').textContent === {asia['nome']!r} && document.getElementById('pc-t').textContent === {asia['t']!r}", timeout=10_000)
    assert pg.evaluate("document.querySelector('#pc-abas [aria-pressed=true]').dataset.cont") == "AS"
    # o Brasil (por estado) vem antes dos continentes
    assert pg.evaluate("[...document.querySelectorAll('#pc-abas button')].map((b) => b.dataset.cont)") == ["BR", *asia["ordem"]]
    # trocas seguidas: a contagem nova cancela a anterior (o número final não volta atrás)
    pg.click("#pc-abas button[data-cont=AF]")
    pg.click("#pc-abas button[data-cont=EU]")
    pg.wait_for_timeout(1600)
    assert pg.evaluate(PAINEL)["t"] == esperado["t"] and pg.evaluate(PAINEL)["share"] == esperado["share"]
    pg.click("#pc-abas button[data-cont=AS]")
    pg.wait_for_function(f"document.getElementById('pc-t').textContent === {asia['t']!r}", timeout=10_000)
    pg.mouse.click(**{k: v for k, v in _ponto(pg, "AS").items()})
    pg.wait_for_function("document.getElementById('painel-continente').hidden && !document.querySelector('.hero-texto').hidden", timeout=5_000)
    assert pg.evaluate("window.radarMapaHero.continenteAberto()") is None
    assert erros == []
    ctx.close()


def test_continente_pelo_teclado_esc_fecha_e_devolve_o_foco(navegador, servidor):
    for reduzido in (False, True):
        ctx, pg, erros = _pagina(navegador, servidor, reduzido=reduzido)
        pg.wait_for_function("!document.getElementById('explorar-continentes').hidden", timeout=15_000)
        maior = pg.evaluate(f"({ESPERADO})('AS').ordem[0]")
        esperado = pg.evaluate(f"({ESPERADO})({maior!r})")
        pg.focus("#explorar-continentes")
        pg.keyboard.press("Enter")
        if reduzido:  # sem movimento: tudo na hora, sem contagem
            assert pg.evaluate(PAINEL)["t"] == esperado["t"]
        pg.wait_for_function("document.activeElement.id === 'pc-titulo'", timeout=5_000)
        r = pg.evaluate(PAINEL)
        assert r["aberto"] == maior and r["visivel"]
        pg.wait_for_function(f"document.getElementById('pc-t').textContent === {esperado['t']!r}", timeout=5_000)
        assert pg.evaluate("getComputedStyle(document.querySelector('#pc-var .seta')).display") != "none"
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.getElementById('painel-continente').hidden", timeout=5_000)
        assert pg.evaluate("document.activeElement.id") == "explorar-continentes"
        assert not pg.evaluate("document.querySelector('.hero-texto').hidden")
        assert erros == []
        ctx.close()


def test_continente_no_celular_abre_abaixo_do_mapa(navegador, servidor):
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pg.goto(servidor)
    pg.wait_for_function("window.anime && window.radarDados", timeout=20_000)
    eu = _ponto(pg, "EU")
    pg.tap("body", position={"x": int(eu["x"]), "y": int(eu["y"])})
    pg.wait_for_function("window.radarMapaHero.continenteAberto() === 'EU'", timeout=5_000)
    pg.wait_for_timeout(1200)
    caixas = pg.evaluate("""() => ({ painel: document.getElementById('painel-continente').getBoundingClientRect().top,
      chip: document.getElementById('chip-brasil').getBoundingClientRect().bottom, rolagem: document.documentElement.scrollWidth - innerWidth })""")
    assert caixas["painel"] >= caixas["chip"], "o painel não cobre o mapa"
    assert caixas["rolagem"] <= 0, "sem rolagem lateral"
    assert erros == []
    ctx.close()


# --- Brasil por estado (clique no Brasil do hero) -----------------------------------------------
# Esperado pelos dados: total, estados com alta, maiores altas e quedas em toneladas e um estado
# de cada cor (variação acima de 20% para cima ou para baixo, sem ser de pouco volume).
ESTADOS = """([ano, grupo]) => { const d = window.radarDados, e = d.estados, a = d.anual;
  if (ano === null) { let i = a.anos.length - 1; while (a.n_meses[i] !== 12) i--; ano = a.anos[i]; }
  const linhas = e.dados[String(ano)][grupo].map(([uf, t, s, v, ta]) => ({ uf, nome: e.ufs[uf], t, v, ta, d: t - ta }));
  const total = linhas.reduce((s, x) => s + x.t, 0), totalAnt = linhas.reduce((s, x) => s + x.ta, 0);
  const corte = 0.005 * Math.max(total, totalAnt);
  const grandes = linhas.filter((x) => Math.max(x.t, x.ta) >= corte && x.v !== null);
  const ativos = linhas.filter((x) => x.t > 0 || x.ta > 0 || x.v !== null);
  const compacto = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 });
  return { ano, t: compacto.format(total) + ' t', altas: `${ativos.filter((x) => x.d > 0.5).length} de ${ativos.length}`,
    sobe: linhas.filter((x) => x.d > 0.5).sort((p, q) => q.d - p.d).slice(0, 3).map((x) => x.nome),
    desce: linhas.filter((x) => x.d < -0.5).sort((p, q) => p.d - q.d).slice(0, 3).map((x) => x.nome),
    alta: grandes.find((x) => x.v > 20)?.uf ?? null, queda: grandes.find((x) => x.v < -20)?.uf ?? null }; }"""
BRASIL = """() => ({ aberto: window.radarMapaHero.continenteAberto(), titulo: document.getElementById('pc-titulo').textContent,
  kicker: document.getElementById('pc-kicker').textContent, t: document.getElementById('pb-t').textContent,
  altas: document.getElementById('pb-altas').textContent, textoOculto: document.querySelector('.hero-texto').hidden,
  sobe: [...document.querySelectorAll('#pb-sobe .nome')].map((n) => n.textContent),
  desce: [...document.querySelectorAll('#pb-desce .nome')].map((n) => n.textContent),
  quadros: document.querySelectorAll('#pb-mapa .pb-uf').length, paradas: document.querySelectorAll('#pb-mapa .pb-uf[tabindex="0"]').length,
  abas: [...document.querySelectorAll('#pc-abas button')].map((b) => b.dataset.cont), continente: !document.getElementById('pc-continente').hidden,
  legenda: document.getElementById('legenda-rotas-hero').textContent, detalhe: document.getElementById('pb-detalhe').textContent,
  foco: document.activeElement.id || document.activeElement.dataset?.uf || '' })"""


def _rgb(cor):
    return [int(x) for x in re.findall(r"\d+", cor)[:3]]


def test_brasil_abre_por_estado_com_altas_quedas_filtros_e_setas(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    br = _ponto(pg, "BR")
    pg.mouse.move(br["x"] - 40, br["y"] - 20)
    pg.mouse.move(br["x"], br["y"], steps=6)
    pg.wait_for_function("!document.getElementById('dica-continente').hidden", timeout=5_000)
    assert pg.evaluate("document.getElementById('dica-continente').textContent").startswith("Brasil · ")
    assert "por estado" in pg.evaluate("document.getElementById('dica-continente').textContent")
    pg.mouse.click(br["x"], br["y"])
    esperado = pg.evaluate(f"({ESTADOS})([null, 'TOTAL'])")
    pg.wait_for_function(f"document.getElementById('pb-t').textContent === {esperado['t']!r}", timeout=10_000)  # a contagem termina
    r = pg.evaluate(BRASIL)
    assert r["aberto"] == "BR" and r["titulo"] == "Brasil" and r["textoOculto"] and not r["continente"]
    assert r["altas"] == esperado["altas"] and r["sobe"] == esperado["sobe"] and r["desce"] == esperado["desce"]
    assert r["quadros"] == 27 and r["paradas"] == 1, "um quadrado por UF e uma só parada de Tab"
    assert r["abas"][0] == "BR" and str(esperado["ano"]) in r["kicker"]
    # cor: alta quente, queda fria
    for uf, quente in ((esperado["alta"], True), (esperado["queda"], False)):
        if uf:
            vermelho, _, azul = _rgb(pg.evaluate(f"getComputedStyle(document.querySelector('.pb-uf[data-uf={uf}]')).backgroundColor"))
            assert (vermelho > azul) == quente, uf
    # filtros: grupo K e o último ano (o parcial, se houver); as listas e as rotas acompanham
    pg.click("#pb-grupos button[data-grupo=K]")
    ultimo = pg.evaluate("Math.max(...[...document.querySelectorAll('#pb-anos button')].map((b) => Number(b.dataset.ano)))")
    pg.click(f"#pb-anos button[data-ano='{ultimo}']", force=True)  # a fileira some quando só há um ano
    k = pg.evaluate(f"({ESTADOS})([{ultimo}, 'K'])")
    pg.wait_for_function(f"document.getElementById('pb-t').textContent === {k['t']!r}", timeout=10_000)
    r = pg.evaluate(BRASIL)
    assert r["sobe"] == k["sobe"] and r["desce"] == k["desce"] and r["altas"] == k["altas"]
    assert "potássicos" in r["legenda"], "as rotas viram as do grupo escolhido"
    # setas andam entre vizinhos do mapa; Enter fixa o estado; Esc fecha e devolve o foco
    pg.focus(".pb-uf[data-uf=PR]")
    for tecla, uf in (("ArrowRight", "SP"), ("ArrowUp", "DF"), ("ArrowLeft", "MS")):
        pg.keyboard.press(tecla)
        assert pg.evaluate("document.activeElement.dataset.uf") == uf
    assert pg.evaluate(BRASIL)["detalhe"].startswith("Mato Grosso do Sul")
    assert pg.evaluate("document.querySelectorAll('#pb-mapa .pb-uf[tabindex=\"0\"]').length") == 1
    pg.keyboard.press("Enter")
    assert pg.evaluate("document.querySelector('.pb-uf[data-uf=MS]').getAttribute('aria-pressed')") == "true"
    pg.keyboard.press("Escape")
    pg.wait_for_function("document.getElementById('painel-continente').hidden", timeout=5_000)
    assert pg.evaluate("document.activeElement.id") == "explorar-continentes"
    assert erros == []
    ctx.close()


def test_brasil_pelo_chip_com_teclado_e_sem_movimento(navegador, servidor):
    for reduzido in (False, True):
        ctx, pg, erros = _pagina(navegador, servidor, reduzido=reduzido)
        pg.wait_for_function("(() => { const c = document.getElementById('chip-brasil'); return !c.hidden && !c.disabled; })()", timeout=15_000)
        esperado = pg.evaluate(f"({ESTADOS})([null, 'TOTAL'])")
        pg.focus("#chip-brasil")
        pg.keyboard.press("Enter")
        if reduzido:  # sem movimento: tudo na hora, sem contagem nem entrada dos quadrados
            assert pg.evaluate("document.getElementById('pb-t').textContent") == esperado["t"]
            assert pg.evaluate("document.querySelector('.pb-uf').getAttribute('style').includes('opacity')") is False
        pg.wait_for_function("document.activeElement.id === 'pc-titulo'", timeout=5_000)
        assert pg.evaluate("document.getElementById('chip-brasil').getAttribute('aria-expanded')") == "true"
        pg.wait_for_function(f"document.getElementById('pb-t').textContent === {esperado['t']!r}", timeout=5_000)
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.getElementById('painel-continente').hidden", timeout=5_000)
        assert pg.evaluate("document.activeElement.id") == "chip-brasil", "o foco volta para o chip que abriu o painel"
        assert pg.evaluate("document.getElementById('chip-brasil').getAttribute('aria-expanded')") == "false"
        assert erros == []
        ctx.close()


def test_brasil_no_celular_abre_abaixo_do_mapa(navegador, servidor):
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pg.goto(servidor)
    pg.wait_for_function("window.anime && window.radarDados", timeout=20_000)
    br = _ponto(pg, "BR")
    pg.tap("body", position={"x": int(br["x"]), "y": int(br["y"])})
    pg.wait_for_function("window.radarMapaHero.continenteAberto() === 'BR'", timeout=5_000)
    pg.wait_for_timeout(1500)
    caixas = pg.evaluate("""() => ({ painel: document.getElementById('painel-continente').getBoundingClientRect().top,
      chip: document.getElementById('chip-brasil').getBoundingClientRect().bottom, rolagem: document.documentElement.scrollWidth - innerWidth,
      quadro: document.querySelector('.pb-uf[data-uf=PR]').getBoundingClientRect().width,
      nomes: [...document.querySelectorAll('#pb-sobe .nome')].map((n) => n.getBoundingClientRect().width) })""")
    assert caixas["painel"] >= caixas["chip"], "o painel não cobre o mapa"
    assert caixas["rolagem"] <= 0, "sem rolagem lateral"
    assert caixas["quadro"] >= 40, "quadrados grandes o bastante para o toque"
    assert all(w > 60 for w in caixas["nomes"]), "o nome do estado aparece na lista"
    assert erros == []
    ctx.close()


# --- Custo por nutriente, legenda de FOB e CIF e glossário ---------------------------------------
NUTRIENTES = """(chaves) => { const d = window.radarDados, nu = d.nutrientes, p = d.mensal.periodos, k = nu.periodos.indexOf(p[p.length - 1]);
  return chaves.map((c) => { const x = nu.produtos[c]; return { nome: x.nome, custo: x.custo[k], brl: x.custo_brl[k], t: x.t[k], pequena: x.amostra_pequena[k] }; }); }"""


def _milhar(v):
    return f"{math.floor(v + 0.5):,}".replace(",", ".")


def test_custo_por_nutriente_um_produto_por_nutriente_moeda_e_tracos(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    pg.evaluate("document.getElementById('secao-nutrientes').scrollIntoView()")
    pg.wait_for_function("document.querySelectorAll('#kpis-nutrientes .kpi').length === 3", timeout=10_000)
    kpis = "[...document.querySelectorAll('#kpis-nutrientes .kpi')].map((k) => [k.querySelector('.rotulo').textContent, k.querySelector('.valor').textContent])"
    esperado = pg.evaluate(f"({NUTRIENTES})(['ureia', 'map', 'kcl'])")
    valores = [f"US$ {_milhar(e['custo'])}/t de {simbolo}" if e["t"] and not e["pequena"] else "–" for e, simbolo in zip(esperado, ("N", "P₂O₅", "K₂O"))]
    # os números contam a partir de zero ao aparecer: espera o valor final
    # (o literal sai fora da f-string: aspas iguais dentro dela só valem do Python 3.12 em diante)
    alvo = json.dumps(json.dumps(valores, ensure_ascii=False, separators=(",", ":")), ensure_ascii=False)
    pg.wait_for_function(f"JSON.stringify([...document.querySelectorAll('#kpis-nutrientes .valor')].map((v) => v.textContent)) === {alvo}", timeout=5_000)
    for (rotulo, _), e in zip(pg.evaluate(kpis), esperado):
        assert rotulo.startswith(e["nome"])
    pg.click("#unidade-nutrientes button:nth-child(2)")
    for (_, valor), e in zip(pg.evaluate(kpis), esperado):
        assert valor == "–" or valor.startswith(f"R$ {_milhar(e['brl'])}/t de ")
    # filtro N: os três produtos de N, na mesma cor e com traços diferentes
    pg.click("#filtro-grupo .chip[data-grupo=N]")
    pg.wait_for_function("document.querySelectorAll('#kpis-nutrientes .kpi').length === 3", timeout=5_000)
    assert pg.evaluate("[...document.querySelectorAll('#legenda-nutrientes li span')].map((s) => s.className)") == ["chave-linha", "chave-tracejada", "chave-pontilhada"]
    assert "Sulfato de amônio" in pg.evaluate("document.getElementById('nota-nutrientes').textContent")
    meses = pg.evaluate("window.radarDados.mensal.periodos.length")
    assert pg.evaluate("document.querySelectorAll('#tabela-nutrientes tbody tr').length") == meses
    assert "não é o preço ao produtor" in pg.evaluate("document.querySelector('#secao-nutrientes .ressalva').textContent").lower()
    assert erros == []
    ctx.close()


def test_legenda_de_fob_e_cif_e_glossario(navegador, servidor):
    ctx, pg, erros = _pagina(navegador, servidor)
    assert pg.evaluate("[...document.querySelectorAll('.kpis .termos dt')].map((d) => d.textContent)")[:2] == ["FOB", "CIF"]
    assert pg.evaluate("[...document.querySelectorAll('#secao-bm .termos dt')].map((d) => d.textContent)") == ["FOB×FOB", "CIF×CFR"]
    hash_antes = pg.evaluate("location.hash")
    pg.click(".kpis a[data-secao=glossario]")
    pg.wait_for_function("document.activeElement.id === 't-glossario'", timeout=5_000)
    assert pg.evaluate("location.hash") == hash_antes, "o link não troca o hash (que guarda os filtros)"
    termos = pg.evaluate("[...document.querySelectorAll('#glossario dt')].map((d) => d.textContent)")
    for termo in ("FOB (free on board)", "CIF (cost, insurance and freight)", "Custo por nutriente", "UF de destino", "Spread"):
        assert termo in termos
    assert pg.evaluate("document.getElementById('fonte-rodape').textContent") == "Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX"
    assert erros == []
    ctx.close()


# --- Comparar portos (mapa de fluxos) e números que contam -------------------------------------
PORTOS_ESPERADOS = """() => { const m = window.radarDados.mapa; let i = m.anos.length - 1;
  while (m.n_meses[i] !== 12) i--;
  const r = m.dados[String(m.anos[i])].TOTAL, portos = r.portos.filter((p) => p[1] > 0).slice(0, 8);
  return { nomes: portos.map((p) => m.portos[p[0]][0]), fretes: portos.map((p) => p[3]) }; }"""
COMPARACAO = """() => ({ titulo: document.getElementById('fluxos-painel-titulo').textContent, largura: document.getElementById('fluxos-painel').offsetWidth,
  nomes: [...document.querySelectorAll('#fluxos-cmp-lista .cmp-linha .nome')].map((n) => n.firstChild.textContent),
  fretes: [...document.querySelectorAll('#fluxos-cmp-lista .cmp-linha .frete')].map((f) => f.textContent),
  mix: [...document.querySelectorAll('#fluxos-cmp-lista .cmp-linha')].map((b) => [...b.querySelectorAll('.preenchida i')].reduce((s, i) => s + Number(i.style.flexGrow || 0), 0)),
  rotulos: [...document.querySelectorAll('#fluxos-cmp-lista .cmp-linha')].map((b) => b.getAttribute('aria-label')),
  visivel: !document.getElementById('fluxos-comparar').hidden, lista: !document.getElementById('fluxos-lista').hidden })"""


def test_comparar_portos_lado_a_lado_com_ordenacao_e_volta(navegador, servidor):
    ctx, pg, erros = _abrir(navegador, servidor, 1440, "light")
    estreito = pg.evaluate("document.getElementById('fluxos-painel').offsetWidth")
    pg.click("#fluxos-abas button[data-visao=comparar]")
    pg.wait_for_function("document.querySelectorAll('#fluxos-cmp-lista .cmp-linha').length >= 5", timeout=10_000)
    pg.wait_for_timeout(900)  # o painel termina de se alargar
    esperado, r = pg.evaluate(PORTOS_ESPERADOS), pg.evaluate(COMPARACAO)
    assert r["titulo"] == "Comparar portos" and r["visivel"] and not r["lista"]
    assert r["largura"] > estreito + 150, "o painel se alarga para a comparação"
    assert r["nomes"] == esperado["nomes"], "os maiores portos, por toneladas"
    assert all(abs(x - 1) < 0.02 for x in r["mix"]), "em Todos, a barra se divide entre os grupos"
    assert all("frete" in x and "Enter mostra as origens" in x for x in r["rotulos"])
    assert "visao=comparar" in pg.evaluate("location.hash")
    # ordenar pelo frete: do menor para o maior; de novo: do maior para o menor
    pg.click("#fluxos-cmp-cabecalho button[data-ordem=frete]")
    fretes = [int(x.replace("US$ ", "")) for x in pg.evaluate(COMPARACAO)["fretes"] if x != "–"]
    assert fretes == sorted(fretes)
    assert pg.evaluate("document.querySelector('#fluxos-cmp-cabecalho [data-ordem=frete]').getAttribute('aria-pressed')") == "true"
    pg.click("#fluxos-cmp-cabecalho button[data-ordem=frete]")
    fretes = [int(x.replace("US$ ", "")) for x in pg.evaluate(COMPARACAO)["fretes"] if x != "–"]
    assert fretes == sorted(fretes, reverse=True)
    # um porto abre as origens dele (o painel volta ao tamanho normal); Esc volta à comparação
    primeiro = pg.evaluate(COMPARACAO)["nomes"][0]
    pg.click("#fluxos-cmp-lista .cmp-linha >> nth=0")
    pg.wait_for_function(f"document.getElementById('fluxos-painel-titulo').textContent.startsWith({primeiro!r})", timeout=5_000)
    pg.wait_for_timeout(900)
    assert pg.evaluate("document.getElementById('fluxos-painel').offsetWidth") < estreito + 20
    pg.keyboard.press("Escape")
    pg.wait_for_function("document.getElementById('fluxos-painel-titulo').textContent === 'Comparar portos'", timeout=5_000)
    assert pg.evaluate("document.activeElement.classList.contains('cmp-linha')"), "o foco volta para a linha do porto"
    assert erros == []
    ctx.close()
    # a URL guarda a visão: ao abrir de novo, a comparação já aparece (e, no celular, cabe na tela)
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pg.goto(servidor + "#visao=comparar")
    pg.wait_for_function("window.anime && window.radarDados", timeout=20_000)
    pg.evaluate("document.getElementById('fluxos').scrollIntoView({block: 'start'})")
    pg.wait_for_function("document.querySelectorAll('#fluxos-cmp-lista .cmp-linha').length >= 5", timeout=15_000)
    caixas = pg.evaluate("""() => ({ rolagem: document.documentElement.scrollWidth - innerWidth,
      nome: document.querySelector('#fluxos-cmp-lista .cmp-linha .nome').getBoundingClientRect().width })""")
    assert caixas["rolagem"] <= 0 and caixas["nome"] > 120, "no celular, o nome do porto tem uma linha só para ele"
    assert erros == []
    ctx.close()


AMOSTRAS = """async (seletor) => { const vistos = []; const fim = performance.now() + 1300;
  while (performance.now() < fim) { const t = document.querySelector(seletor).textContent; if (vistos[vistos.length - 1] !== t) vistos.push(t);
    await new Promise((r) => requestAnimationFrame(r)); }
  return vistos; }"""


def test_numeros_do_resumo_contam_ao_trocar_o_grupo(navegador, servidor):
    for reduzido in (False, True):
        ctx, pg, erros = _pagina(navegador, servidor, reduzido=reduzido)
        pg.evaluate("document.getElementById('kpis-mes').scrollIntoView({block: 'center'})")
        pg.wait_for_timeout(1600)
        esperado = pg.evaluate("""() => { const d = window.radarDados, i = d.mensal.periodos.length - 1;
          return new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 }).format(d.mensal.K.t[i]) + ' t'; }""")
        pg.evaluate("document.querySelector('#filtro-grupo .chip[data-grupo=K]').click()")
        vistos = pg.evaluate(AMOSTRAS, "#kpis-mes .kpi .valor")
        assert vistos[-1] == esperado
        if reduzido:
            assert len(vistos) == 1, "sem movimento, o número troca de uma vez"
        else:
            assert len(vistos) >= 3, "o número conta até o novo valor"
        assert erros == []
        ctx.close()



# --- Lista de espera -------------------------------------------------------------------------
# O Turnstile e a Edge Function são simulados: nenhuma chamada sai do navegador.
TURNSTILE_FALSO = """(() => {
  const widgets = [];
  window.__turnstile = { renders: [], resets: 0, removes: 0 };
  const emitir = (w) => setTimeout(() => { w.n += 1; w.opcoes.callback(`tok-${w.n}`); }, 30);
  window.turnstile = {
    render(el, opcoes) {
      window.__turnstile.renders.push({ sitekey: opcoes.sitekey, action: opcoes.action, theme: opcoes.theme,
        size: opcoes.size, language: opcoes.language, campo: opcoes["response-field"] });
      const w = { id: `w${widgets.length}`, opcoes, n: 0 };
      widgets.push(w);
      el.textContent = "verificação simulada";
      emitir(w);
      return w.id;
    },
    reset(id) { window.__turnstile.resets += 1; emitir(widgets.find((w) => w.id === id)); },
    remove() { window.__turnstile.removes += 1; },
  };
  setTimeout(() => window.radarTurnstilePronto && window.radarTurnstilePronto(), 0);
})();"""
FUNCAO_LISTA = "https://recqdysmwfltkrhrhdjx.supabase.co/functions/v1/lista-espera"
CORS_FALSO = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type", "Access-Control-Allow-Methods": "POST, OPTIONS"}


def _lista(navegador, servidor, respostas=(), largura=1440, tema="light", busca=""):
    """Página com o Turnstile e a função falsos. `respostas`: (status, corpo), "rede" ou "segurar"."""
    ctx = navegador.new_context(viewport={"width": largura, "height": 900}, color_scheme=tema, reduced_motion="reduce")
    pg = ctx.new_page()
    erros, envios, cargas, segurados = [], [], [], []
    fila = list(respostas)
    pg.on("pageerror", lambda e: erros.append(str(e)))
    # respostas 4xx simuladas e a falha de rede aparecem como "Failed to load resource": não são erros do script
    pg.on("console", lambda m: erros.append(m.text) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    pg.route("https://cdn.jsdelivr.net/**", _cdn)

    def turnstile(rota):
        cargas.append(rota.request.url)
        rota.fulfill(status=200, content_type="application/javascript", body=TURNSTILE_FALSO)

    def funcao(rota):
        if rota.request.method == "OPTIONS":
            return rota.fulfill(status=204, headers=CORS_FALSO)
        envios.append(json.loads(rota.request.post_data))
        resposta = fila.pop(0) if fila else (200, {"ok": True})
        if resposta == "rede":
            return rota.abort()
        if resposta == "segurar":
            return segurados.append(rota)
        rota.fulfill(status=resposta[0], headers={**CORS_FALSO, "Content-Type": "application/json"}, body=json.dumps(resposta[1]))

    pg.route("https://challenges.cloudflare.com/**", turnstile)
    pg.route(FUNCAO_LISTA, funcao)
    pg.goto(servidor + busca)
    pg.wait_for_function("window.radarDados", timeout=20_000)
    return ctx, pg, {"erros": erros, "envios": envios, "cargas": cargas, "segurados": segurados}


def _ir_para_a_lista(pg):
    """Rola até a seção e põe o foco no primeiro campo: é aí que o Turnstile carrega."""
    pg.evaluate("window.scrollTo(0, document.getElementById('lista-espera').getBoundingClientRect().top + scrollY - 80)")
    pg.focus("#le-nome")
    pg.wait_for_function("window.__turnstile && window.__turnstile.renders.length === 1", timeout=10_000)


def _preencher(pg, email="maria@cooperativa.com.br"):
    pg.fill("#le-nome", "  Maria   da Silva ")
    pg.fill("#le-email", email)
    pg.fill("#le-empresa", "Cooperativa Agro")
    pg.select_option("#le-segmento", "cooperativa")
    pg.check("#le-form input[value=mcp]")
    pg.check("#le-form input[value=alertas]")
    pg.check("#le-consentimento")


def test_lista_de_espera_envia_com_a_origem_da_visita_e_mostra_o_sucesso(navegador, servidor):
    ctx, pg, r = _lista(navegador, servidor, respostas=["segurar"], busca="?utm_source=linkedin&utm_campaign=post-alertas")
    # o script assumiu o formulário: o botão, desligado no HTML, liga
    assert pg.evaluate("document.getElementById('le-enviar').disabled") is False
    # só ver a seção não chama a Cloudflare; o Turnstile carrega quando alguém usa o formulário
    pg.evaluate("window.scrollTo(0, document.getElementById('lista-espera').getBoundingClientRect().top + scrollY - 80)")
    pg.wait_for_timeout(600)
    assert r["cargas"] == [], "o Turnstile só carrega quando alguém usa o formulário"
    _ir_para_a_lista(pg)
    assert r["cargas"][0].startswith("https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit")
    assert pg.evaluate("window.__turnstile.renders[0]") == {
        "sitekey": "0x4AAAAAAFYdiHT5aqpj-sA2", "action": "lista-espera", "theme": "light",
        "size": "flexible", "language": "pt-br", "campo": False,
    }
    assert pg.evaluate("document.getElementById('titulo-lista-espera').textContent") == "Receba alertas do seu mercado"
    _preencher(pg)
    pg.click("#le-enviar")
    # enviando: botão ocupado (mas com foco), status anunciado, nada de segundo envio
    pg.wait_for_function("document.getElementById('le-form').getAttribute('aria-busy') === 'true'")
    assert pg.evaluate("document.getElementById('le-enviar').getAttribute('aria-disabled')") == "true"
    assert pg.evaluate("document.getElementById('le-status').textContent") == "Enviando a sua inscrição…"
    pg.evaluate("document.getElementById('le-enviar').click()")
    assert len(r["envios"]) == 1
    r["segurados"].pop().fulfill(status=200, headers={**CORS_FALSO, "Content-Type": "application/json"}, body='{"ok":true}')
    pg.wait_for_selector("#le-sucesso:not([hidden])")
    assert r["envios"] == [{
        "nome": "Maria da Silva", "email": "maria@cooperativa.com.br", "empresa": "Cooperativa Agro", "cargo": None,
        "segmento": "cooperativa", "interesses": ["alertas", "mcp"], "consentimento_lgpd": True,
        "origem": "utm_source=linkedin&utm_campaign=post-alertas", "turnstile": "tok-1", "website": "",
    }]
    assert pg.evaluate("document.getElementById('le-form').hidden") is True
    assert pg.evaluate("document.activeElement.id") == "le-sucesso"
    assert "Pronto! Você está na lista." in pg.evaluate("document.getElementById('le-sucesso').textContent")
    assert r["erros"] == []
    ctx.close()


def test_lista_de_espera_valida_antes_de_enviar_e_mostra_os_erros_da_funcao(navegador, servidor):
    respostas = [
        (422, {"ok": False, "erro": "campos", "campos": {"email": "Confira o e-mail: ele precisa ter o formato nome@empresa.com.br."}}),
        (429, {"ok": False, "erro": "limite"}),
        "rede",
        (200, {"ok": True}),
    ]
    ctx, pg, r = _lista(navegador, servidor, respostas=respostas)
    _ir_para_a_lista(pg)
    pg.click("#le-enviar")
    erro = lambda campo: pg.evaluate(f"(() => {{ const e = document.getElementById('{campo}-erro'); return e.hidden ? null : e.textContent; }})()")
    assert erro("le-nome") == "Informe o seu nome."
    assert erro("le-email") == "Informe o seu e-mail."
    assert erro("le-segmento") == "Escolha o seu segmento."
    assert erro("le-consentimento") == "Para entrar na lista, aceite o uso dos dados para este aviso."
    assert erro("le-empresa") is None and erro("le-cargo") is None
    assert pg.evaluate("document.getElementById('le-nome').getAttribute('aria-invalid')") == "true"
    assert pg.evaluate("document.activeElement.id") == "le-nome"
    assert r["envios"] == []
    # o erro some quando a pessoa corrige o campo; regras iguais às da função
    pg.fill("#le-nome", "M")
    assert erro("le-nome") is None
    _preencher(pg, email="maria@empresa")
    pg.fill("#le-nome", "<b>Maria</b>")
    pg.click("#le-enviar")
    assert erro("le-nome") == "Não use links nem os sinais < e >."
    assert erro("le-email") == "Confira o e-mail: ele precisa ter o formato nome@empresa.com.br."
    for nome, mensagem in [
        ("\uff1cscript\uff1e", "Não use links nem os sinais < e >."),  # < > largos
        ('=HYPERLINK("golpe.exemplo")', "Não comece com =, +, - ou @."),
        ("12", "Escreva com letras."),
        ("Ma\u200bria", "Há caracteres invisíveis ou de controle no texto."),
    ]:
        pg.fill("#le-nome", nome)
        pg.click("#le-enviar")
        assert erro("le-nome") == mensagem, nome
    assert r["envios"] == []
    _preencher(pg)
    # 422: o erro do campo vem da função e o token não é trocado (ela recusa antes do Turnstile)
    pg.click("#le-enviar")
    pg.wait_for_function("!document.getElementById('le-email-erro').hidden")
    assert pg.evaluate("window.__turnstile.resets") == 0
    # 429: aviso geral (role=alert) e um token novo para a próxima tentativa
    pg.click("#le-enviar")
    pg.wait_for_function("!document.getElementById('le-erro').hidden")
    assert pg.evaluate("document.getElementById('le-erro').textContent") == (
        "Já saíram muitas inscrições desta rede na última hora. Tente de novo mais tarde ou escreva para contato@graaos.cloud.")
    assert pg.evaluate("document.getElementById('le-erro').getAttribute('role')") == "alert"
    assert pg.evaluate("window.__turnstile.resets") == 1
    assert pg.evaluate("document.getElementById('le-enviar').getAttribute('aria-disabled')") == "false"
    # sem conexão: outra mensagem, outro token
    pg.wait_for_timeout(100)
    pg.click("#le-enviar")
    pg.wait_for_function("document.getElementById('le-erro').textContent.startsWith('Não foi possível falar')")
    assert pg.evaluate("window.__turnstile.resets") == 2
    pg.wait_for_timeout(100)
    pg.click("#le-enviar")
    pg.wait_for_selector("#le-sucesso:not([hidden])")
    assert [e["turnstile"] for e in r["envios"]] == ["tok-1", "tok-1", "tok-2", "tok-3"]
    assert r["erros"] == []
    ctx.close()


def test_lista_de_espera_no_celular_no_teclado_e_no_tema_escuro(navegador, servidor):
    ctx, pg, r = _lista(navegador, servidor, largura=390, tema="dark")
    _ir_para_a_lista(pg)
    assert pg.evaluate("window.__turnstile.renders[0].theme") == "dark"
    # nada da seção passa da largura da tela
    fora = pg.evaluate("""() => { const W = document.documentElement.clientWidth;
      return [...document.querySelectorAll('#lista-espera *')].filter((e) => { const b = e.getBoundingClientRect();
        return b.width > 0 && b.right > W + 0.5 && !e.closest('.le-isca'); }).map((e) => e.id || e.className); }""")
    assert fora == []
    # todo controle tem rótulo; a isca fica fora do teclado e dos leitores de tela
    sem_rotulo = pg.evaluate("""() => [...document.querySelectorAll('#le-form input, #le-form select')]
      .filter((c) => !c.closest('.le-isca') && c.labels.length === 0).map((c) => c.id || c.name)""")
    assert sem_rotulo == []
    assert pg.evaluate("document.querySelector('.le-isca').inert") is True
    assert pg.evaluate("document.querySelector('.le-isca').getAttribute('aria-hidden')") == "true"
    pg.focus("#le-nome")
    ordem = []
    for _ in range(14):
        ordem.append(pg.evaluate("document.activeElement.id || document.activeElement.value"))
        pg.keyboard.press("Tab")
    assert "le-website" not in ordem
    assert ordem[:6] == ["le-nome", "le-email", "le-empresa", "le-cargo", "le-segmento", "alertas"]
    assert "le-consentimento" in ordem and "le-enviar" in ordem
    # Enter no campo envia o formulário (com os erros dos campos que faltam)
    pg.focus("#le-nome")
    pg.keyboard.press("Enter")
    assert pg.evaluate("document.getElementById('le-nome-erro').hidden") is False
    assert r["envios"] == [] and r["erros"] == []
    ctx.close()


def test_lista_de_espera_sem_javascript_nao_envia_nada(navegador, servidor):
    ctx = navegador.new_context(viewport={"width": 1280, "height": 900}, java_script_enabled=False)
    pg = ctx.new_page()
    pg.route("https://cdn.jsdelivr.net/**", _cdn)
    pedidos = []
    pg.on("request", lambda req: pedidos.append(req.url) if "challenges.cloudflare.com" in req.url or "supabase.co" in req.url else None)
    pg.goto(servidor)
    # o botão fica desligado (nem o Enter envia), o aviso aparece e nada vai para a Cloudflare nem para a função
    assert pg.locator("#le-enviar").is_disabled()
    # (o aviso fica num <noscript>; o Chromium do teste só desliga a execução, então não o desenha)
    assert "Para entrar na lista, ative o JavaScript" in pg.content()
    pg.fill("#le-email", "maria@cooperativa.com.br")
    pg.press("#le-email", "Enter")
    pg.wait_for_timeout(300)
    assert "maria" not in pg.url and pedidos == []
    assert pg.get_attribute("#le-form", "method") == "post"
    ctx.close()
