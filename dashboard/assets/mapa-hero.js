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
 * - Brasil por estado: o clique no Brasil (ou no chip dele) abre o mesmo painel com um quadrado
 *   por UF de destino, na cor da variação sobre o mesmo período do ano anterior (bloco "estados"),
 *   as maiores altas e quedas em toneladas e os filtros de grupo e de ano; as rotas viram as das
 *   maiores origens do grupo e do ano escolhidos. Teclado: setas entre os estados.
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
  const CURTO = { NA: "Am. Norte", SA: "Am. Sul" }; // rótulo das abas (o nome inteiro vai no aria-label)
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const NOMES_GRUPO = { TOTAL: "todos os grupos", N: "nitrogenados", P: "fosfatados", K: "potássicos", NPK: "compostos (NPK)" };
  // Brasil por estado: posição (linha, coluna) de cada UF no mapa de quadrados.
  const GRADE_UF = {
    RR: [0, 1], AP: [0, 2],
    AC: [1, 0], AM: [1, 1], PA: [1, 2], MA: [1, 3], CE: [1, 4], RN: [1, 5],
    RO: [2, 1], TO: [2, 2], PI: [2, 3], PB: [2, 4], PE: [2, 5],
    MT: [3, 1], GO: [3, 2], BA: [3, 3], SE: [3, 4], AL: [3, 5],
    MS: [4, 1], DF: [4, 2], MG: [4, 3], ES: [4, 4],
    PR: [5, 1], SP: [5, 2], RJ: [5, 3],
    SC: [6, 1],
    RS: [7, 1],
  };
  const NOMES_UF = {
    AC: "Acre", AL: "Alagoas", AP: "Amapá", AM: "Amazonas", BA: "Bahia", CE: "Ceará", DF: "Distrito Federal", ES: "Espírito Santo",
    GO: "Goiás", MA: "Maranhão", MT: "Mato Grosso", MS: "Mato Grosso do Sul", MG: "Minas Gerais", PA: "Pará", PB: "Paraíba",
    PR: "Paraná", PE: "Pernambuco", PI: "Piauí", RJ: "Rio de Janeiro", RN: "Rio Grande do Norte", RS: "Rio Grande do Sul",
    RO: "Rondônia", RR: "Roraima", SC: "Santa Catarina", SP: "São Paulo", SE: "Sergipe", TO: "Tocantins",
  };
  const POUCO_VOLUME = 0.005; // abaixo de 0,5% do total nos dois períodos, a cor fica neutra
  const ESCALA_VAR = 50; // a cor satura em ±50%

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

  // Altura de referência do mapa: a da tela. Se o painel aberto alonga o hero, o mapa fica onde
  // está, sem pular de lugar nem de tamanho.
  const alturaDoMapa = () => Math.min(altura, Math.max(480, window.innerHeight || altura));

  function projetar() {
    const c0 = Math.floor((RECORTE.oeste - mapa.oeste) / mapa.passo);
    const c1 = Math.ceil((RECORTE.leste - mapa.oeste) / mapa.passo);
    const colunas = c1 - c0;
    // Desktop largo: mapa à direita do título. Abaixo de 1100 px: mapa em cima, texto embaixo.
    const empilhado = largura < 1100;
    const alturaMapa = alturaDoMapa();
    const area = empilhado
      ? { x: 8, y: largura < 820 ? 132 : 150, w: largura - 16, h: Math.min(alturaMapa * 0.4, (largura - 16) * (mapa.linhas / colunas)) }
      : { x: largura * 0.44, y: alturaMapa * 0.12, w: largura * 0.56 - 24, h: alturaMapa * 0.74 };
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
          if (brasil) (porCont.BR ??= []).push(p); // o Brasil abre por estado
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
      // o ponto do continente mais perto do centro (sempre em terra); o da América do Sul fica fora
      // do Brasil, porque o clique no Brasil abre o painel por estado
      const candidatos = cod !== "BR" && lista.some((p) => !p.brasil) ? lista.filter((p) => !p.brasil) : lista;
      let perto = candidatos[0], dist = Infinity;
      for (const p of candidatos) {
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

  // O Brasil abre por estado; o resto do mapa, pelo continente.
  const disponivel = (cod) => !!porCont[cod] && (cod === "BR" ? !!dados?.estados?.dados : !!dados?.mapa?.continentes?.[cod]);
  function regiaoDe(p) {
    if (!p) return null;
    if (p.brasil && disponivel("BR")) return "BR";
    return disponivel(p.cont) ? p.cont : null;
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
    const info = !sobre ? null : sobre === "BR" ? resumoDoBrasil() : dados?.mapa?.continentes ? resumo(sobre) : null;
    if (!info) { dica.hidden = true; return; }
    const nome = document.createElement("strong");
    nome.textContent = info.nome;
    const acao = document.createElement("span");
    acao.className = "acao";
    acao.textContent = sobre === aberto ? "clique para fechar" : sobre === "BR" ? "clique para ver por estado" : "clique para ver os dados";
    dica.replaceChildren(nome, ` · ${toneladas(info.t)} em ${info.ano}`, acao);
  }

  function moverDica(evento) {
    const noChip = evento.target.closest?.("#chip-brasil");
    if (!dica || !sobre || noChip || evento.pointerType === "touch") { if (dica) dica.hidden = true; return; }
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
    // sobre o chip do Brasil, o Brasil acende (o chip é o rótulo dele)
    const noChip = evento.target.closest?.("#chip-brasil") && evento.pointerType !== "touch";
    definirSobre(noChip ? (disponivel("BR") ? "BR" : null) : regiaoDe(fora ? null : pontoEm(x, y)));
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
    const cod = regiaoDe(pontoEm(ponto.x, ponto.y));
    if (cod) {
      if (cod === aberto) fechar();
      else abrir(cod, ponto);
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
    for (const cod of [...(disponivel("BR") ? ["BR"] : []), ...ordem()]) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.cont = cod;
      b.textContent = cod === "BR" ? "Brasil" : CURTO[cod] ?? dados.mapa.continentes[cod];
      if (cod === "BR") { b.className = "aba-brasil"; b.setAttribute("aria-label", "Brasil por estado"); }
      else if (CURTO[cod]) b.setAttribute("aria-label", dados.mapa.continentes[cod]);
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => { if (cod !== aberto) abrir(cod, null, { manterFoco: true }); });
      abas.append(b);
    }
  }

  // Variação com seta: a seta é só visual; o leitor de tela ouve "alta de" ou "queda de".
  function mostrarVariacao(dd, valor, animado) {
    if (!dd.querySelector(".num")) {
      const parte = (classe) => Object.assign(document.createElement("span"), { className: classe });
      dd.replaceChildren(parte("seta"), parte("leitor"), parte("num"));
      dd.firstChild.setAttribute("aria-hidden", "true");
    }
    const [seta, falada, num] = dd.children;
    contar(num, valor, (v) => {
      const texto = variacao(v);
      const sobe = texto.startsWith("▲"), desce = texto.startsWith("▼");
      seta.textContent = sobe ? "▲" : desce ? "▼" : "";
      falada.textContent = sobe ? "alta de " : desce ? "queda de " : "";
      return texto.replace(/^[▲▼] /, "");
    }, animado);
  }

  function escreverTitulo(texto, animado) {
    const titulo = $("pc-titulo");
    if (animado && !semMovimento()) A.animate(titulo, { textContent: A.scrambleText({ text: texto, chars: "A-Z", duration: 600 }) });
    else titulo.textContent = texto;
  }

  function preencherPainel(cod, animado) {
    const brasil = cod === "BR";
    montarAbas();
    for (const b of $("pc-abas").children) b.setAttribute("aria-pressed", String(b.dataset.cont === cod));
    // painel já aberto passando do continente para o Brasil (ou o contrário): o corpo entra de leve
    const mudou = $("pc-brasil").hidden === brasil && !painel.hidden;
    $("pc-continente").hidden = brasil;
    $("pc-brasil").hidden = !brasil;
    $("pc-link").hidden = brasil;
    $("pc-fonte").textContent = brasil ? "Fonte: Comex Stat/MDIC · UF de destino" : "Fonte: Comex Stat/MDIC · continentes do Natural Earth";
    hero.classList.toggle("painel-brasil", brasil);
    if (mudou && animado && !semMovimento()) {
      const corpo = $(brasil ? "pc-brasil" : "pc-continente");
      A.animate(corpo, { opacity: [0, 1], translateY: [10, 0], duration: 460, ease: "outExpo",
        onComplete: () => { corpo.style.removeProperty("opacity"); corpo.style.removeProperty("transform"); } });
    }
    if (brasil) {
      escreverTitulo("Brasil", animado);
      preencherBrasil(animado, { entrada: true });
      return;
    }
    const info = resumo(cod);
    $("pc-kicker").textContent = `Continente · ${info.ano}`;
    escreverTitulo(info.nome, animado);
    $("pc-rotulo-t").textContent = `importadas em ${info.ano}`;
    $("pc-rotulo-var").textContent = `sobre ${info.ano - 1}`;
    contar($("pc-t"), info.t, toneladas, animado);
    contar($("pc-share"), info.share, pct, animado);
    mostrarVariacao($("pc-var"), info.var, animado);

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

  // --- Brasil por estado -----------------------------------------------------------------------
  // Bloco "estados" do dashboard.json: por ano e grupo, [uf, t, share, variação %, t do mesmo
  // período do ano anterior]. A UF é a de destino da mercadoria (não a do porto de chegada).
  const pb = { grupo: "TOTAL", ano: null, uf: null, sobre: null, foco: null };
  let quadros = null; // uf -> botão do mapa de quadrados
  let atualBrasil = null; // linhas do grupo e do ano escolhidos

  function mesesDoAno(ano) {
    const a = dados?.anual, i = a?.anos?.indexOf(ano) ?? -1;
    return i >= 0 ? a.n_meses[i] : 12;
  }
  function rotuloPeriodo(ano, deslocamento = 0) {
    const n = mesesDoAno(ano);
    const a = ano + deslocamento;
    return n >= 12 ? String(a) : `${n === 1 ? "jan" : `jan–${MESES[n - 1]}`}/${a}`;
  }

  // O último ano completo (o mesmo do chip) e o ano em curso, se ele for parcial.
  function anosDoBrasil() {
    const anos = dados?.anual?.anos ?? [];
    const lista = [anoCompleto(), anos[anos.length - 1]].filter((a, i, l) => a != null && l.indexOf(a) === i);
    return lista.filter((a) => dados.estados?.dados?.[String(a)]);
  }

  function resumoDoBrasil() {
    const ano = anoCompleto(), i = dados?.anual?.anos?.indexOf(ano) ?? -1;
    return i >= 0 && dados.anual.TOTAL?.t?.[i] != null ? { nome: "Brasil", t: dados.anual.TOTAL.t[i], ano } : null;
  }

  function linhasDoBrasil() {
    const bruto = dados.estados.dados?.[String(pb.ano)]?.[pb.grupo] ?? [];
    const linhas = bruto.map(([uf, t, share, v, tAnt]) => ({
      uf, nome: dados.estados.ufs?.[uf] ?? NOMES_UF[uf] ?? uf, t: t ?? 0, share, var: v, tAnt: tAnt ?? 0, delta: (t ?? 0) - (tAnt ?? 0),
    }));
    const total = linhas.reduce((soma, x) => soma + x.t, 0);
    const totalAnt = linhas.reduce((soma, x) => soma + x.tAnt, 0);
    const corte = POUCO_VOLUME * Math.max(total, totalAnt);
    for (const x of linhas) x.pequena = Math.max(x.t, x.tAnt) < corte;
    return { linhas, total, totalAnt, porUf: Object.fromEntries(linhas.map((x) => [x.uf, x])) };
  }

  const nomeUf = (uf) => dados?.estados?.ufs?.[uf] ?? NOMES_UF[uf] ?? uf;
  // sem importação nos dois períodos (menos de 1 t arredonda para zero, mas tem variação)
  const semImportacao = (x) => !x || (x.t <= 0 && x.tAnt <= 0 && x.var == null);
  // menos de 1 t (o JSON arredonda para zero): "menos de 1 t", e não "0 t", se não parou de importar
  const toneladasUf = (x) => (x.t < 0.5 && x.var != null && x.var > -100 ? "menos de 1 t" : toneladas(x.t));
  const deltaT = (d) => `${d > 0.5 ? "+" : d < -0.5 ? "−" : ""}${toneladas(Math.abs(d))}`;
  // variação curta, para caber no quadrado: inteira, ou "×N" acima de +1.000%
  function varCurta(x) {
    if (x.var == null) return x.t > 0 ? "novo" : "–";
    if (x.var >= 999.5 && x.tAnt > 0) return `×${fmt0.format(x.t / x.tAnt)}`;
    const r = Math.round(x.var);
    return `${r > 0 ? "+" : r < 0 ? "−" : ""}${fmt0.format(Math.abs(r))}%`;
  }
  function varFalada(x) {
    if (x.var == null) return x.t > 0 ? "começou a importar" : "sem importação";
    const r = Math.round(x.var * 10) / 10;
    return r === 0 ? "sem variação" : `${r > 0 ? "alta" : "queda"} de ${fmt1.format(Math.abs(r))}%`;
  }

  // Cores da variação: do cinza neutro (variação zero) até o polo de alta (quente) ou de queda
  // (fria), interpoladas em OKLab com o matiz do polo, para o meio do caminho não virar um tom
  // sujo. Os quadrados ficam num palco escuro nos dois temas, como o mapa de fluxos.
  const linear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const gama = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  function oklab(hex) {
    const n = parseInt(hex.replace("#", ""), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => linear(c / 255));
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const k = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * k, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * k, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * k];
  }
  function rgb([L, a, b]) {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const k = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    const canais = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * k, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * k, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * k];
    return `rgb(${canais.map((c) => Math.round(255 * gama(limitar(c, 0, 1)))).join(", ")})`;
  }
  const NEUTRO = [0.37, 0, -0.012];
  function tomDaVariacao(v, polos) {
    const forca = Math.min(1, Math.abs(v) / ESCALA_VAR) ** 0.7;
    const polo = v >= 0 ? polos.alta : polos.queda;
    const lab = NEUTRO.map((c, i) => c + (polo[i] - c) * forca);
    return { fundo: rgb(lab), escuro: lab[0] > 0.64 };
  }
  // Cor do quadrado; estado com pouco volume nos dois períodos fica neutro, e sem importação, vazado.
  function corDoEstado(x, polos) {
    if (semImportacao(x)) return null;
    if (x.pequena) return { fundo: rgb(NEUTRO), escuro: false };
    return tomDaVariacao(x.var ?? 100, polos); // começou a importar: alta cheia
  }

  function textoDoEstado(uf) {
    const x = atualBrasil?.porUf[uf];
    const periodo = rotuloPeriodo(pb.ano), anterior = rotuloPeriodo(pb.ano, -1);
    if (semImportacao(x)) return { nome: nomeUf(uf), visivel: `sem importação em ${periodo} nem em ${anterior}`, falado: `sem importação em ${periodo} nem em ${anterior}` };
    const partes = [`${toneladasUf(x)} em ${periodo}`];
    if (x.t > 0) partes.push(`${pct(x.share)} do total`);
    const mudanca = x.var == null ? `não importou em ${anterior}` : `${variacao(x.var)} sobre ${anterior} (${deltaT(x.delta)})`;
    const falada = x.var == null ? `não importou em ${anterior}` : `${varFalada(x)} sobre ${anterior}, ${deltaT(x.delta)}`;
    const nota = x.pequena ? " · pouco volume, cor neutra" : "";
    return { nome: x.nome, visivel: `${partes.join(" · ")} · ${mudanca}${nota}`, falado: `${partes.join(", ")}; ${falada}` };
  }

  function mostrarDetalhe() {
    const alvo = $("pb-detalhe");
    if (!alvo || !atualBrasil) return;
    const uf = pb.sobre ?? pb.foco ?? pb.uf;
    if (!uf) {
      const maior = atualBrasil.linhas[0];
      alvo.textContent = maior
        ? `Passe o mouse, toque ou use as setas num estado. Maior destino: ${maior.nome}, ${toneladas(maior.t)} (${pct(maior.share)} do total).`
        : "Nenhum estado com importação no período.";
      return;
    }
    const { nome, visivel } = textoDoEstado(uf);
    const forte = document.createElement("strong");
    forte.textContent = nome;
    alvo.replaceChildren(forte, ` · ${visivel}`);
  }

  // Um botão por UF, na grade; setas andam entre vizinhos (foco itinerante: uma parada de Tab).
  function montarQuadros() {
    const caixa = $("pb-mapa");
    if (quadros || !caixa) return;
    quadros = {};
    for (const [uf, [linha, coluna]] of Object.entries(GRADE_UF)) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pb-uf";
      b.dataset.uf = uf;
      b.tabIndex = -1;
      b.style.gridRow = String(linha + 1);
      b.style.gridColumn = String(coluna + 1);
      b.setAttribute("aria-pressed", "false");
      const valor = Object.assign(document.createElement("span"), { className: "v" });
      valor.setAttribute("aria-hidden", "true");
      b.append(Object.assign(document.createElement("b"), { textContent: uf }), valor);
      b.addEventListener("click", () => selecionarUf(uf));
      b.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") sobreUf(uf); });
      b.addEventListener("pointerleave", () => sobreUf(null));
      b.addEventListener("focus", () => { rodarFoco(uf); pb.foco = uf; mostrarDetalhe(); });
      b.addEventListener("blur", () => { pb.foco = null; mostrarDetalhe(); });
      caixa.append(b);
      quadros[uf] = b;
    }
    caixa.addEventListener("keydown", (evento) => {
      const atual = evento.target.closest?.(".pb-uf");
      if (!atual) return;
      const passos = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] };
      const ufs = Object.keys(GRADE_UF);
      let alvo = null;
      if (passos[evento.key]) alvo = vizinhoUf(atual.dataset.uf, ...passos[evento.key]);
      else if (evento.key === "Home") alvo = ufs[0];
      else if (evento.key === "End") alvo = ufs[ufs.length - 1];
      else return;
      evento.preventDefault();
      if (alvo) quadros[alvo].focus();
    });
  }

  // O vizinho na direção da seta: o mais perto à frente, preferindo a mesma linha ou coluna.
  function vizinhoUf(uf, dLinha, dColuna) {
    const [l0, c0] = GRADE_UF[uf];
    let melhor = null, nota = Infinity;
    for (const [outra, [l, c]] of Object.entries(GRADE_UF)) {
      const frente = dLinha ? (l - l0) * dLinha : (c - c0) * dColuna;
      if (frente <= 0) continue;
      const lado = dLinha ? Math.abs(c - c0) : Math.abs(l - l0);
      const n = frente + lado * 2.5;
      if (n < nota) { nota = n; melhor = outra; }
    }
    return melhor;
  }

  function rodarFoco(uf) {
    for (const [u, b] of Object.entries(quadros ?? {})) b.tabIndex = u === uf ? 0 : -1;
  }

  function selecionarUf(uf, { alternar = true } = {}) {
    pb.uf = alternar && pb.uf === uf ? null : uf;
    for (const [u, b] of Object.entries(quadros)) b.setAttribute("aria-pressed", String(u === pb.uf));
    mostrarDetalhe();
  }

  function sobreUf(uf) {
    pb.sobre = uf;
    for (const [u, b] of Object.entries(quadros)) b.classList.toggle("destacada", u === uf);
    for (const b of document.querySelectorAll(".pb-lista button")) b.classList.toggle("destacada", b.dataset.uf === uf);
    mostrarDetalhe();
  }

  function montarFiltrosBrasil() {
    const grupos = $("pb-grupos"), anos = $("pb-anos");
    if (!grupos.childElementCount) {
      for (const g of ["TOTAL", ...GRUPOS]) {
        const b = Object.assign(document.createElement("button"), { type: "button", textContent: g === "TOTAL" ? "Todos" : g });
        b.dataset.grupo = g;
        b.setAttribute("aria-label", g === "TOTAL" ? "Todos os grupos" : `${NOMES_GRUPO[g]} (${g})`);
        b.addEventListener("click", () => { if (pb.grupo !== g) { pb.grupo = g; trocarFiltroBrasil(); } });
        grupos.append(b);
      }
    }
    const lista = anosDoBrasil();
    if (anos.dataset.anos !== lista.join(",")) {
      anos.dataset.anos = lista.join(",");
      anos.replaceChildren(...lista.map((ano) => {
        const b = Object.assign(document.createElement("button"), { type: "button", textContent: rotuloPeriodo(ano) });
        b.dataset.ano = String(ano);
        b.addEventListener("click", () => { if (pb.ano !== ano) { pb.ano = ano; trocarFiltroBrasil(); } });
        return b;
      }));
    }
    anos.hidden = lista.length < 2;
    for (const b of grupos.children) b.setAttribute("aria-pressed", String(b.dataset.grupo === pb.grupo));
    for (const b of anos.children) b.setAttribute("aria-pressed", String(Number(b.dataset.ano) === pb.ano));
  }

  function trocarFiltroBrasil() {
    preencherBrasil(true);
    montarRotas({ animar: true }); // as rotas viram as maiores origens do grupo e do ano
  }

  function montarLista(id, itens, sobe, cores) {
    const ol = $(id);
    if (!itens.length) {
      ol.replaceChildren(Object.assign(document.createElement("li"), { className: "vazio", textContent: sobe ? "Nenhum estado com alta." : "Nenhum estado com queda." }));
      return;
    }
    ol.replaceChildren(...itens.map((x) => {
      const b = Object.assign(document.createElement("button"), { type: "button" });
      b.dataset.uf = x.uf;
      const marca = Object.assign(document.createElement("i"), { className: "tom" }); // ("marca" é a do site)
      marca.style.background = tomDaVariacao(sobe ? ESCALA_VAR : -ESCALA_VAR, cores).fundo;
      const celula = (classe, texto) => Object.assign(document.createElement("span"), { className: classe, textContent: texto });
      b.append(marca, celula("nome", x.nome), celula("valor", deltaT(x.delta)), celula("var", x.var == null ? "novo" : variacao(x.var)));
      b.setAttribute("aria-label", `${x.nome}: ${deltaT(x.delta)}, ${varFalada(x)}`);
      b.addEventListener("click", () => { selecionarUf(x.uf, { alternar: false }); quadros[x.uf]?.focus({ preventScroll: true }); });
      b.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") sobreUf(x.uf); });
      b.addEventListener("pointerleave", () => sobreUf(null));
      const li = document.createElement("li");
      li.append(b);
      return li;
    }));
  }

  function montarTabelaBrasil() {
    const caixa = $("pb-tabela");
    if (!caixa) return;
    const periodo = rotuloPeriodo(pb.ano), anterior = rotuloPeriodo(pb.ano, -1);
    const celula = (tag, texto, num) => Object.assign(document.createElement(tag), { textContent: texto, className: num ? "num" : "" });
    const cabeca = document.createElement("tr");
    cabeca.append(celula("th", "Estado"), celula("th", `t em ${periodo}`, true), celula("th", "Share", true), celula("th", `Sobre ${anterior}`, true), celula("th", "Diferença (t)", true));
    const corpo = atualBrasil.linhas.map((x) => {
      const tr = document.createElement("tr");
      const d = Math.round(x.delta);
      tr.append(celula("td", `${x.nome} (${x.uf})`), celula("td", fmtInteiro.format(Math.round(x.t)), true), celula("td", pct(x.share), true),
        celula("td", x.var == null ? (x.t > 0 ? "novo" : "–") : variacao(x.var), true), celula("td", `${d > 0 ? "+" : d < 0 ? "−" : ""}${fmtInteiro.format(Math.abs(d))}`, true));
      return tr;
    });
    const tabela = document.createElement("table");
    tabela.append(Object.assign(document.createElement("thead"), {}), document.createElement("tbody"));
    tabela.tHead.append(cabeca);
    tabela.tBodies[0].append(...corpo);
    caixa.replaceChildren(tabela);
  }

  function preencherBrasil(animado, { entrada = false } = {}) {
    if (!dados?.estados?.dados) return;
    const anos = anosDoBrasil();
    if (!anos.includes(pb.ano)) pb.ano = anos[0] ?? anoCompleto();
    montarQuadros();
    montarFiltrosBrasil();
    const atual = (atualBrasil = linhasDoBrasil());
    const periodo = rotuloPeriodo(pb.ano), anterior = rotuloPeriodo(pb.ano, -1);
    $("pc-kicker").textContent = `Por estado · ${periodo}`;
    $("pb-rotulo-t").textContent = mesesDoAno(pb.ano) >= 12 ? `importadas em ${periodo}` : "importadas no período";
    $("pb-rotulo-var").textContent = `sobre ${anterior}`;
    const variacaoTotal = atual.totalAnt > 0 ? 100 * (atual.total / atual.totalAnt - 1) : null;
    contar($("pb-t"), atual.total, toneladas, animado);
    mostrarVariacao($("pb-var"), variacaoTotal, animado);
    const ativos = atual.linhas.filter((x) => !semImportacao(x));
    const altas = ativos.filter((x) => x.delta > 0.5).length;
    $("pb-altas").textContent = `${altas} de ${ativos.length}`;

    const cores = { alta: oklab(variavel("--pb-alta") || "#ffa94d"), queda: oklab(variavel("--pb-queda") || "#74b9ff") };
    const tinta = variavel("--pb-tinta") || "#0b1036";
    const gradiente = hero.querySelector(".pb-gradiente");
    if (gradiente) gradiente.style.background = `linear-gradient(90deg, ${[-1, -0.5, 0, 0.5, 1].map((f) => tomDaVariacao(f * ESCALA_VAR, cores).fundo).join(", ")})`;
    for (const [uf, b] of Object.entries(quadros)) {
      const x = atual.porUf[uf];
      const c = corDoEstado(x, cores);
      b.classList.toggle("vazia", !c);
      b.classList.toggle("pequena", !!x?.pequena);
      b.style.background = c?.fundo ?? "";
      b.style.color = c?.escuro ? tinta : "";
      b.lastElementChild.textContent = semImportacao(x) ? "" : varCurta(x);
      const { nome, falado } = textoDoEstado(uf);
      b.setAttribute("aria-label", `${nome}: ${falado}`);
    }
    // a parada de Tab do mapa: o estado escolhido ou o maior destino (que tenha quadrado)
    if (!$("pb-mapa").contains(document.activeElement)) rodarFoco(pb.uf ?? atual.linhas.find((x) => quadros[x.uf])?.uf ?? "PR");

    // as maiores altas e quedas em toneladas (o que mais pesa no total)
    const sobe = atual.linhas.filter((x) => x.delta > 0.5).sort((a, b) => b.delta - a.delta).slice(0, 3);
    const desce = atual.linhas.filter((x) => x.delta < -0.5).sort((a, b) => a.delta - b.delta).slice(0, 3);
    montarLista("pb-sobe", sobe, true, cores);
    montarLista("pb-desce", desce, false, cores);
    montarTabelaBrasil();
    mostrarDetalhe();
    $("pc-nota").textContent = `Cor: variação em toneladas sobre ${anterior} (satura em ±${ESCALA_VAR}%); cinza com menos de ${fmt1.format(100 * POUCO_VOLUME)}% do total. UF de destino da mercadoria: não é a do porto nem a da sede do importador.`;
    const falaTotal = variacaoTotal == null ? "" : `, ${varFalada({ var: variacaoTotal, t: atual.total })} sobre ${anterior}`;
    $("pc-anuncio").textContent = `Brasil por estado, ${NOMES_GRUPO[pb.grupo]}, ${periodo}: ${toneladas(atual.total)}${falaTotal}; ${altas} de ${ativos.length} estados com alta.`
      + (sobe[0] ? ` Maior alta: ${sobe[0].nome}, ${deltaT(sobe[0].delta)}.` : "") + (desce[0] ? ` Maior queda: ${desce[0].nome}, ${deltaT(desce[0].delta)}.` : "");

    if (!animado || semMovimento()) return;
    if (entrada) {
      // os quadrados nascem em diagonal, do noroeste para o sul, como uma varredura
      const lista = Object.values(quadros);
      lista.forEach((b) => b.classList.add("entrando"));
      A.animate(lista, {
        opacity: [0, 1], scale: [0.35, 1], duration: 560, ease: "outBack(1.7)",
        delay: (b) => 140 + (GRADE_UF[b.dataset.uf][0] + GRADE_UF[b.dataset.uf][1]) * 34,
        onComplete: () => lista.forEach((b) => { b.classList.remove("entrando"); b.style.removeProperty("opacity"); b.style.removeProperty("transform"); }),
      });
    }
    const linhas = [...document.querySelectorAll("#pb-sobe li, #pb-desce li")];
    A.animate(linhas, { opacity: [0, 1], translateX: [-8, 0], duration: 520, delay: A.stagger(55, { start: entrada ? 420 : 40 }), ease: "outExpo",
      onComplete: () => linhas.forEach((li) => { li.style.removeProperty("opacity"); li.style.removeProperty("transform"); }) });
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
    if (!disponivel(cod) || !painel || !textoHero) return;
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
    chipBrasil?.setAttribute("aria-expanded", String(cod === "BR"));
    montarRotas({ animar: true });
    preencherDica();
    pedirQuadro();
  }

  let voltarPara = null; // quem abriu o painel pelo teclado recebe o foco de volta
  function fechar() {
    if (!aberto) return;
    const devolver = painel.contains(document.activeElement);
    aberto = null;
    pb.sobre = pb.foco = null;
    trocarConteudo(false);
    hero.classList.remove("continente-aberto", "painel-brasil");
    chipBrasil?.setAttribute("aria-expanded", "false");
    montarRotas({ animar: true });
    preencherDica();
    pedirQuadro();
    const alvo = voltarPara && !voltarPara.hidden && !voltarPara.disabled ? voltarPara : botaoExplorar;
    voltarPara = null;
    if (devolver && alvo && !alvo.hidden) alvo.focus({ preventScroll: true });
  }

  $("pc-fechar")?.addEventListener("click", fechar);
  botaoExplorar?.addEventListener("click", () => {
    if (aberto) $("pc-titulo").focus({ preventScroll: true });
    else if (ordem().length) { voltarPara = botaoExplorar; abrir(ordem()[0], null); }
  });
  const chipBrasil = $("chip-brasil");
  chipBrasil?.addEventListener("click", () => {
    if (aberto === "BR") { fechar(); return; }
    voltarPara = chipBrasil;
    abrir("BR", null);
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

  function origensDoHero(regiao) {
    const m = dados?.mapa, brasil = regiao === "BR";
    const ano = brasil && pb.ano != null ? pb.ano : anoCompleto();
    const grupo = brasil ? pb.grupo : "TOTAL";
    if (!m?.paises || !m.dados || ano == null) return null;
    const itens = (m.dados[String(ano)]?.[grupo]?.origens ?? [])
      .filter(([co]) => brasil || !regiao || m.paises[co]?.[4] === regiao)
      .slice(0, largura < 700 ? 4 : 6)
      .map(([co, t]) => ({ nome: m.paises[co]?.[0], lon: m.paises[co]?.[2], lat: m.paises[co]?.[3], t }))
      .filter((x) => x.nome && Number.isFinite(x.lon) && Number.isFinite(x.lat) && x.t > 0);
    return { ano, grupo, itens };
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
      const nome = aberto && aberto !== "BR" && dados.mapa.continentes?.[aberto];
      const de = fonte.grupo !== "TOTAL" ? ` de ${NOMES_GRUPO[fonte.grupo]}` : "";
      legendaRotas.lastElementChild.textContent = nome ? `${rotas.length} maiores origens da ${nome} em ${rotuloPeriodo(fonte.ano)}` : `${rotas.length} maiores origens${de} em ${rotuloPeriodo(fonte.ano)}`;
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
    // nunca além da borda da tela: se não couber ao lado do ponto, recua (o translate soma 14 px)
    const sobra = largura - 8 - (x + 14 + chip.offsetWidth);
    if (sobra < 0) chip.style.left = `${Math.max(8 - 14, Math.round(x + sobra))}px`;
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
    if (i >= 0 && d.anual.TOTAL?.t?.[i] != null && chipBrasil) {
      const texto = `${toneladas(d.anual.TOTAL.t[i])} importadas em ${ano}`;
      const porEstado = !!d.estados?.dados;
      const nome = document.createElement("strong");
      nome.textContent = "Brasil";
      const ir = Object.assign(document.createElement("span"), { className: "ir", textContent: "→" });
      ir.setAttribute("aria-hidden", "true");
      // "importadas" some em tela estreita (CSS), para o chip caber ao lado do Brasil
      const longo = Object.assign(document.createElement("span"), { className: "longo", textContent: "importadas " });
      chipBrasil.replaceChildren(nome, ` · ${toneladas(d.anual.TOTAL.t[i])} `, longo, `em ${ano}`, ...(porEstado ? [ir] : []));
      chipBrasil.disabled = !porEstado;
      chipBrasil.setAttribute("aria-label", porEstado ? `Brasil: ${texto}. Ver por estado` : `Brasil: ${texto}`);
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

  let medida = "";
  function redesenhar({ tudo = false } = {}) {
    if (!mapa) return;
    medir();
    const chave = `${largura}|${alturaDoMapa()}`;
    if (!tudo && geo && chave === medida) {
      // só a altura do hero mudou (o painel abriu ou fechou): o mapa e as rotas ficam onde estão
      desenharBase();
      rotasSvg?.setAttribute("viewBox", `0 0 ${largura} ${altura}`);
      quadro(performance.now());
      return;
    }
    medida = chave;
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
  const refazer = () => redesenhar({ tudo: true });
  reduzido.addEventListener?.("change", refazer);
  // Troca de tema (botão ou sistema): as cores dos pontos vêm das variáveis do CSS.
  new MutationObserver(refazer).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", refazer);

  // Para os testes de navegador: o ponto (na tela) que representa cada continente.
  window.radarMapaHero = {
    pontoDoContinente(cod) {
      const c = centros[cod];
      if (!c) return null;
      const r = canvas.getBoundingClientRect();
      return { x: r.left + c.x * (r.width / largura), y: r.top + c.y * (r.height / altura) };
    },
    continenteAberto: () => aberto,
    brasil: () => ({ grupo: pb.grupo, ano: pb.ano, uf: pb.uf }),
  };

  carregarMapa();
})();
