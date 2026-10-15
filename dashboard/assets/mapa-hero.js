/* Mapa do hero: mapa-múndi em pontos (canvas), rotas vetoriais (SVG) e dados por continente.
 *
 * - Pontos de assets/mapa-pontos.json (Natural Earth 1:110m, gerado por `python -m src.mapa
 *   pontos`), com o continente de cada país (campo CONTINENT do Natural Earth). Nenhum cálculo
 *   de geografia aqui: só desenho.
 * - Números sempre do dashboard.json (evento "radar:dados"): kicker, chip do Brasil, rotas das
 *   maiores origens do último ano completo e o painel de cada continente (bloco "mapa").
 * - Luz do cursor: os pontos perto do ponteiro acendem, com uma lente leve, e o continente sob
 *   ele se destaca, com uma dica. Um clique (ou toque) abre o painel do continente no lugar do
 *   título: um "ping" de radar percorre os pontos dele, uma varredura passa por eles em ciclo e
 *   as rotas viram as das maiores origens do continente. Teclado: o botão "Explorar por
 *   continente", as abas do painel e Esc para fechar.
 * - Rotas: cada arco se desenha da origem ao Brasil com uma ponta de luz (svg.createDrawable e
 *   svg.createMotionPath); depois, cometas (cabeça e cauda) correm pelos arcos em ciclo, com um
 *   anel na chegada. Tudo para quando o hero sai da tela.
 * - Sem movimento: sem onda, luz, ping, varredura, cometas nem contagem dos números; o destaque
 *   e o painel aparecem na hora.
 */
"use strict";
(() => {
  const A = window.anime ?? null;
  const reduzido = matchMedia("(prefers-reduced-motion: reduce)");
  const semMovimento = () => reduzido.matches || !A;
  const limitar = (v, a, b) => Math.min(b, Math.max(a, v));
  const variavel = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
  const fmtCompacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  const fmtInteiro = new Intl.NumberFormat("pt-BR");
  const fmt1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmt0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const toneladas = (t) => `${fmtCompacto.format(Math.max(0, t))} t`;
  const pct = (v) => (v == null ? "—" : v > 0 && v < 0.05 ? "< 0,1%" : `${fmt1.format(v)}%`);
  // variação com seta; o sinal sai do número já arredondado (nunca "▼ 0,0%")
  const variacao = (v) => {
    if (v == null || !Number.isFinite(v)) return "—";
    const r = Math.round(v * 10) / 10;
    return r === 0 ? "0,0%" : `${r > 0 ? "▲" : "▼"} ${fmt1.format(Math.abs(r))}%`;
  };

  const $ = (id) => document.getElementById(id);
  const hero = $("inicio");
  const canvas = $("mapa-hero");
  if (!hero || !canvas) return;
  const ctx = canvas.getContext("2d");
  const rotasSvg = $("rotas-hero");
  const legendaRotas = $("legenda-rotas-hero");
  const textoHero = hero.querySelector(".hero-texto");
  const painel = $("painel-continente");
  const dica = $("dica-continente");
  const botaoExplorar = $("explorar-continentes");

  const RECORTE = { oeste: -95, leste: 150 }; // longitudes mostradas: das Américas ao Japão
  const BRASIL = "BRA";
  const CICLO_ONDA = 7000; // ms entre o começo de uma onda e o da seguinte
  const DURACAO_ONDA = 3600;
  const DURACAO_PING = 1500;
  const CICLO_VARREDURA = 3400;
  const GRUPOS = ["N", "P", "K", "NPK"];
  const CURTO = { NA: "Am. do Norte", SA: "Am. do Sul" }; // rótulo das abas (o nome inteiro vai no aria-label)

  let mapa = null;
  let dados = null;
  let pontos = []; // {x, y, brasil, cont}, ordenados por x
  let celulas = new Map(); // linha * 1000 + coluna -> ponto (o ponto sob o cursor)
  let porCont = {}; // continente -> pontos
  let centros = {}; // continente -> ponto mais perto do centro e caixa {x0, y0, x1, y1}
  let geo = null; // {passo, x0, y0, c0} da última medida
  let base = null; // camada estática (canvas fora da tela)
  let largura = 0, altura = 0, escala = 1;
  let cor = { onda: "255, 255, 255" };
  let rodando = false, heroVisivel = true, inicioOnda = 0;

  // --- Medida e projeção ---------------------------------------------------------------------
  function medir() {
    const caixa = hero.getBoundingClientRect();
    largura = Math.round(caixa.width);
    altura = Math.round(caixa.height);
    escala = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(largura * escala);
    canvas.height = Math.round(altura * escala);
  }

  function projetar() {
    const c0 = Math.floor((RECORTE.oeste - mapa.oeste) / mapa.passo);
    const c1 = Math.ceil((RECORTE.leste - mapa.oeste) / mapa.passo);
    const colunas = c1 - c0;
    // Desktop largo: mapa à direita do título. Abaixo de 1100 px: mapa em cima, texto embaixo.
    const empilhado = largura < 1100;
    const area = empilhado
      ? { x: 8, y: largura < 820 ? 132 : 150, w: largura - 16, h: Math.min(altura * 0.4, (largura - 16) * (mapa.linhas / colunas)) }
      : { x: largura * 0.44, y: altura * 0.12, w: largura * 0.56 - 24, h: altura * 0.74 };
    const passo = Math.min(area.w / colunas, area.h / mapa.linhas);
    const x0 = empilhado ? (largura - passo * colunas) / 2 : area.x + area.w - passo * colunas;
    const y0 = area.y + (area.h - passo * mapa.linhas) / 2;
    geo = { passo, x0, y0, c0, largura: passo * colunas, altura: passo * mapa.linhas, empilhado };
    pontos = [];
    celulas = new Map();
    porCont = {};
    mapa.grade.forEach((trincas, linha) => {
      for (let i = 0; i < trincas.length; i += 3) {
        const indice = trincas[i + 2];
        const brasil = mapa.paises[indice] === BRASIL;
        const cont = mapa.continentes?.[indice] ?? null;
        for (let c = trincas[i]; c < trincas[i] + trincas[i + 1]; c++) {
          if (c < c0 || c >= c1) continue;
          const p = { x: x0 + (c - c0 + 0.5) * passo, y: y0 + (linha + 0.5) * passo, brasil, cont };
          pontos.push(p);
          celulas.set(linha * 1000 + c, p);
          if (cont) (porCont[cont] ??= []).push(p);
        }
      }
    });
    pontos.sort((a, b) => a.x - b.x);
    centros = {};
    for (const [cod, lista] of Object.entries(porCont)) {
      let sx = 0, sy = 0, x0c = Infinity, y0c = Infinity, x1c = -Infinity, y1c = -Infinity;
      for (const p of lista) {
        sx += p.x; sy += p.y;
        x0c = Math.min(x0c, p.x); x1c = Math.max(x1c, p.x); y0c = Math.min(y0c, p.y); y1c = Math.max(y1c, p.y);
      }
      const cx = sx / lista.length, cy = sy / lista.length;
      let perto = lista[0], dist = Infinity; // o ponto do continente mais perto do centro (sempre em terra)
      for (const p of lista) {
        const d = (p.x - cx) ** 2 + (p.y - cy) ** 2;
        if (d < dist) { dist = d; perto = p; }
      }
      centros[cod] = { x: perto.x, y: perto.y, x0: x0c, y0: y0c, x1: x1c, y1: y1c };
    }
  }

  function lonLat(lon, lat) {
    return {
      x: geo.x0 + ((lon - mapa.oeste) / mapa.passo - geo.c0) * geo.passo,
      y: geo.y0 + ((mapa.norte - lat) / mapa.passo) * geo.passo,
    };
  }

  const lado = () => Math.max(1.4, geo.passo * 0.58);

  function desenharBase() {
    base = document.createElement("canvas");
    base.width = canvas.width;
    base.height = canvas.height;
    const b = base.getContext("2d");
    b.setTransform(escala, 0, 0, escala, 0, 0);
    const l = lado(), meio = l / 2;
    b.fillStyle = variavel("--hero-ponto");
    for (const p of pontos) if (!p.brasil) b.fillRect(p.x - meio, p.y - meio, l, l);
    b.fillStyle = variavel("--hero-ponto-destaque");
    for (const p of pontos) if (p.brasil) b.fillRect(p.x - meio, p.y - meio, l, l);
    cor = { onda: variavel("--hero-onda") || "255, 255, 255" };
  }

  // Primeiro índice com x >= alvo (busca binária nos pontos ordenados).
  function primeiro(alvo) {
    let a = 0, b = pontos.length;
    while (a < b) {
      const m = (a + b) >> 1;
      if (pontos[m].x < alvo) a = m + 1;
      else b = m;
    }
    return a;
  }

  // Pontos agrupados por intensidade (poucas trocas de fillStyle por quadro).
  function pintarNiveis(niveis, crescer) {
    const l = lado(), n = niveis.length;
    niveis.forEach((lista, k) => {
      if (!lista.length) return;
      const f = (k + 0.5) / n;
      const s = l * (1 + crescer * f), m = s / 2;
      ctx.fillStyle = `rgba(${cor.onda}, ${(0.92 * f).toFixed(3)})`;
      for (const p of lista) ctx.fillRect(p.x - m, p.y - m, s, s);
    });
  }

  // --- Quadro ------------------------------------------------------------------------------------
  // Com movimento e o hero na tela, roda em laço (a onda); sem isso, só quando algo muda.
  let pedido = 0, ultimo = 0;
  const pedirQuadro = () => { if (!pedido) pedido = requestAnimationFrame(quadro); };

  function quadro(agora) {
    pedido = 0;
    const dt = ultimo ? Math.min(64, agora - ultimo) : 16.7;
    ultimo = agora;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (base) ctx.drawImage(base, 0, 0);
    if (!geo) return;
    ctx.setTransform(escala, 0, 0, escala, 0, 0);
    let mais = destacarContinentes(agora, dt);
    mais = desenharPings(agora) || mais;
    mais = desenharLuz(dt) || mais;
    if (rodando) desenharOnda(agora);
    if (rodando || mais) pedirQuadro();
    else ultimo = 0;
  }

  // Onda de brilho: uma faixa vertical que atravessa os pontos da esquerda para a direita.
  function desenharOnda(agora) {
    const t = (agora - inicioOnda) % CICLO_ONDA;
    if (t >= DURACAO_ONDA || !pontos.length) return;
    const minX = pontos[0].x, maxX = pontos[pontos.length - 1].x;
    const faixa = Math.max(40, geo.passo * 9);
    const progresso = 1 - (1 - t / DURACAO_ONDA) ** 2; // ease-out
    const centro = minX - faixa * 2 + (maxX - minX + faixa * 4) * progresso;
    const niveis = [[], [], [], [], []];
    for (let i = primeiro(centro - faixa * 2.5); i < pontos.length && pontos[i].x <= centro + faixa * 2.5; i++) {
      const p = pontos[i];
      const d = (p.x - centro) / faixa;
      const forca = Math.exp(-d * d) * (p.brasil ? 0.25 : 0.55);
      if (forca < 0.03) continue;
      niveis[Math.min(4, Math.floor(forca * 5 / 0.6))].push(p);
    }
    const l = lado(), m = l / 2;
    niveis.forEach((lista, k) => {
      if (!lista.length) return;
      ctx.fillStyle = `rgba(${cor.onda}, ${(((k + 0.5) / 5) * 0.6).toFixed(3)})`;
      for (const p of lista) ctx.fillRect(p.x - m, p.y - m, l, l);
    });
  }

  // Continente sob o cursor (meio aceso) e o aberto (aceso, com varredura em ciclo).
  const brilho = {};
  let sobre = null, aberto = null, abertoEm = 0;
  function destacarContinentes(agora, dt) {
    let mudando = false;
    const k = semMovimento() ? 1 : 1 - Math.pow(0.8, dt / 16.7);
    const l = lado();
    for (const cod in porCont) {
      const alvo = cod === aberto ? 1 : cod === sobre ? 0.5 : 0;
      let b = brilho[cod] ?? 0;
      b += (alvo - b) * k;
      if (Math.abs(alvo - b) < 0.004) b = alvo;
      else mudando = true;
      brilho[cod] = b;
      if (b < 0.01) continue;
      const s = l * (1 + 0.18 * Math.max(0, b - 0.5) * 2), ms = s / 2; // o aberto ganha pontos um pouco maiores
      ctx.fillStyle = `rgba(${cor.onda}, ${(0.18 + 0.7 * b).toFixed(3)})`;
      for (const p of porCont[cod]) ctx.fillRect(p.x - ms, p.y - ms, s, s);
    }
    const c = aberto && centros[aberto];
    if (c && rodando) {
      const t = ((agora - abertoEm) % CICLO_VARREDURA) / CICLO_VARREDURA;
      const faixa = Math.max(26, geo.passo * 6);
      const x = c.x0 - faixa * 2 + (c.x1 - c.x0 + faixa * 4) * t;
      const niveis = [[], [], []];
      for (const p of porCont[aberto]) {
        const d = (p.x - x) / faixa;
        const f = Math.exp(-d * d) * 0.6;
        if (f > 0.08) niveis[Math.min(2, Math.floor(f * 3 / 0.6))].push(p);
      }
      pintarNiveis(niveis, 0.25);
    }
    return mudando;
  }

  // Ping de radar ao abrir um continente: um anel que se abre do clique e acende os pontos dele.
  let pings = [];
  function desenharPings(agora) {
    if (!pings.length) return false;
    pings = pings.filter((pg) => agora - pg.t0 < DURACAO_PING);
    for (const pg of pings) {
      const t = Math.max(0, (agora - pg.t0) / DURACAO_PING);
      const r = (1 - (1 - t) ** 3) * pg.alcance;
      const fade = 1 - t;
      const faixa = Math.max(18, geo.passo * 5);
      ctx.save(); // o anel não sai da área do mapa (nem passa por cima do painel)
      ctx.beginPath();
      ctx.rect(geo.x0, geo.y0, geo.largura, geo.altura);
      ctx.clip();
      ctx.strokeStyle = `rgba(${cor.onda}, ${(0.55 * fade).toFixed(3)})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(pg.x, pg.y, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      const niveis = [[], [], [], []];
      for (const p of porCont[pg.cont] ?? []) {
        const d = Math.hypot(p.x - pg.x, p.y - pg.y) - r;
        if (Math.abs(d) > faixa * 2.2) continue;
        const f = Math.exp(-(d * d) / (faixa * faixa)) * fade;
        if (f > 0.08) niveis[Math.min(3, Math.floor(f * 4))].push(p);
      }
      pintarNiveis(niveis, 0.8);
    }
    return pings.length > 0;
  }

  // Luz do cursor: os pontos perto do ponteiro acendem e crescem um pouco (lente).
  const ponteiro = { x: 0, y: 0, dentro: false };
  const luz = { x: 0, y: 0, forca: 0 };
  function desenharLuz(dt) {
    if (semMovimento()) return false;
    const alvo = ponteiro.dentro ? 1 : 0;
    if (luz.forca < 0.01 && !alvo) { luz.forca = 0; return false; }
    if (luz.forca < 0.01) { luz.x = ponteiro.x; luz.y = ponteiro.y; } // nasce no lugar
    const k = 1 - Math.pow(0.7, dt / 16.7);
    luz.x += (ponteiro.x - luz.x) * k;
    luz.y += (ponteiro.y - luz.y) * k;
    luz.forca += (alvo - luz.forca) * k;
    const R = limitar(geo.passo * 15, 56, 120), alcance = R * 1.7;
    const niveis = [[], [], [], [], [], []];
    for (let i = primeiro(luz.x - alcance); i < pontos.length && pontos[i].x <= luz.x + alcance; i++) {
      const p = pontos[i];
      const dy = p.y - luz.y;
      if (Math.abs(dy) > alcance) continue;
      const dx = p.x - luz.x;
      const f = Math.exp(-(dx * dx + dy * dy) / (R * R)) * luz.forca;
      if (f > 0.06) niveis[Math.min(5, Math.floor(f * 6))].push(p);
    }
    pintarNiveis(niveis, 0.6);
    return Math.abs(ponteiro.x - luz.x) > 0.3 || Math.abs(ponteiro.y - luz.y) > 0.3 || Math.abs(alvo - luz.forca) > 0.01;
  }

  function animar() {
    const deve = !reduzido.matches && heroVisivel && document.visibilityState === "visible" && pontos.length > 0;
    if (deve && !rodando) {
      rodando = true;
      inicioOnda = performance.now() - CICLO_ONDA + 600; // primeira onda logo depois de aparecer
      pedirQuadro();
      entradaRotas?.resume();
      for (const a of cometas) a.resume();
    } else if (!deve && rodando) {
      rodando = false;
      pedirQuadro(); // um último quadro limpo, sem a faixa
      entradaRotas?.pause();
      for (const a of cometas) a.pause();
    }
  }

  // --- Ponteiro: luz, destaque, dica e clique ---------------------------------------------------
  const interativo = (alvo) => alvo?.closest?.("a, button, input, select, label, summary, .painel-continente, .chip-legenda");

  function noMapa(evento) {
    // o canvas pode estar escalado pela animação de saída do hero: a conta usa a caixa real
    const r = canvas.getBoundingClientRect();
    return { x: (evento.clientX - r.left) * (largura / r.width), y: (evento.clientY - r.top) * (altura / r.height) };
  }

  // O ponto do mapa sob (x, y), ou o mais perto nas células vizinhas (costas e ilhas).
  function pontoEm(x, y) {
    if (!geo) return null;
    const c = Math.floor((x - geo.x0) / geo.passo) + geo.c0;
    const l = Math.floor((y - geo.y0) / geo.passo);
    let melhor = null, dist = Infinity;
    for (let dl = -1; dl <= 1; dl++) {
      for (let dc = -1; dc <= 1; dc++) {
        const p = celulas.get((l + dl) * 1000 + c + dc);
        if (!p) continue;
        const d = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d < dist) { dist = d; melhor = p; }
      }
    }
    return melhor;
  }

  function definirSobre(cod) {
    if (cod === sobre) return;
    sobre = cod;
    hero.classList.toggle("sobre-continente", !!cod);
    preencherDica();
    pedirQuadro();
  }

  function preencherDica() {
    if (!dica) return;
    const info = sobre && dados?.mapa?.continentes ? resumo(sobre) : null;
    if (!info) { dica.hidden = true; return; }
    const nome = document.createElement("strong");
    nome.textContent = info.nome;
    const acao = document.createElement("span");
    acao.className = "acao";
    acao.textContent = sobre === aberto ? "clique para fechar" : "clique para ver os dados";
    dica.replaceChildren(nome, ` · ${toneladas(info.t)} em ${info.ano}`, acao);
  }

  function moverDica(evento) {
    if (!dica || !sobre || evento.pointerType === "touch") { if (dica) dica.hidden = true; return; }
    const r = hero.getBoundingClientRect();
    dica.hidden = false;
    let x = evento.clientX - r.left + 16;
    const y = evento.clientY - r.top + 18;
    if (x + dica.offsetWidth > r.width - 12) x = evento.clientX - r.left - dica.offsetWidth - 16;
    dica.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }

  hero.addEventListener("pointermove", (evento) => {
    if (!geo) return;
    const { x, y } = noMapa(evento);
    const fora = !!interativo(evento.target) || evento.pointerType === "touch";
    ponteiro.x = x;
    ponteiro.y = y;
    ponteiro.dentro = !fora;
    const p = fora ? null : pontoEm(x, y);
    definirSobre(porCont[p?.cont] && dados?.mapa?.continentes ? p.cont : null);
    moverDica(evento);
    pedirQuadro();
  });
  hero.addEventListener("pointerleave", () => {
    ponteiro.dentro = false;
    definirSobre(null);
    if (dica) dica.hidden = true;
    pedirQuadro();
  });
  hero.addEventListener("click", (evento) => {
    if (!geo || evento.button !== 0 || interativo(evento.target)) return;
    const ponto = noMapa(evento);
    const p = pontoEm(ponto.x, ponto.y);
    if (p?.cont && porCont[p.cont] && dados?.mapa?.continentes) {
      if (p.cont === aberto) fechar();
      else abrir(p.cont, ponto);
    } else if (aberto && ponto.x >= geo.x0) {
      fechar(); // clique no mar fecha
    }
  });

  // --- Painel do continente ---------------------------------------------------------------------
  function anoCompleto() {
    const a = dados?.anual;
    if (!a?.anos) return null;
    let i = a.anos.length - 1;
    while (i >= 0 && a.n_meses[i] !== 12) i--; // o mesmo ano do chip do Brasil
    return i >= 0 ? a.anos[i] : null;
  }

  function resumo(cod) {
    const m = dados.mapa, ano = anoCompleto();
    const d = m.dados?.[String(ano)] ?? {};
    const linha = (g) => d[g]?.continentes?.find((c) => c[0] === cod);
    const total = linha("TOTAL") ?? [cod, 0, 0, null];
    return {
      ano,
      nome: m.continentes?.[cod] ?? cod,
      t: total[1] ?? 0,
      share: total[2],
      var: total[3],
      grupos: GRUPOS.map((g) => ({ g, t: linha(g)?.[1] ?? 0 })),
      origens: (d.TOTAL?.origens ?? []).filter(([co]) => m.paises?.[co]?.[4] === cod).slice(0, 5)
        .map(([co, t, share]) => ({ nome: m.paises[co][0], t, share })),
    };
  }

  // Continentes do ano, do maior para o menor (abas e botão "Explorar por continente").
  function ordem() {
    const lista = dados?.mapa?.dados?.[String(anoCompleto())]?.TOTAL?.continentes ?? [];
    return lista.map((c) => c[0]).filter((c) => porCont[c] && dados.mapa.continentes?.[c]);
  }

  // Contagem dos números: parte do valor na tela (zero na primeira vez) e cancela a anterior,
  // para duas trocas seguidas não disputarem o mesmo número.
  const contagens = new WeakMap(); // elemento -> { estado: {v}, animacao }
  function contar(elemento, valor, formatar, animado) {
    const anterior = contagens.get(elemento);
    anterior?.animacao?.pause();
    const estado = { v: anterior?.estado.v ?? 0 };
    if (!animado || semMovimento() || valor == null || !Number.isFinite(estado.v)) {
      estado.v = valor;
      contagens.set(elemento, { estado });
      elemento.textContent = formatar(valor);
      return;
    }
    const animacao = A.animate(estado, { v: valor, duration: 900, ease: "outExpo", onUpdate: () => { elemento.textContent = formatar(estado.v); } });
    contagens.set(elemento, { estado, animacao });
  }

  function montarAbas() {
    const abas = $("pc-abas");
    if (!abas || abas.childElementCount) return;
    for (const cod of ordem()) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.cont = cod;
      b.textContent = CURTO[cod] ?? dados.mapa.continentes[cod];
      if (CURTO[cod]) b.setAttribute("aria-label", dados.mapa.continentes[cod]);
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => { if (cod !== aberto) abrir(cod, null, { manterFoco: true }); });
      abas.append(b);
    }
  }

  function preencherPainel(cod, animado) {
    const info = resumo(cod);
    montarAbas();
    $("pc-kicker").textContent = `Continente · ${info.ano}`;
    const titulo = $("pc-titulo");
    if (animado && !semMovimento()) A.animate(titulo, { textContent: A.scrambleText({ text: info.nome, chars: "A-Z", duration: 600 }) });
    else titulo.textContent = info.nome;
    for (const b of $("pc-abas").children) b.setAttribute("aria-pressed", String(b.dataset.cont === cod));
    $("pc-rotulo-t").textContent = `importadas em ${info.ano}`;
    $("pc-rotulo-var").textContent = `sobre ${info.ano - 1}`;
    contar($("pc-t"), info.t, toneladas, animado);
    contar($("pc-share"), info.share, pct, animado);
    // variação: a seta é só visual; o leitor de tela ouve "alta de" ou "queda de"
    const dd = $("pc-var");
    if (!dd.querySelector(".num")) {
      const parte = (classe) => Object.assign(document.createElement("span"), { className: classe });
      dd.replaceChildren(parte("seta"), parte("leitor"), parte("num"));
      dd.firstChild.setAttribute("aria-hidden", "true");
    }
    const [seta, falada, num] = dd.children;
    contar(num, info.var, (v) => {
      const texto = variacao(v);
      const sobe = texto.startsWith("▲"), desce = texto.startsWith("▼");
      seta.textContent = sobe ? "▲" : desce ? "▼" : "";
      falada.textContent = sobe ? "alta de " : desce ? "queda de " : "";
      return texto.replace(/^[▲▼] /, "");
    }, animado);

    // grupos: barra empilhada com as cores fixas e a legenda com o percentual
    const soma = info.grupos.reduce((s, x) => s + x.t, 0) || 1;
    const barra = $("pc-barra");
    if (!barra.childElementCount) {
      for (const g of GRUPOS) {
        const seg = document.createElement("span");
        seg.dataset.g = g;
        seg.style.width = "0%";
        barra.append(seg);
      }
    }
    const lista = $("pc-grupos");
    lista.replaceChildren(...info.grupos.map(({ g, t }) => {
      const li = document.createElement("li");
      li.dataset.g = g;
      const valor = document.createElement("b");
      valor.textContent = pct((100 * t) / soma);
      li.append(document.createElement("i"), `${g} `, valor);
      return li;
    }));
    info.grupos.forEach(({ g, t }) => {
      const seg = barra.querySelector(`[data-g="${g}"]`);
      const alvo = `${((100 * t) / soma).toFixed(2)}%`;
      if (animado && !semMovimento()) A.animate(seg, { width: alvo, duration: 800, ease: "outExpo" });
      else seg.style.width = alvo;
    });

    // maiores origens: barra relativa à maior do continente
    const paises = $("pc-paises");
    const maior = info.origens[0]?.t || 1;
    if (!info.origens.length) {
      const li = document.createElement("li");
      li.className = "vazio";
      li.textContent = `Nenhuma origem com ${fmtInteiro.format(dados.mapa.minimo_origem_t ?? 1000)} t ou mais em ${info.ano}.`;
      paises.replaceChildren(li);
    } else {
      paises.replaceChildren(...info.origens.map((o, i) => {
        const li = document.createElement("li");
        const barraPais = document.createElement("span");
        barraPais.className = "barra";
        const preenchido = document.createElement("i");
        preenchido.style.transform = `scaleX(${(o.t / maior).toFixed(4)})`;
        barraPais.append(preenchido);
        const celula = (classe, texto) => { const s = document.createElement("span"); s.className = classe; s.textContent = texto; return s; };
        li.append(celula("pos", String(i + 1)), celula("nome", o.nome), celula("valor", toneladas(o.t)), celula("share", pct(o.share)), barraPais);
        return li;
      }));
      if (animado && !semMovimento()) {
        const linhas = [...paises.children];
        A.animate(linhas, { opacity: [0, 1], translateX: [-10, 0], duration: 600, delay: A.stagger(60, { start: 120 }), ease: "outExpo",
          onComplete: () => linhas.forEach((li) => { li.style.removeProperty("opacity"); li.style.removeProperty("transform"); }) });
        info.origens.forEach((o, i) => {
          A.animate(linhas[i].querySelector(".barra i"), { scaleX: [0, o.t / maior], duration: 900, delay: 200 + i * 60, ease: "outExpo" });
        });
      }
    }

    const notas = ["Soma de todas as origens do continente no ano, em toneladas."];
    if (cod === "EU") notas.push("A Rússia entra inteira na Europa, como no Natural Earth.");
    if (cod === "NA") notas.push("Inclui o México, a América Central e o Caribe.");
    $("pc-nota").textContent = notas.join(" ");
    $("pc-anuncio").textContent = `${info.nome}: ${toneladas(info.t)} importadas em ${info.ano}, ${pct(info.share)} do total do Brasil.`;
  }

  // Troca o título do hero pelo painel (e volta), com uma passagem lateral curta.
  // `aoMostrar` roda quando o que entra já está visível (o foco só pode ir para algo visível).
  let troca = null;
  function trocarConteudo(abrindo, aoMostrar) {
    troca?.pause();
    const sai = abrindo ? textoHero : painel, entra = abrindo ? painel : textoHero;
    const limpar = (el) => { el.style.removeProperty("opacity"); el.style.removeProperty("transform"); };
    if (semMovimento()) {
      sai.hidden = true;
      entra.hidden = false;
      limpar(sai);
      limpar(entra);
      aoMostrar?.();
      return;
    }
    const lado = abrindo ? 1 : -1;
    troca = A.createTimeline({ onComplete: () => { limpar(entra); troca = null; } })
      .add(sai, { opacity: [1, 0], translateX: [0, -18 * lado], duration: 200, ease: "inQuad",
        onComplete: () => { sai.hidden = true; limpar(sai); entra.hidden = false; aoMostrar?.(); } })
      .add(entra, { opacity: [0, 1], translateX: [18 * lado, 0], duration: 460, ease: "outExpo" });
  }

  function abrir(cod, origem, { manterFoco = false } = {}) {
    if (!porCont[cod] || !dados?.mapa?.continentes || !painel || !textoHero) return;
    const jaAberto = !!aberto;
    aberto = cod;
    abertoEm = performance.now();
    const c = centros[cod];
    if (!semMovimento() && c) {
      const o = origem ?? { x: c.x, y: c.y };
      const alcance = Math.max(...[[c.x0, c.y0], [c.x1, c.y0], [c.x0, c.y1], [c.x1, c.y1]].map(([x, y]) => Math.hypot(x - o.x, y - o.y))) + 24;
      pings.push({ x: o.x, y: o.y, t0: performance.now(), cont: cod, alcance });
    }
    preencherPainel(cod, true);
    const focar = () => { if (!manterFoco) $("pc-titulo").focus({ preventScroll: true }); };
    if (!jaAberto) trocarConteudo(true, focar);
    else focar();
    hero.classList.add("continente-aberto");
    montarRotas({ animar: true });
    preencherDica();
    pedirQuadro();
  }

  function fechar() {
    if (!aberto) return;
    const devolver = painel.contains(document.activeElement);
    aberto = null;
    trocarConteudo(false);
    hero.classList.remove("continente-aberto");
    montarRotas({ animar: true });
    preencherDica();
    pedirQuadro();
    if (devolver && botaoExplorar && !botaoExplorar.hidden) botaoExplorar.focus({ preventScroll: true });
  }

  $("pc-fechar")?.addEventListener("click", fechar);
  botaoExplorar?.addEventListener("click", () => {
    if (aberto) $("pc-titulo").focus({ preventScroll: true });
    else if (ordem().length) abrir(ordem()[0], null);
  });
  document.addEventListener("keydown", (evento) => {
    if (evento.key !== "Escape" || !aberto) return;
    const foco = document.activeElement;
    if (foco && foco !== document.body && !hero.contains(foco)) return; // Esc de outro componente
    fechar();
  });

  function liberarExplorar() {
    if (botaoExplorar) botaoExplorar.hidden = !(dados?.mapa?.continentes && Object.keys(porCont).length && ordem().length);
  }

  // --- Rotas vetoriais (SVG) ------------------------------------------------------------------
  // As maiores origens do último ano completo (todos os grupos) ligadas ao Brasil por arcos com
  // espessura pelas toneladas; com um continente aberto, as maiores dele.
  const NS = "http://www.w3.org/2000/svg";
  let rotas = []; // [{ linha, cauda, rastro, cabeca, ponto, anel, chegada, rotulo, r }]
  let cometas = []; // animações em ciclo (pausam fora da tela)
  let entradaRotas = null;
  let rotasEntraram = false; // a entrada toca uma vez; resize e tema refazem tudo pronto
  const no = (tag, attrs = {}, ...filhos) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
    e.append(...filhos);
    return e;
  };

  function origensDoHero(cont) {
    const m = dados?.mapa, ano = anoCompleto();
    if (!m?.paises || !m.dados || ano == null) return null;
    const itens = (m.dados[String(ano)]?.TOTAL?.origens ?? [])
      .filter(([co]) => !cont || m.paises[co]?.[4] === cont)
      .slice(0, largura < 700 ? 4 : 6)
      .map(([co, t]) => ({ nome: m.paises[co]?.[0], lon: m.paises[co]?.[2], lat: m.paises[co]?.[3], t }))
      .filter((x) => x.nome && Number.isFinite(x.lon) && Number.isFinite(x.lat) && x.t > 0);
    return { ano, itens };
  }

  function arco(a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
    let nx = -dy / d, ny = dx / d;
    if (ny > 0) { nx = -nx; ny = -ny; } // curva para cima, como no mapa de fluxos
    const c = { x: (a.x + b.x) / 2 + nx * d * 0.22, y: (a.y + b.y) / 2 + ny * d * 0.22 };
    return `M${a.x.toFixed(1)} ${a.y.toFixed(1)} Q${c.x.toFixed(1)} ${c.y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
  }

  function pararCometas() {
    for (const a of cometas) a.revert?.();
    cometas = [];
  }

  function montarRotas({ animar: animado = false } = {}) {
    const brasil = mapa?.rotulos?.[BRASIL];
    const fonte = origensDoHero(aberto);
    if (!rotasSvg || !geo || !brasil || !fonte) return;
    entradaRotas?.pause();
    entradaRotas = null;
    pararCometas();
    rotasSvg.setAttribute("viewBox", `0 0 ${largura} ${altura}`);
    const destino = lonLat(brasil[0], brasil[1]);
    const maior = fonte.itens[0]?.t || 1;
    // o mesmo halo difuso dos rótulos do mapa de fluxos, na cor do fundo do hero
    const defs = no("defs", {}, no("filter", { id: "halo-hero", x: "-20%", y: "-80%", width: "140%", height: "260%", "color-interpolation-filters": "sRGB" },
      no("feMorphology", { in: "SourceAlpha", operator: "dilate", radius: 2.8, result: "grosso" }),
      no("feGaussianBlur", { in: "grosso", stdDeviation: 2.2, result: "difuso" }),
      no("feFlood", { class: "halo-cor" }),
      no("feComposite", { in2: "difuso", operator: "in", result: "sombra" }),
      no("feMerge", {}, no("feMergeNode", { in: "sombra" }), no("feMergeNode", { in: "SourceGraphic" }))));
    const camadas = { arcos: no("g"), cometas: no("g"), chegadas: no("g"), pontos: no("g"), rotulos: no("g") };
    const chip = $("chip-brasil");
    const ocupados = [{ x0: destino.x + 8, x1: destino.x + 14 + (chip?.offsetWidth || 260), y0: destino.y - 16, y1: destino.y + 16 }];
    const sobrepoe = (a, b) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
    rotas = fonte.itens.map((item, k) => {
      // o ponto fica dentro do recorte do mapa (o Canadá, por exemplo, passa da borda oeste)
      const o = lonLat(limitar(item.lon, RECORTE.oeste + 2, RECORTE.leste - 2), item.lat);
      const d = arco(o, destino);
      const espessura = 0.9 + 2.6 * Math.sqrt(item.t / maior);
      const id = `rota-hero-${k}`;
      defs.append(no("linearGradient", { id, gradientUnits: "userSpaceOnUse", x1: o.x, y1: o.y, x2: destino.x, y2: destino.y },
        no("stop", { offset: 0, class: "rota-ini" }), no("stop", { offset: 1, class: "rota-fim" })));
      const linha = no("path", { class: "rota", d, stroke: `url(#${id})`, "stroke-width": espessura.toFixed(2) });
      // cometa: cauda longa e fraca, rastro curto e forte, cabeça com brilho
      const cauda = no("path", { class: "cauda", d, "stroke-width": (espessura + 1.4).toFixed(2), opacity: 0 });
      const rastro = no("path", { class: "rastro", d, "stroke-width": (espessura + 0.6).toFixed(2), opacity: 0 });
      const cabeca = no("g", { class: "cabeca", opacity: 0 }, no("circle", { class: "cabeca-brilho", r: (espessura + 4).toFixed(1) }), no("circle", { class: "cabeca-nucleo", r: (espessura * 0.6 + 1.2).toFixed(1) }));
      const r = 2 + 2.6 * Math.sqrt(item.t / maior);
      const ponto = no("circle", { class: "origem-ponto", cx: o.x.toFixed(1), cy: o.y.toFixed(1), r: r.toFixed(2) });
      const anel = no("circle", { class: "origem-anel", cx: o.x.toFixed(1), cy: o.y.toFixed(1), r: r.toFixed(2) });
      const chegada = no("circle", { class: "chegada", cx: destino.x.toFixed(1), cy: destino.y.toFixed(1), r: 3 });
      let rotulo = null;
      if (k < 3 && largura >= 900) { // rótulos só para as três maiores e com o mapa ao lado do título
        const valor = toneladas(item.t);
        const w = item.nome.length * 7.2 + valor.length * 6.2 + 8;
        // acima à direita, acima à esquerda, abaixo à direita, abaixo à esquerda: a primeira livre
        const opcoes = [[1, -1], [-1, -1], [1, 1], [-1, 1]].map(([lx, ly]) => {
          const x = o.x + lx * (r + 6), y = ly < 0 ? o.y - r - 4 : o.y + r + 13;
          return { x, y, ancora: lx > 0 ? "start" : "end", caixa: { x0: lx > 0 ? x : x - w, x1: lx > 0 ? x + w : x, y0: y - 12, y1: y + 4 } };
        });
        const dentro = (c) => c.x0 >= 4 && c.x1 <= largura - 8 && c.y0 >= 4;
        const escolhida = opcoes.find((op) => dentro(op.caixa) && !ocupados.some((c) => sobrepoe(op.caixa, c))) ?? (k === 0 ? opcoes[0] : null);
        if (escolhida) {
          ocupados.push(escolhida.caixa);
          rotulo = no("text", { class: "rotulo-rota", x: escolhida.x.toFixed(1), y: escolhida.y.toFixed(1), "text-anchor": escolhida.ancora },
            no("tspan", { class: "n" }, item.nome), no("tspan", { class: "t", dx: 6 }, valor));
          camadas.rotulos.append(rotulo);
        }
      }
      ocupados.push({ x0: o.x - r - 2, x1: o.x + r + 2, y0: o.y - r - 2, y1: o.y + r + 2 }); // o próprio ponto
      camadas.arcos.append(linha);
      camadas.cometas.append(cauda, rastro, cabeca);
      camadas.pontos.append(anel, ponto);
      camadas.chegadas.append(chegada);
      return { linha, cauda, rastro, cabeca, ponto, anel, chegada, rotulo, r };
    });
    rotasSvg.replaceChildren(defs, camadas.arcos, camadas.cometas, camadas.chegadas, camadas.pontos, camadas.rotulos);
    if (legendaRotas) {
      const nome = aberto && dados.mapa.continentes?.[aberto];
      legendaRotas.lastElementChild.textContent = nome ? `${rotas.length} maiores origens da ${nome} em ${fonte.ano}` : `${rotas.length} maiores origens em ${fonte.ano}`;
      legendaRotas.hidden = !rotas.length;
    }
    if (semMovimento() || !A?.svg) { rotasEntraram = true; return; }
    if (rotasEntraram && !animado) { ligarCometas(); return; }
    const primeira = !rotasEntraram;
    rotasEntraram = true;
    entrarRotas(primeira ? 1 : 0.6);
  }

  // Entrada: o ponto nasce, o arco se desenha até o Brasil com uma ponta de luz que corre na
  // frente do traço e, na chegada, abre um anel; os rótulos se "decodificam".
  function entrarRotas(ritmo) {
    const linha = A.createTimeline({ onComplete: () => { entradaRotas = null; ligarCometas(); } });
    rotas.forEach((x, k) => {
      const inicio = (350 + k * 190) * ritmo;
      const duracao = 1500 * ritmo;
      const [traco] = A.svg.createDrawable(x.linha);
      A.utils.set(x.ponto, { r: 0 });
      A.utils.set(traco, { draw: "0 0" });
      const { translateX, translateY } = A.svg.createMotionPath(x.linha);
      linha
        .add(x.ponto, { r: [0, x.r], duration: 650 * ritmo, ease: "outBack(2.6)" }, inicio)
        .add(x.anel, { r: [x.r, x.r * 4.2], opacity: [0.85, 0], duration: 1100 * ritmo, ease: "outQuad" }, inicio)
        .add(traco, { draw: ["0 0", "0 1"], duration: duracao, ease: "inOutQuart" }, inicio + 120 * ritmo)
        .add(x.cabeca, { translateX, translateY, duration: duracao, ease: "inOutQuart" }, inicio + 120 * ritmo)
        .add(x.cabeca, { opacity: [0, 1, 1, 0], duration: duracao, ease: "linear" }, inicio + 120 * ritmo)
        .add(x.chegada, { r: [2, 18], opacity: [0.9, 0], duration: 1000, ease: "outQuad" }, inicio + 120 * ritmo + duracao);
      if (x.rotulo) {
        A.utils.set(x.rotulo, { opacity: 0 });
        linha.add(x.rotulo, { opacity: [0, 1], duration: 300, ease: "outQuad" }, inicio + 500 * ritmo);
        for (const parte of x.rotulo.children) linha.add(parte, { textContent: A.scrambleText({ chars: "A-Z0-9", duration: 900 * ritmo }) }, inicio + 500 * ritmo);
      }
    });
    entradaRotas = linha;
    if (!rodando) linha.pause(); // hero fora da tela: espera voltar (animar() retoma)
  }

  // Cometa: a frente anda de 0 até o fim do arco mais a cauda; a cauda e o rastro são traços que
  // terminam na frente (stroke-dashoffset), e a cabeça segue a frente pelo próprio arco.
  function ligarCometas() {
    pararCometas();
    if (semMovimento() || !A?.animate) return;
    rotas.forEach((x, k) => {
      x.cabeca.removeAttribute("style"); // a entrada deixa transform e opacidade inline na cabeça
      const comprimento = x.linha.getTotalLength();
      if (comprimento < 12) return;
      const tamCauda = Math.max(24, comprimento * 0.26), tamRastro = Math.max(10, comprimento * 0.09);
      x.cauda.setAttribute("stroke-dasharray", `${tamCauda.toFixed(1)} ${(comprimento * 2 + tamCauda).toFixed(1)}`);
      x.rastro.setAttribute("stroke-dasharray", `${tamRastro.toFixed(1)} ${(comprimento * 2 + tamRastro).toFixed(1)}`);
      const frente = { s: 0 };
      let chegou = false;
      const pintar = () => {
        const s = frente.s;
        x.cauda.setAttribute("stroke-dashoffset", (tamCauda - s).toFixed(1));
        x.rastro.setAttribute("stroke-dashoffset", (tamRastro - s).toFixed(1));
        const p = x.linha.getPointAtLength(Math.min(s, comprimento));
        x.cabeca.setAttribute("transform", `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
        x.cabeca.setAttribute("opacity", s <= comprimento ? "1" : Math.max(0, 1 - (s - comprimento) / (tamRastro * 1.5)).toFixed(2));
        if (s >= comprimento && !chegou) {
          chegou = true;
          if (rodando) A.animate(x.chegada, { r: [2, 14], opacity: [0.75, 0], duration: 900, ease: "outQuad" });
        }
      };
      cometas.push(A.animate(frente, {
        s: [0, comprimento + tamCauda], duration: limitar(comprimento * 4.4, 1800, 4200), ease: "inOutSine",
        delay: 300 + k * 520, loop: true, loopDelay: 1400 + (k % 3) * 650,
        onBegin: () => { x.cauda.setAttribute("opacity", "1"); x.rastro.setAttribute("opacity", "1"); },
        onUpdate: pintar,
        onLoop: () => { chegou = false; },
      }));
    });
    if (!rodando) for (const a of cometas) a.pause();
  }

  // --- Dados (kicker, fonte e chip do Brasil) ------------------------------------------------
  function posicionarChip() {
    const chip = $("chip-brasil");
    const ponto = mapa?.rotulos?.[BRASIL];
    if (!dados || !geo || !ponto || !chip?.textContent) return;
    const { x, y } = lonLat(ponto[0], ponto[1]);
    chip.style.left = `${Math.round(x)}px`;
    chip.style.top = `${Math.round(y)}px`;
    chip.hidden = false;
  }

  function preencher(d) {
    dados = d;
    const meses = d.mensal?.periodos?.length;
    const ncms = d.meta?.ncms?.monitorados ?? d.meta?.ncms?.com_importacao;
    const grupos = Object.keys(d.meta?.grupos ?? {}).filter((g) => g !== "TOTAL").length;
    const partes = [];
    if (meses) partes.push(`${fmtInteiro.format(meses)} meses`);
    if (ncms) partes.push(`${fmtInteiro.format(ncms)} NCMs`);
    if (grupos) partes.push(`${fmtInteiro.format(grupos)} grupos de nutrientes`);
    if (partes.length) $("kicker-hero").textContent = partes.join(", ");
    if (!d.benchmark) $("fonte-hero").textContent = "Fonte: Comex Stat/MDIC · Base cartográfica: Natural Earth";
    // Chip no Brasil: toneladas do último ano completo (12 meses), todos os grupos.
    const ano = anoCompleto();
    const i = d.anual?.anos?.indexOf(ano) ?? -1;
    if (i >= 0 && d.anual.TOTAL?.t?.[i] != null) {
      const chip = $("chip-brasil");
      chip.replaceChildren(document.createElement("strong"), ` · ${toneladas(d.anual.TOTAL.t[i])} importadas em ${ano}`);
      chip.firstChild.textContent = "Brasil";
    }
    posicionarChip();
    montarRotas();
    liberarExplorar();
  }

  // --- Início ------------------------------------------------------------------------------------
  // Mapa em cima do texto (celular e telas estreitas): o painel começa abaixo do mapa.
  function margemDoPainel() {
    if (!geo || !painel) return;
    const chip = hero.querySelector(".chip-legenda");
    const topoLinha = chip ? chip.getBoundingClientRect().bottom - hero.getBoundingClientRect().top + 24 : 0;
    const fimDoMapa = geo.y0 + geo.altura + 18;
    hero.style.setProperty("--margem-painel", `${geo.empilhado ? Math.max(0, Math.round(fimDoMapa - topoLinha)) : 0}px`);
  }

  function redesenhar() {
    if (!mapa) return;
    medir();
    projetar();
    margemDoPainel();
    desenharBase();
    pings = [];
    quadro(performance.now());
    posicionarChip();
    montarRotas();
    liberarExplorar();
    animar();
  }

  async function carregarMapa() {
    try {
      const resposta = await fetch("assets/mapa-pontos.json");
      if (!resposta.ok) return;
      mapa = await resposta.json();
      redesenhar();
    } catch {
      /* sem o mapa, o hero continua legível: só o fundo e o texto */
    }
  }

  document.addEventListener("radar:dados", (e) => preencher(e.detail));
  if (window.radarDados) preencher(window.radarDados);

  new ResizeObserver(() => redesenhar()).observe(hero);
  new IntersectionObserver((entradas) => {
    heroVisivel = entradas[0].isIntersecting;
    animar();
  }).observe(hero);
  document.addEventListener("visibilitychange", animar);
  reduzido.addEventListener?.("change", redesenhar);
  // Troca de tema (botão ou sistema): as cores dos pontos vêm das variáveis do CSS.
  new MutationObserver(redesenhar).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", redesenhar);

  // Para os testes de navegador: o ponto (na tela) que representa cada continente.
  window.radarMapaHero = {
    pontoDoContinente(cod) {
      const c = centros[cod];
      if (!c) return null;
      const r = canvas.getBoundingClientRect();
      return { x: r.left + c.x * (r.width / largura), y: r.top + c.y * (r.height / altura) };
    },
    continenteAberto: () => aberto,
  };

  carregarMapa();
})();
