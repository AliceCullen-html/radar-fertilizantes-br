/* Mapa de fluxos: de que país vem o adubo e por qual porto ele entra, ano a ano.
 *
 * - Pontos (assets/mapa-pontos.json, Natural Earth 1:110m) desenhados num <canvas> (a camada
 *   cinza fica num canvas fora da tela e só é refeita quando a câmera muda); por cima, um <svg>
 *   com arcos, cometas, marcadores focáveis e rótulos.
 * - Visão geral: um arco por país de origem até o Brasil (as maiores origens do ano), com
 *   largura proporcional às toneladas, e o ranking no painel. Ao clicar num país, os arcos se
 *   abrem até cada porto de chegada; num porto, vêm de cada origem até ele. Rota abaixo do
 *   corte do ano não ganha arco: fica em "outras".
 * - Dados só do bloco "mapa" do dashboard.json (gerado pelo transform a partir de
 *   share_origem_ano, share_urf_ano, rotas_ano e precos_mes_grupo_origem).
 * - Animações com anime.js v4 (animate, createTimeline, stagger, svg.createDrawable,
 *   svg.createMotionPath, cubicBezier): ele anima objetos JS (câmera, entrada, pulso, onda) e o
 *   canvas é redesenhado a cada quadro em que algo mudou.
 * - prefers-reduced-motion (ou sem anime.js): sem câmera nem entrada animadas, sem cometas,
 *   só transições curtas de opacidade. O botão "Pausar animação" para cometas e pulsos.
 * - O grupo vem do filtro da página (evento "radar:filtros"); ano, origem e porto ficam no
 *   hash da URL junto dos outros filtros.
 */
"use strict";
(() => {
  const app = document.getElementById("fluxos-app");
  if (!app) return;
  const A = window.anime ?? null;
  const reduzido = matchMedia("(prefers-reduced-motion: reduce)");
  const semMovimento = () => reduzido.matches || !A;

  // --- Utilidades ------------------------------------------------------------------------
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const suave = (t) => 1 - (1 - t) ** 3; // ease-out cúbico, por ponto
  const fmtInt = new Intl.NumberFormat("pt-BR");
  const fmt0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const fmt1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtAte1 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
  const MENOS = "−";
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const NOMES_GRUPO = { TOTAL: "Todos", N: "N", P: "P", K: "K", NPK: "NPK" };
  const NOME_LONGO = { TOTAL: "Todos os fertilizantes", N: "Nitrogenados (N)", P: "Fosfatados (P)", K: "Potássicos (K)", NPK: "Compostos (NPK)" };
  const DESCRICAO_GRUPO = { TOTAL: "todos os grupos", N: "nitrogenados", P: "fosfatados", K: "potássicos", NPK: "compostos (NPK)" };
  const ORDEM_GRUPOS = ["TOTAL", "N", "P", "K", "NPK"];
  const variavel = (nome) => getComputedStyle(app).getPropertyValue(nome).trim();
  // toneladas: "3,0 mi t", "845 mil t", "980 t" (a mesma precisão em todo o mapa)
  const MILHAO = 999_500;
  const unidadeDe = (t) => (t >= MILHAO ? "mi" : t >= 1000 ? "mil" : "t");
  function toneladasNa(unidade, t) {
    if (t == null) return "–";
    if (unidade === "mi") return `${fmt1.format(t / 1e6)} mi t`;
    if (unidade === "mil") return `${fmt0.format(t / 1e3)} mil t`;
    return `${fmtInt.format(Math.round(t))} t`;
  }
  const toneladas = (t) => (t == null ? "–" : toneladasNa(unidadeDe(t), t));
  const formatoFixo = (final) => (t) => toneladasNa(unidadeDe(final), t); // a unidade não troca durante a contagem
  function falado(t) {
    if (t == null) return "sem dado";
    if (t >= MILHAO) { const m = Math.round(t / 1e5) / 10; return `${fmt1.format(m)} ${m < 2 ? "milhão" : "milhões"} de toneladas`; }
    if (t >= 1000) return `${fmt0.format(t / 1e3)} mil toneladas`;
    return `${fmtInt.format(Math.round(t))} toneladas`;
  }
  const milhoes = (t) => fmt1.format((t ?? 0) / 1e6);
  const porTonelada = (v) => (v == null ? "–" : `US$ ${fmt0.format(v)}/t`);
  const pct = (v) => (v == null ? "–" : `${fmt1.format(v)}%`);
  // variação sobre os mesmos meses do ano anterior; sobre base quase zero, "novo"
  function variacao(v, t) {
    if (v == null || !Number.isFinite(v)) return { texto: "–", classe: "", falado: "" };
    const base = t != null && v > -100 ? t / (1 + v / 100) : null;
    if (base != null && base < (bloco?.minimo_origem_t ?? 1000)) return { texto: "novo", classe: "ganho", falado: "sem volume relevante no ano anterior" };
    const txt = fmt1.format(Math.abs(v));
    if (!/[1-9]/.test(txt)) return { texto: `■ ${txt}%`, classe: "", falado: "estável sobre o ano anterior" };
    if (v >= 1000) return { texto: "▲ >+999%", classe: "ganho", falado: "alta de mais de 999% sobre o ano anterior" };
    return v > 0 ? { texto: `▲ +${txt}%`, classe: "ganho", falado: `alta de ${txt}% sobre o ano anterior` }
      : { texto: `▼ ${MENOS}${txt}%`, classe: "perda", falado: `queda de ${txt}% sobre o ano anterior` };
  }
  function listaNomes(nomes) {
    if (nomes.length < 2) return nomes.join("");
    return `${nomes.slice(0, -1).join(", ")} e ${nomes[nomes.length - 1]}`;
  }
  const slug = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const SVG_TAGS = new Set(["g", "path", "circle", "rect", "text", "tspan", "title", "defs", "linearGradient", "stop"]);
  function el(tag, attrs = {}, ...filhos) {
    const e = SVG_TAGS.has(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "classe") e.setAttribute("class", v);
      else e.setAttribute(k, v);
    }
    for (const f of filhos) if (f != null) e.append(f);
    return e;
  }

  // Animação com anime.js; sem movimento, aplica o valor final na hora (em objetos JS e em
  // atributos/estilos simples de elementos).
  function animar(alvo, parametros) {
    if (semMovimento()) {
      const { onUpdate, onComplete, onBegin, duration, delay, ease, loop, loopDelay, frameRate, ...valores } = parametros;
      for (const unico of Array.isArray(alvo) || alvo instanceof NodeList ? [...alvo] : [alvo]) {
        for (const [k, v] of Object.entries(valores)) {
          const final = Array.isArray(v) ? v[v.length - 1] : typeof v === "function" ? null : v;
          if (final == null) continue;
          if (unico instanceof Element) {
            if (k === "opacity") unico.style.opacity = String(final);
            else if (k === "scaleX" || k === "scaleY") unico.style.transform = `${k}(${final})`;
            else if (k === "translateX" || k === "translateY") unico.style.transform = "";
            else unico.setAttribute(k.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`), String(final));
          } else if (typeof unico === "object") unico[k] = final;
        }
      }
      onBegin?.();
      onUpdate?.();
      onComplete?.();
      return null;
    }
    return A.animate(alvo, parametros);
  }

  // --- Elementos ---------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const palco = $("fluxos-palco");
  const canvas = $("fluxos-canvas");
  const ctx = canvas.getContext("2d");
  const base = document.createElement("canvas"); // camada cinza, refeita só quando a câmera muda
  const bctx = base.getContext("2d");
  const svg = $("fluxos-svg");
  const carregando = $("fluxos-carregando");
  const painel = $("fluxos-painel");
  const titulo = $("fluxos-painel-titulo");
  const abas = $("fluxos-abas");
  const numeros = $("fluxos-numeros");
  const lista = $("fluxos-lista");
  const anuncio = $("fluxos-anuncio");
  const botaoVoltar = $("fluxos-voltar");
  const botaoPlay = $("fluxos-play");
  const botaoPausa = $("fluxos-pausa");
  const faixa = $("fluxos-ano");
  const saidaAno = $("fluxos-ano-valor");
  const marcas = $("fluxos-marcas");
  const caixaGrupo = $("fluxos-grupo");
  const defs = el("defs");
  const camadaArcos = el("g", { classe: "camada-arcos" });
  const camadaNavios = el("g", { classe: "camada-navios", "aria-hidden": "true" });
  const camadaMarcadores = el("g", { classe: "camada-marcadores" });
  const camadaRotulos = el("g", { classe: "camada-rotulos", "aria-hidden": "true" });
  const hub = el("g", { classe: "hub", "aria-hidden": "true" },
    el("circle", { classe: "anel", r: "6" }), el("circle", { classe: "nucleo", r: "4.5" }),
    el("text", { classe: "rotulo", x: "-11", y: "4", "text-anchor": "end" }, el("tspan", { classe: "nome" }, "Brasil")));
  hub.style.opacity = "0";
  svg.append(defs, camadaArcos, camadaNavios, hub, camadaMarcadores, camadaRotulos);

  // --- Estado ------------------------------------------------------------------------------
  let bloco = null; // dashboard.json -> mapa
  let grade = null; // assets/mapa-pontos.json
  let pontos = null; // arrays por ponto
  let anoPadrao = null; // último ano completo: onde a página abre e onde o play para
  // sel: {tipo: "origem", co} | {tipo: "porto", i}; visao: ranking de países ou de portos;
  // destaque: chave do item sob o mouse ou o foco ("o:co" ou "p:i")
  const estado = { ano: null, grupo: "TOTAL", sel: null, visao: "origens", destaque: null };
  const cam = { x: 0, y: 0, z: 1 }; // centro (em células da grade) e pixels por célula
  let geral = { x: 0, y: 0, z: 1 };
  let W = 0, H = 0, dpr = 1;
  const layout = { sobreposto: true, larguraPainel: 0 };
  const anim = { entrada: 0, brasil: 0, fase: 0, onda: 1, hub: 0 };
  let iniciado = false, visivel = false, quadroPedido = 0, cameraAnimando = false, pausado = false;
  const intensidade = new Map(); // ISO3 -> {v}: brilho do país (0 a 1), interpolado
  const marcadores = new Map(); // chave -> {g, corpo, etiqueta, rotulo, dados, raio, naTela}
  const arcos = new Map(); // chave -> {g, path, grad, dados}
  const cometas = new Map(); // chave do arco -> {g, trilha, anims}
  let focoMapa = null; // chave do marcador que recebe o Tab (os outros: setas)
  let pulso = null; // animação do pulso
  let linhaEntrada = null;
  let cores = {};
  const ref = { rota: {}, origem: {}, porto: {}, ano: {} };
  let idGradiente = 0;

  // --- Geometria ---------------------------------------------------------------------------
  const gradeDe = (lon, lat) => ({ gx: (lon - grade.oeste) / grade.passo, gy: (grade.norte - lat) / grade.passo });
  const tela = (gx, gy) => ({ x: (gx - cam.x) * cam.z + W / 2, y: (gy - cam.y) * cam.z + H / 2 });
  const larguraUtil = () => (layout.sobreposto ? Math.max(W * 0.5, W - layout.larguraPainel - 28) : W);

  function enquadrar(caixa, { folga = 0.14, minimo = 0.01, maximo = Infinity } = {}) {
    // caixa em células: {x0, y0, x1, y1}; devolve a câmera que mostra tudo com folga. Com o
    // painel sobre o mapa (desktop), o enquadramento fica na parte à esquerda dele.
    const bw = Math.max(caixa.x1 - caixa.x0, 4), bh = Math.max(caixa.y1 - caixa.y0, 4);
    const util = larguraUtil();
    const z = clamp(Math.min(util / (bw * (1 + folga * 2)), H / (bh * (1 + folga * 2))), minimo, Math.max(minimo, maximo));
    const deslocamento = (W - util) / 2 / z; // abre espaço para o painel à direita
    return { x: (caixa.x0 + caixa.x1) / 2 + deslocamento, y: (caixa.y0 + caixa.y1) / 2, z };
  }

  function medir() {
    const r = palco.getBoundingClientRect();
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = base.width = Math.round(W * dpr);
    canvas.height = base.height = Math.round(H * dpr);
    baseChave = "";
    layout.sobreposto = getComputedStyle(painel).position === "absolute";
    layout.larguraPainel = layout.sobreposto ? painel.offsetWidth : 0;
    // visão geral: das Américas ao leste da Ásia; no celular, um recorte mais justo
    const estreito = W < 600;
    app.toggleAttribute("data-estreito", estreito);
    const a = gradeDe(estreito ? -114 : -122, 72), b = gradeDe(estreito ? 125 : 145, estreito ? -42 : -50);
    geral = enquadrar({ x0: a.gx, y0: a.gy, x1: b.gx, y1: b.gy }, { folga: 0.02 });
  }

  // --- Pontos ------------------------------------------------------------------------------
  function prepararPontos() {
    const n = grade.pontos;
    const p = { gx: new Float32Array(n), gy: new Float32Array(n), pais: new Uint16Array(n), d0: new Float32Array(n),
      dc: new Float32Array(n), nx: new Float32Array(n), ny: new Float32Array(n), n };
    let semente = 7;
    const aleatorio = () => ((semente = (semente * 16807) % 2147483647) / 2147483647);
    let i = 0;
    grade.grade.forEach((trincas, linha) => {
      for (let k = 0; k < trincas.length; k += 3) {
        for (let c = trincas[k]; c < trincas[k] + trincas[k + 1]; c++, i++) {
          p.gx[i] = c + 0.5;
          p.gy[i] = linha + 0.5;
          p.pais[i] = trincas[k + 2];
          p.nx[i] = (aleatorio() - 0.5) * 60;
          p.ny[i] = (aleatorio() - 0.5) * 34;
        }
      }
    });
    const br = centroIso("BRA");
    let max = 0;
    for (let j = 0; j < n; j++) {
      p.d0[j] = Math.hypot(p.gx[j] - br.gx, p.gy[j] - br.gy);
      max = Math.max(max, p.d0[j]);
    }
    for (let j = 0; j < n; j++) p.d0[j] /= max;
    // distância de cada ponto ao centro do próprio país (para o pulso e a onda)
    const centros = grade.paises.map((iso) => centroIso(iso));
    const raio = new Float32Array(grade.paises.length);
    for (let j = 0; j < n; j++) {
      const c = centros[p.pais[j]];
      p.dc[j] = c ? Math.hypot(p.gx[j] - c.gx, p.gy[j] - c.gy) : 0;
      raio[p.pais[j]] = Math.max(raio[p.pais[j]], p.dc[j]);
    }
    for (let j = 0; j < n; j++) p.dc[j] = raio[p.pais[j]] ? p.dc[j] / raio[p.pais[j]] : 0;
    // índices dos pontos de cada país: o passe colorido só percorre os países de origem
    const listas = grade.paises.map(() => []);
    for (let j = 0; j < n; j++) listas[p.pais[j]].push(j);
    p.porPais = listas.map((l) => Int32Array.from(l));
    p.brasil = grade.paises.indexOf("BRA");
    pontos = p;
  }

  function centroIso(iso) {
    for (const v of Object.values(bloco.paises)) if (v[1] === iso) return gradeDe(v[2], v[3]);
    const r = grade.rotulos?.[iso];
    return r ? gradeDe(r[0], r[1]) : null;
  }

  // --- Desenho do canvas ---------------------------------------------------------------------
  function pedirQuadro() {
    if (!quadroPedido) quadroPedido = requestAnimationFrame(desenhar);
  }

  let baseChave = "";
  function posicaoPonto(j, e) {
    let gx = pontos.gx[j], gy = pontos.gy[j], k = 1;
    if (e < 1) {
      k = suave(clamp((e - pontos.d0[j] * 0.6) / 0.4, 0, 1));
      gx += pontos.nx[j] * (1 - k);
      gy += pontos.ny[j] * (1 - k);
    }
    return [gx * cam.z + W / 2 - cam.x * cam.z, gy * cam.z + H / 2 - cam.y * cam.z, k];
  }

  function desenhar() {
    quadroPedido = 0;
    if (!pontos) return;
    const lado = clamp(cam.z * 0.6, 1.2, 8), meio = lado / 2;
    const e = anim.entrada, fase = anim.fase * Math.PI * 2;
    // camada cinza: refeita só quando câmera, tamanho, cor ou entrada mudam
    const chave = `${cam.x.toFixed(3)}|${cam.y.toFixed(3)}|${cam.z.toFixed(4)}|${W}|${H}|${dpr}|${cores.ponto}`;
    if (e < 1 || chave !== baseChave) {
      bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      bctx.clearRect(0, 0, W, H);
      bctx.fillStyle = cores.ponto;
      for (let j = 0; j < pontos.n; j++) {
        const [x, y, k] = posicaoPonto(j, e);
        if (x < -lado || y < -lado || x > W + lado || y > H + lado || k <= 0.06) continue;
        bctx.globalAlpha = 0.16 * k;
        bctx.fillRect(x - meio, y - meio, lado, lado);
      }
      bctx.globalAlpha = 1;
      baseChave = e < 1 ? "" : chave;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(base, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // países de origem: cor do grupo, brilho pela tonelagem e um pulso que corre do centro
    const isoDe = (ch) => (ch?.startsWith("o:") ? bloco.paises[ch.slice(2)]?.[1] : null);
    const selIso = estado.sel?.tipo === "origem" ? bloco.paises[estado.sel.co]?.[1] : null;
    const selIdx = selIso ? grade.paises.indexOf(selIso) : -1;
    const destIso = isoDe(estado.destaque);
    const destIdx = destIso ? grade.paises.indexOf(destIso) : -1;
    const doPorto = estado.sel?.tipo === "porto" ? new Set(recorte().rotas.filter((x) => x[1] === estado.sel.i).map((x) => bloco.paises[x[0]]?.[1])) : null;
    ctx.fillStyle = cores.fluxo;
    grade.paises.forEach((iso, p) => {
      const b = intensidade.get(iso)?.v ?? 0;
      if (b <= 0 && p !== selIdx) return;
      let fator = 1;
      if (selIdx >= 0 && p !== selIdx) fator = 0.35;
      if (doPorto && !doPorto.has(iso)) fator = 0.3;
      if (destIdx >= 0 && p !== destIdx) fator *= 0.4;
      for (const j of pontos.porPais[p]) {
        const [x, y, k] = posicaoPonto(j, e);
        if (x < -lado || y < -lado || x > W + lado || y > H + lado) continue;
        let c = b > 0 ? (0.22 + 0.58 * b) * (0.82 + 0.18 * Math.sin(fase - pontos.dc[j] * 3.2)) : 0;
        if (p === selIdx) c = Math.max(c, 0.3 + 0.7 * suave(clamp((anim.onda - pontos.dc[j] * 0.75) / 0.25, 0, 1)));
        if (p === destIdx) c = 1;
        c = Math.min(1, c * (p === destIdx || p === selIdx ? 1 : fator)) * k;
        if (c <= 0.02) continue;
        ctx.globalAlpha = c;
        ctx.fillRect(x - meio, y - meio, lado, lado);
      }
    });
    // Brasil: destino, em branco
    if (anim.brasil > 0 && pontos.brasil >= 0) {
      ctx.fillStyle = "#ffffff";
      for (const j of pontos.porPais[pontos.brasil]) {
        const [x, y, k] = posicaoPonto(j, e);
        ctx.globalAlpha = Math.max(0.16 * k, anim.brasil * 0.92);
        ctx.fillRect(x - meio, y - meio, lado, lado);
      }
    }
    ctx.globalAlpha = 1;
  }

  // --- Dados do ano e grupo ----------------------------------------------------------------
  const vazio = { t: 0, origens: [], outros_t: 0, rotas: [], portos: [] };
  const recorte = (ano = estado.ano, grupo = estado.grupo) => bloco.dados[String(ano)]?.[grupo] ?? vazio;
  const nomePais = (co) => bloco.paises[co]?.[0] ?? co;
  const porto = (i) => ({ nome: bloco.portos[i][0], uf: bloco.portos[i][1], lon: bloco.portos[i][2], lat: bloco.portos[i][3], urfs: bloco.portos[i][4] });
  const posOrigem = (co) => { const v = bloco.paises[co]; return gradeDe(v[2], v[3]); };
  const posPorto = (i) => { const p = porto(i); return gradeDe(p.lon, p.lat); };
  const meses = (ano) => bloco.n_meses[bloco.anos.indexOf(Number(ano))];
  function rotuloAno(ano) {
    const m = meses(ano);
    return m && m < 12 ? `${ano} (jan–${MESES[m - 1]})` : String(ano);
  }
  // corte das rotas no ano (50 mil t num ano completo, proporcional num ano parcial)
  function textoCorte(ano = estado.ano) {
    const corte = bloco.corte_rota_t?.[bloco.anos.indexOf(Number(ano))] ?? 50000;
    const m = meses(ano);
    return `pelo menos ${fmtAte1.format(corte / 1000)} mil t ${m && m < 12 ? `em jan–${MESES[m - 1]}` : "no ano"}`;
  }
  function calcularReferencias() {
    for (const g of ORDEM_GRUPOS) {
      let rota = 1, origem = 1, prt = 1, ano = 1;
      for (const a of bloco.anos) {
        const r = bloco.dados[String(a)]?.[g];
        if (!r) continue;
        for (const x of r.rotas) rota = Math.max(rota, x[2]);
        for (const x of r.origens) origem = Math.max(origem, x[1]);
        for (const x of r.portos) prt = Math.max(prt, x[1]);
        ano = Math.max(ano, r.t);
      }
      ref.rota[g] = rota;
      ref.origem[g] = origem;
      ref.porto[g] = prt;
      ref.ano[g] = ano;
    }
  }
  // quantas origens no ranking e na visão geral: o que cabe no painel sem rolar
  function quantasOrigens() {
    if (W < 600) return 6;
    if (!layout.sobreposto) return 8;
    return clamp(Math.floor((H - 24 - 330) / 38), 5, 8);
  }

  // --- Intensidade (pulso) dos países de origem ---------------------------------------------
  function atualizarIntensidades(instantaneo = false) {
    const r = recorte();
    const alvo = new Map();
    if (estado.visao === "origens" || estado.sel) {
      for (const o of r.origens) {
        const iso = bloco.paises[o[0]]?.[1];
        if (iso) alvo.set(iso, Math.sqrt(o[1] / ref.origem[estado.grupo]));
      }
    }
    for (const iso of new Set([...intensidade.keys(), ...alvo.keys()])) {
      if (!intensidade.has(iso)) intensidade.set(iso, { v: 0 });
      const obj = intensidade.get(iso);
      const v = alvo.get(iso) ?? 0;
      if (instantaneo || semMovimento()) obj.v = v;
      else animar(obj, { v, duration: 900, ease: "inOutQuad", onUpdate: pedirQuadro });
    }
    pedirQuadro();
  }

  function iniciarPulso() {
    if (semMovimento() || pausado) return;
    if (!pulso) pulso = A.animate(anim, { fase: [0, 1], duration: 2800, ease: "linear", loop: true, frameRate: 30, onUpdate: pedirQuadro });
    if (visivel) pulso.play();
    else pulso.pause();
  }

  // --- Marcadores (origens e portos, focáveis) ----------------------------------------------
  const chaveDaSelecao = () => (estado.sel ? (estado.sel.tipo === "origem" ? `o:${estado.sel.co}` : `p:${estado.sel.i}`) : null);

  function listaMarcadores() {
    const r = recorte();
    const sel = estado.sel;
    const itens = [];
    // na seleção, o país ou porto do outro lado da rota mostra a tonelagem da rota
    const envolvidos = new Map();
    if (sel?.tipo === "origem") r.rotas.filter((x) => x[0] === sel.co).forEach((x) => envolvidos.set(`p:${x[1]}`, x[2]));
    if (sel?.tipo === "porto") r.rotas.filter((x) => x[1] === sel.i).forEach((x) => envolvidos.set(`o:${x[0]}`, x[2]));
    const estreito = W < 600;
    const nOrigens = estado.visao === "portos" && !sel ? 0 : estreito ? 8 : 16;
    r.origens.forEach((o, i) => {
      const chave = `o:${o[0]}`;
      if (i < nOrigens || envolvidos.has(chave) || sel?.co === o[0]) {
        itens.push({ chave, tipo: "origem", co: o[0], t: o[1], share: o[2], vari: o[3], ordem: i, rota: envolvidos.get(chave) });
      }
    });
    const nPortos = estado.visao === "portos" || sel ? 16 : estreito ? 0 : 10;
    r.portos.forEach((p, i) => {
      const chave = `p:${p[0]}`;
      if (i < nPortos || envolvidos.has(chave) || sel?.i === p[0]) {
        itens.push({ chave, tipo: "porto", i: p[0], t: p[1], share: r.t ? (100 * p[1]) / r.t : null, ordem: i, rota: envolvidos.get(chave) });
      }
    });
    return { itens, envolvidos };
  }

  function criarMarcador(item) {
    const g = el("g", { classe: `marcador ${item.tipo}`, tabindex: "-1", role: "button" });
    const corpo = el("g", { classe: "corpo" });
    if (item.tipo === "origem") corpo.append(el("circle", { classe: "anel", r: "6" }), el("circle", { classe: "nucleo", r: "0" }));
    else corpo.append(el("path", { classe: "nucleo", d: "M0 -4 4 0 0 4 -4 0Z" }));
    corpo.append(el("circle", { classe: "foco", r: "12" }), el("circle", { classe: "alvo", r: "16" }));
    g.append(corpo);
    const rotulo = el("text", { classe: "rotulo" }, el("tspan", { classe: "nome" }), el("tspan", { classe: "valor" }));
    const etiqueta = el("g", { classe: "etiqueta" }, rotulo);
    camadaRotulos.append(etiqueta);
    const selecionar = () => (g.__item.tipo === "origem" ? selecionarOrigem(g.__item.co) : selecionarPorto(g.__item.i));
    g.addEventListener("click", () => { pararPlay(); selecionar(); });
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        pararPlay();
        selecionar();
        return;
      }
      const passo = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1, Home: "inicio", End: "fim" }[ev.key];
      if (passo == null) return;
      ev.preventDefault();
      moverFocoMapa(g.__item.chave, passo);
    });
    g.addEventListener("pointerenter", () => destacar(g.__item.chave));
    g.addEventListener("pointerleave", () => destacar(null));
    g.addEventListener("focus", () => { focoMapa = g.__item.chave; aplicarTabindex(); destacar(g.__item.chave); });
    g.addEventListener("blur", () => destacar(null));
    return { g, corpo, etiqueta, rotulo };
  }

  // tamanho: área proporcional às toneladas (do país ou porto; na seleção, da rota)
  const escala = () => (W < 600 ? 0.75 : 1);
  function raioOrigem(item) {
    const t = item.rota ?? item.t, r = item.rota != null ? ref.rota[estado.grupo] : ref.origem[estado.grupo];
    return escala() * Math.max(3, 14 * Math.sqrt(t / r));
  }
  function ladoPorto(item) {
    const t = item.rota ?? item.t, r = item.rota != null ? ref.rota[estado.grupo] : ref.porto[estado.grupo];
    return escala() * Math.max(3, 9 * Math.sqrt(t / r));
  }

  function sincronizarMarcadores({ entrada = false } = {}) {
    const { itens, envolvidos } = listaMarcadores();
    const chaves = new Set(itens.map((x) => x.chave));
    for (const [chave, m] of marcadores) {
      if (!chaves.has(chave)) {
        m.g.remove();
        m.etiqueta.remove();
        marcadores.delete(chave);
      }
    }
    const anoTxt = rotuloAno(estado.ano);
    const chaveSel = chaveDaSelecao();
    let novos = 0;
    for (const item of itens) {
      let m = marcadores.get(item.chave);
      const novo = !m;
      if (!m) {
        m = criarMarcador(item);
        marcadores.set(item.chave, m);
        camadaMarcadores.append(m.g);
      }
      m.g.__item = item;
      m.dados = item;
      const nome = item.tipo === "origem" ? nomePais(item.co) : porto(item.i).nome;
      m.rotulo.querySelector(".nome").textContent = nome;
      m.rotulo.querySelector(".valor").textContent = item.rota != null ? `${toneladas(item.rota)} nesta rota`
        : item.share == null ? toneladas(item.t) : `${toneladas(item.t)} · ${fmt1.format(item.share)}%`;
      if (item.tipo === "origem") {
        const r = raioOrigem(item);
        m.raio = r;
        const nucleo = m.g.querySelector(".nucleo");
        if (novo && !semMovimento()) A.animate(nucleo, { r: [0, r], duration: 900, delay: (entrada ? 0 : 150) + novos++ * 45, ease: "outBack(2)" });
        else animar(nucleo, { r, duration: 800, ease: "inOutQuad" });
        m.g.querySelector(".anel").setAttribute("r", (r + 1.5).toFixed(1));
        m.g.querySelector(".anel").style.animationDuration = `${(3.6 - 1.8 * Math.sqrt(item.t / ref.origem[estado.grupo])).toFixed(2)}s`;
        m.g.querySelector(".alvo").setAttribute("r", String(Math.max(14, r + 6)));
        m.g.querySelector(".foco").setAttribute("r", (r + 5).toFixed(1));
        const v = variacao(item.vari, item.t);
        const rota = item.rota != null ? `, ${falado(item.rota)} na rota até o porto aberto` : "";
        m.g.setAttribute("aria-label", `${nome}: ${falado(item.t)} em ${anoTxt}, ${pct(item.share)} de ${DESCRICAO_GRUPO[estado.grupo]}${v.falado ? `, ${v.falado}` : ""}${rota}. Enter mostra os portos de chegada.`);
      } else {
        const p = porto(item.i);
        const s = ladoPorto(item);
        m.raio = s;
        m.g.querySelector(".nucleo").setAttribute("d", `M0 ${-s} ${s} 0 0 ${s} ${-s} 0Z`);
        m.g.querySelector(".foco").setAttribute("r", (s + 5).toFixed(1));
        const rota = item.rota != null ? `, ${falado(item.rota)} na rota do país aberto` : "";
        m.g.setAttribute("aria-label", `Porto ${p.nome} (${p.uf}): ${falado(item.t)} recebidas em ${anoTxt}${rota}. Enter mostra as origens.`);
      }
      const ativo = item.chave === chaveSel;
      m.g.classList.toggle("ativo", ativo);
      if (ativo) m.g.setAttribute("aria-current", "true");
      else m.g.removeAttribute("aria-current");
      m.g.classList.toggle("envolvido", envolvidos.has(item.chave));
      m.g.classList.toggle("destacado", estado.destaque === item.chave);
      m.etiqueta.classList.toggle("destacado", estado.destaque === item.chave);
    }
    // ordem no DOM (e das setas): origens por toneladas, depois os portos; o nó com foco não
    // sai do lugar (mover o nó tiraria o foco dele)
    const ordenados = [...marcadores.values()].sort((a, b) => (a.dados.tipo === b.dados.tipo ? a.dados.ordem - b.dados.ordem : a.dados.tipo === "origem" ? -1 : 1));
    const focado = ordenados.findIndex((m) => m.g === document.activeElement);
    if (focado < 0) ordenados.forEach((m) => camadaMarcadores.append(m.g));
    else {
      const g = ordenados[focado].g;
      g.before(...ordenados.slice(0, focado).map((m) => m.g));
      g.after(...ordenados.slice(focado + 1).map((m) => m.g));
    }
    posicionarMarcadores();
    if (estado.destaque && !marcadores.has(estado.destaque) && !lista.querySelector(`[data-chave="${estado.destaque}"]`)) destacar(null);
  }

  // Tab entra no mapa por um só marcador; as setas andam entre os visíveis, na ordem do ranking.
  function navegaveis() {
    return [...camadaMarcadores.children].map((g) => marcadores.get(g.__item?.chave)).filter((m) => m && m.naTela);
  }
  function aplicarTabindex() {
    const disponiveis = navegaveis();
    if (!disponiveis.some((m) => m.dados.chave === focoMapa)) focoMapa = disponiveis[0]?.dados.chave ?? null;
    for (const [chave, m] of marcadores) {
      const ti = chave === focoMapa ? "0" : "-1";
      if (m.g.getAttribute("tabindex") !== ti) m.g.setAttribute("tabindex", ti);
      if (m.naTela) m.g.removeAttribute("aria-hidden");
      else if (m.g !== document.activeElement) m.g.setAttribute("aria-hidden", "true");
    }
  }
  function moverFocoMapa(chave, passo) {
    const disponiveis = navegaveis();
    if (!disponiveis.length) return;
    const i = disponiveis.findIndex((m) => m.dados.chave === chave);
    const j = passo === "inicio" ? 0 : passo === "fim" ? disponiveis.length - 1 : (Math.max(i, 0) + passo + disponiveis.length) % disponiveis.length;
    focoMapa = disponiveis[j].dados.chave;
    aplicarTabindex();
    disponiveis[j].g.focus({ preventScroll: true });
  }

  function rotulosVisiveis() {
    const sel = estado.sel;
    const r = recorte();
    const v = new Set();
    const estreito = W < 600;
    if (!sel && estado.visao === "origens") r.origens.slice(0, estreito ? 3 : 7).forEach((o) => v.add(`o:${o[0]}`));
    if (!sel && estado.visao === "portos") r.portos.slice(0, estreito ? 5 : 10).forEach((p) => v.add(`p:${p[0]}`));
    if (sel?.tipo === "origem") {
      v.add(`o:${sel.co}`);
      r.rotas.filter((x) => x[0] === sel.co).slice(0, estreito ? 5 : 9).forEach((x) => v.add(`p:${x[1]}`));
    }
    if (sel?.tipo === "porto") {
      v.add(`p:${sel.i}`);
      r.rotas.filter((x) => x[1] === sel.i).slice(0, estreito ? 5 : 9).forEach((x) => v.add(`o:${x[0]}`));
    }
    if (estado.destaque) v.add(estado.destaque);
    return v;
  }

  const sobrepoe = (a, b) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
  const areaSobreposta = (a, b) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));

  function posicionarMarcadores() {
    const sel = estado.sel;
    const visiveis = rotulosVisiveis();
    const direita = W - (layout.sobreposto ? layout.larguraPainel + 24 : 0);
    const fatorEntrada = anim.entrada < 1 ? clamp((anim.entrada - 0.55) / 0.45, 0, 1) : 1;
    const prioridade = (m) => {
      if (m.g.classList.contains("destacado") || m.g === document.activeElement) return 4;
      if (m.g.classList.contains("ativo")) return 3;
      if (!sel && estado.visao === "origens" && m.dados.tipo === "origem" && m.dados.ordem < 3) return 2; // as 3 do resumo
      return 0;
    };
    // 1º passe: posição, opacidade e obstáculos (os próprios marcadores)
    const obstaculos = [];
    for (const m of marcadores.values()) {
      const d = m.dados;
      const pos = d.tipo === "origem" ? posOrigem(d.co) : posPorto(d.i);
      const s = tela(pos.gx, pos.gy);
      m.s = s;
      m.g.setAttribute("transform", `translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})`);
      m.etiqueta.setAttribute("transform", `translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})`);
      let o = 1;
      if (prioridade(m) === 4) o = 1;
      else if (sel) o = m.g.classList.contains("ativo") || m.g.classList.contains("envolvido") ? 1 : 0.28;
      else if (estado.visao === "origens" && d.tipo === "porto") o = 0.5;
      else if (estado.visao === "portos" && d.tipo === "origem") o = 0;
      o *= fatorEntrada;
      m.g.style.opacity = String(o);
      m.etiqueta.style.opacity = String(o);
      m.naTela = o > 0.1 && s.x >= 0 && s.x <= direita && s.y >= 0 && s.y <= H;
      const r = m.raio ?? 4;
      if (o >= 0.5) obstaculos.push({ m, x0: s.x - r, x1: s.x + r, y0: s.y - r, y1: s.y + r });
    }
    // 2º passe: rótulos, dos mais importantes aos menores, na 1ª de 4 posições livre
    const peso = (m) => m.dados.rota ?? m.dados.t;
    const ordem = [...marcadores.values()].sort((a, b) => prioridade(b) - prioridade(a) || peso(b) - peso(a));
    const ocupados = [];
    for (const m of ordem) {
      const nome = m.rotulo.querySelector(".nome"), valor = m.rotulo.querySelector(".valor");
      const comValor = !(W < 600 && !sel && prioridade(m) < 4); // no celular, a visão geral mostra só o nome
      valor.style.display = comValor ? "" : "none";
      const mostrar = (visiveis.has(m.dados.chave) || m.g === document.activeElement) && m.naTela;
      if (!mostrar) { m.rotulo.style.display = "none"; continue; }
      const s = m.s, r = (m.raio ?? 4) + 5;
      const largura = Math.max(nome.textContent.length * 7, comValor ? valor.textContent.length * 6.4 : 0) + 6;
      const alto = comValor ? 28 : 16;
      const opcoes = [
        { ancora: "start", x: r, yn: -1, yv: 12, caixa: { x0: s.x + r - 2, x1: s.x + r + largura, y0: s.y - 12, y1: s.y - 12 + alto } },
        { ancora: "end", x: -r, yn: -1, yv: 12, caixa: { x0: s.x - r - largura, x1: s.x - r + 2, y0: s.y - 12, y1: s.y - 12 + alto } },
        { ancora: "middle", x: 0, yn: -r - (comValor ? 15 : 3), yv: -r - 2, caixa: { x0: s.x - largura / 2, x1: s.x + largura / 2, y0: s.y - r - alto - 2, y1: s.y - r } },
        { ancora: "middle", x: 0, yn: r + 11, yv: r + 24, caixa: { x0: s.x - largura / 2, x1: s.x + largura / 2, y0: s.y + r, y1: s.y + r + alto + 2 } },
      ];
      const dentro = (c) => c.x0 >= 2 && c.x1 <= direita - 2 && c.y0 >= 2 && c.y1 <= H - 2;
      const livre = (c) => !ocupados.some((o) => sobrepoe(c, o)) && !obstaculos.some((o) => o.m !== m && sobrepoe(c, o));
      let escolhida = opcoes.find((op) => dentro(op.caixa) && livre(op.caixa));
      if (!escolhida && prioridade(m) > 0) {
        // os mais importantes sempre aparecem: na posição de menor sobreposição
        const custo = (op) => [...ocupados, ...obstaculos.filter((o) => o.m !== m)].reduce((soma, o) => soma + areaSobreposta(op.caixa, o), 0) + (dentro(op.caixa) ? 0 : 1e6);
        escolhida = [...opcoes].sort((a, b) => custo(a) - custo(b))[0];
      }
      if (!escolhida) { m.rotulo.style.display = "none"; continue; }
      m.rotulo.style.display = "";
      m.rotulo.setAttribute("text-anchor", escolhida.ancora);
      for (const t of [nome, valor]) t.setAttribute("x", String(escolhida.x));
      nome.setAttribute("y", String(escolhida.yn));
      valor.setAttribute("y", String(escolhida.yv));
      ocupados.push(escolhida.caixa);
    }
    aplicarTabindex();
    posicionarHub();
  }

  function posicionarHub() {
    const br = centroIso("BRA");
    if (!br) return;
    const s = tela(br.gx, br.gy);
    hub.setAttribute("transform", `translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})`);
    hub.style.opacity = String(anim.hub);
  }

  // --- Destaque (mouse, foco ou linha do ranking) ---------------------------------------------
  function destacar(chave) {
    if (chave && chave === chaveDaSelecao()) chave = null; // o item aberto não apaga o resto
    if (estado.destaque === chave) return;
    estado.destaque = chave;
    svg.classList.toggle("com-destaque", !!chave);
    for (const [k, m] of marcadores) {
      m.g.classList.toggle("destacado", k === chave);
      m.etiqueta.classList.toggle("destacado", k === chave);
    }
    const doArco = (d) => !!chave && (chave === `o:${d.co}` || chave === `p:${d.i}`);
    for (const a of arcos.values()) a.g.classList.toggle("destacado", doArco(a.dados));
    for (const c of cometas.values()) c.g.classList.toggle("destacado", doArco(c.dados));
    for (const li of lista.querySelectorAll(".linha[data-chave]")) li.classList.toggle("destacado", li.dataset.chave === chave);
    posicionarMarcadores();
    pedirQuadro();
  }

  // --- Arcos e cometas ------------------------------------------------------------------------
  function listaArcos() {
    const r = recorte();
    const sel = estado.sel;
    if (sel?.tipo === "origem") return r.rotas.filter((x) => x[0] === sel.co).map(rotaParaArco);
    if (sel?.tipo === "porto") return r.rotas.filter((x) => x[1] === sel.i).map(rotaParaArco);
    if (estado.visao === "portos") return [];
    return r.origens.slice(0, quantasOrigens()).map((o) => ({ chave: `b:${o[0]}`, tipo: "brasil", co: o[0], i: null, t: o[1] }));
  }
  const rotaParaArco = (x) => ({ chave: `r:${x[0]}|${x[1]}`, tipo: "rota", co: x[0], i: x[1], t: x[2], frete: x[3] });

  function extremos(arco) {
    const a = posOrigem(arco.co);
    const b = arco.tipo === "brasil" ? centroIso("BRA") : posPorto(arco.i);
    return [tela(a.gx, a.gy), tela(b.gx, b.gy)];
  }

  function geometria(arco) {
    const [s, f] = extremos(arco);
    const dx = f.x - s.x, dy = f.y - s.y, d = Math.hypot(dx, dy) || 1;
    let nx = -dy / d, ny = dx / d;
    if (ny > 0) { nx = -nx; ny = -ny; } // curva para cima
    const curva = arco.tipo === "brasil" ? 0.2 : 0.24;
    const c = { x: (s.x + f.x) / 2 + nx * d * curva, y: (s.y + f.y) / 2 + ny * d * curva };
    return { d: `M${s.x.toFixed(1)} ${s.y.toFixed(1)} Q${c.x.toFixed(1)} ${c.y.toFixed(1)} ${f.x.toFixed(1)} ${f.y.toFixed(1)}`, s, f };
  }

  const larguraArco = (item) => (item.tipo === "brasil" ? 1.2 + 12 * (item.t / ref.origem[estado.grupo]) : 1.2 + 10 * (item.t / ref.rota[estado.grupo]));

  function criarArco() {
    const id = `fx-grad-${++idGradiente}`;
    // degradê do país ao destino: apagado na origem, cheio no meio, um pouco mais leve no fim
    const grad = el("linearGradient", { id, gradientUnits: "userSpaceOnUse" },
      el("stop", { offset: "0", "stop-opacity": "0.3" }), el("stop", { offset: "0.6", "stop-opacity": "0.92" }), el("stop", { offset: "1", "stop-opacity": "0.6" }));
    defs.append(grad);
    const path = el("path", { classe: "arco", stroke: `url(#${id})` });
    const g = el("g", { classe: "fluxo" }, path);
    camadaArcos.append(g);
    return { g, path, grad };
  }

  function sincronizarArcos({ desenharNovos = true, atraso = 0 } = {}) {
    const itens = listaArcos();
    const chaves = new Set(itens.map((x) => x.chave));
    for (const [chave, a] of arcos) {
      if (chaves.has(chave)) continue;
      arcos.delete(chave);
      removerCometa(chave);
      const fim = () => { a.g.remove(); a.grad.remove(); };
      if (semMovimento()) fim();
      else A.animate(a.g, { opacity: 0, duration: 380, ease: "outQuad", onComplete: fim });
    }
    let novos = 0;
    // os maiores por baixo: as linhas finas ficam visíveis por cima das grossas
    for (const item of [...itens].sort((a, b) => b.t - a.t)) {
      let a = arcos.get(item.chave);
      const largura = larguraArco(item);
      if (!a) {
        a = { ...criarArco(), dados: item };
        arcos.set(item.chave, a);
        a.path.setAttribute("d", geometria(item).d);
        a.path.setAttribute("stroke-width", largura.toFixed(2));
        if (desenharNovos && !semMovimento()) {
          const [linha] = A.svg.createDrawable(a.path);
          A.animate(linha, { draw: ["0 0", "0 1"], duration: 1000, delay: atraso + novos * 110, ease: "inOutCubic" });
        }
        novos++;
      } else {
        a.dados = item;
        animar(a.path, { strokeWidth: largura, duration: 800, ease: "inOutQuad" });
      }
      camadaArcos.append(a.g);
      a.g.classList.toggle("destacado", !!estado.destaque && (estado.destaque === `o:${item.co}` || estado.destaque === `p:${item.i}`));
      a.path.dataset.t = String(item.t);
    }
    reposicionarArcos();
  }

  function reposicionarArcos() {
    for (const a of arcos.values()) {
      const { d, s, f } = geometria(a.dados);
      a.path.setAttribute("d", d);
      a.grad.setAttribute("x1", s.x.toFixed(1));
      a.grad.setAttribute("y1", s.y.toFixed(1));
      a.grad.setAttribute("x2", f.x.toFixed(1));
      a.grad.setAttribute("y2", f.y.toFixed(1));
    }
  }

  function removerCometa(chave) {
    const c = cometas.get(chave);
    if (!c) return;
    for (const a of c.anims) a.revert?.();
    c.g.remove();
    cometas.delete(chave);
  }
  function pararNavios() {
    for (const chave of [...cometas.keys()]) removerCometa(chave);
  }

  // Um cometa por arco (os maiores): a cabeça segue o arco pelo motion path e o rastro é um
  // traço do próprio arco que anda junto (stroke-dashoffset), no mesmo ritmo. Os cometas de
  // arcos que continuam (troca de ano sem mexer na câmera) seguem rodando.
  function soltarNavios() {
    if (semMovimento() || !visivel || !iniciado || pausado || cameraAnimando) { pararNavios(); return; }
    const escolhidos = [...arcos.values()].sort((a, b) => b.dados.t - a.dados.t).slice(0, W < 600 ? 4 : 8);
    const chaves = new Set(escolhidos.map((a) => a.dados.chave));
    for (const chave of [...cometas.keys()]) if (!chaves.has(chave)) removerCometa(chave);
    escolhidos.forEach((a, k) => {
      const largura = (Math.min(larguraArco(a.dados), 3.2) + 0.6).toFixed(1);
      const existente = cometas.get(a.dados.chave);
      if (existente) { existente.trilha.setAttribute("stroke-width", largura); existente.dados = a.dados; return; }
      const comprimento = a.path.getTotalLength();
      if (comprimento < 8) return;
      const duracao = clamp(comprimento * 6.5, 1600, 5200);
      const rastro = clamp(comprimento * 0.2, 16, 80);
      const trilha = el("path", { classe: "rastro", d: a.path.getAttribute("d"), "stroke-width": largura,
        "stroke-dasharray": `${rastro.toFixed(1)} ${(comprimento + rastro).toFixed(1)}`, "stroke-dashoffset": rastro.toFixed(1) });
      const cabeca = el("g", { classe: "cabeca" }, el("circle", { classe: "brilho", r: "6" }), el("circle", { classe: "navio", r: "2.6" }));
      const g = el("g", { classe: "cometa" }, trilha, cabeca);
      g.style.opacity = "0"; // nada parado na origem durante o atraso
      const doArco = !!estado.destaque && (estado.destaque === `o:${a.dados.co}` || estado.destaque === `p:${a.dados.i}`);
      g.classList.toggle("destacado", doArco);
      camadaNavios.append(g);
      const comum = { duration: duracao, delay: (k * 380) % duracao, loopDelay: 700, ease: "linear", loop: true };
      const { translateX, translateY } = A.svg.createMotionPath(a.path);
      const anims = [
        A.animate(trilha, { strokeDashoffset: [rastro, rastro - comprimento], ...comum }),
        A.animate(cabeca, { translateX, translateY, ...comum, onBegin: () => g.style.removeProperty("opacity") }),
      ];
      cometas.set(a.dados.chave, { g, trilha, anims, dados: a.dados });
    });
  }

  // --- Câmera --------------------------------------------------------------------------------
  let animCamera = null;
  function moverCamera(alvo, aoTerminar) {
    animCamera?.pause();
    cameraAnimando = true;
    pararNavios();
    const fim = () => {
      cameraAnimando = false;
      reposicionarArcos();
      posicionarMarcadores();
      soltarNavios();
      aoTerminar?.();
    };
    animCamera = animar(cam, {
      x: alvo.x, y: alvo.y, z: alvo.z, duration: 1200, ease: A ? A.cubicBezier(0.65, 0, 0.35, 1) : "inOutCubic",
      onUpdate: () => { reposicionarArcos(); posicionarMarcadores(); pedirQuadro(); },
      onComplete: fim,
    });
  }

  function caixaDe(pontosGrade) {
    const xs = pontosGrade.map((p) => p.gx), ys = pontosGrade.map((p) => p.gy);
    return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
  }

  // câmera de cada estado: seleção (país ou porto e as rotas), aba Portos (a costa) ou geral
  function alvosDaSelecao() {
    const sel = estado.sel;
    const r = recorte();
    if (sel.tipo === "origem") {
      const rotas = r.rotas.filter((x) => x[0] === sel.co);
      return [posOrigem(sel.co), ...(rotas.length ? rotas.map((x) => posPorto(x[1])) : [centroIso("BRA")])];
    }
    const alvos = [posPorto(sel.i), ...r.rotas.filter((x) => x[1] === sel.i).map((x) => posOrigem(x[0]))];
    return alvos.length > 1 ? alvos : [alvos[0], centroIso("BRA")];
  }
  function cameraDoEstado() {
    if (estado.sel) return enquadrar(caixaDe(alvosDaSelecao()), { minimo: geral.z, maximo: geral.z * 7 });
    if (estado.visao === "portos") {
      const portos = recorte().portos.slice(0, 10).map((p) => posPorto(p[0]));
      if (portos.length) return enquadrar(caixaDe(portos), { folga: 0.35, minimo: geral.z, maximo: geral.z * 5 });
    }
    return { ...geral };
  }
  function algumForaDaTela(alvos) {
    const direita = W - (layout.sobreposto ? layout.larguraPainel + 24 : 0);
    return alvos.some((a) => { const s = tela(a.gx, a.gy); return s.x < 8 || s.x > direita - 8 || s.y < 8 || s.y > H - 8; });
  }

  function mostrarHub(visivelHub) {
    animar(anim, { hub: visivelHub ? 1 : 0, duration: 500, ease: "outQuad", onUpdate: posicionarHub });
  }

  // --- Seleção ---------------------------------------------------------------------------------
  function aoSelecionar(instantaneo) {
    const doPainel = painel.contains(document.activeElement);
    preencherPainel();
    sincronizarMarcadores();
    destacar(null);
    sincronizarArcos({ atraso: instantaneo ? 0 : 500 });
    atualizarIntensidades();
    atualizarLegenda();
    mostrarHub(false);
    const alvo = cameraDoEstado();
    if (instantaneo) { animCamera?.pause(); cameraAnimando = false; Object.assign(cam, alvo); reposicionarArcos(); posicionarMarcadores(); soltarNavios(); pedirQuadro(); }
    else moverCamera(alvo);
    botaoVoltar.hidden = false;
    // a lista do painel foi refeita: o foco vai para o título do painel, e não para o <body>
    if (doPainel) titulo.focus({ preventScroll: true });
  }

  function selecionarOrigem(co, { instantaneo = false } = {}) {
    if (!bloco.paises[co]) return;
    estado.sel = { tipo: "origem", co };
    gravarUrl();
    const r = recorte();
    const rotas = r.rotas.filter((x) => x[0] === co);
    const o = r.origens.find((x) => x[0] === co);
    anim.onda = 0;
    animar(anim, { onda: 1, duration: 1100, delay: 250, ease: "linear", onUpdate: pedirQuadro });
    aoSelecionar(instantaneo);
    anunciar(o ? `${nomePais(co)}: ${falado(o[1])} em ${rotuloAno(estado.ano)}, ${pct(o[2])} de ${DESCRICAO_GRUPO[estado.grupo]}; ${rotas.length} ${rotas.length === 1 ? "porto" : "portos"} com rota de ${textoCorte()}.`
      : `${nomePais(co)}: sem importação registrada em ${rotuloAno(estado.ano)} neste grupo.`);
  }

  function selecionarPorto(i, { instantaneo = false } = {}) {
    if (!bloco.portos[i]) return;
    estado.sel = { tipo: "porto", i };
    gravarUrl();
    const r = recorte();
    const rotas = r.rotas.filter((x) => x[1] === i);
    const tp = r.portos.find((x) => x[0] === i)?.[1] ?? 0;
    aoSelecionar(instantaneo);
    anunciar(`Porto ${porto(i).nome}: ${falado(tp)} recebidas em ${rotuloAno(estado.ano)}; ${rotas.length} ${rotas.length === 1 ? "origem" : "origens"} com rota de ${textoCorte()}.`);
  }

  function voltar() {
    if (!estado.sel) return;
    const anterior = chaveDaSelecao();
    const focoFora = painel.contains(document.activeElement) || document.activeElement === botaoVoltar;
    estado.sel = null;
    gravarUrl();
    destacar(null);
    preencherPainel();
    sincronizarMarcadores();
    sincronizarArcos({ atraso: 350 });
    atualizarIntensidades();
    atualizarLegenda();
    mostrarHub(estado.visao === "origens");
    moverCamera(cameraDoEstado());
    // devolve o foco à linha do item que estava aberto (ou ao título), antes de esconder o botão
    if (focoFora) (lista.querySelector(`.linha[data-chave="${anterior}"]`) ?? titulo).focus({ preventScroll: true });
    botaoVoltar.hidden = true;
    anunciar(`Visão geral: ${falado(recorte().t)} de ${DESCRICAO_GRUPO[estado.grupo]} em ${rotuloAno(estado.ano)}.`);
    pedirQuadro();
  }

  function definirVisao(v) {
    if (v === estado.visao) return;
    estado.visao = v;
    marcarSegmentado(abas, "visao", v, false);
    if (estado.sel) return;
    preencherPainel();
    sincronizarMarcadores();
    sincronizarArcos();
    atualizarIntensidades();
    atualizarLegenda();
    mostrarHub(v === "origens");
    moverCamera(cameraDoEstado());
  }

  // legenda: diz o que o mapa mostra em cada estado
  function atualizarLegenda() {
    const sel = estado.sel;
    const pais = $("fluxos-legenda-pais"), arco = $("fluxos-legenda-arco"), prt = $("fluxos-legenda-porto");
    const texto = (li, t) => { li.lastChild.textContent = t; };
    pais.hidden = !sel && estado.visao === "portos";
    arco.hidden = !sel && estado.visao === "portos";
    prt.hidden = !sel && estado.visao === "origens" && W < 600; // no celular, a visão geral não desenha portos
    if (sel?.tipo === "porto") texto(pais, "área do círculo: toneladas na rota");
    else texto(pais, "área do círculo: toneladas do país");
    texto(arco, sel ? "espessura: toneladas na rota" : "espessura: toneladas até o Brasil");
    texto(prt, sel?.tipo === "origem" ? "porto de chegada (tamanho: toneladas na rota)" : !sel && estado.visao === "portos" ? "porto (tamanho: toneladas recebidas)" : "porto de chegada");
  }

  // --- Painel ---------------------------------------------------------------------------------
  const contadores = new Map(); // id -> {v}
  function contador(id, valor, formato) {
    const dd = $(id);
    if (!dd) return;
    if (valor == null) { dd.textContent = "–"; contadores.delete(id); return; }
    const obj = contadores.get(id) ?? { v: 0 };
    contadores.set(id, obj);
    if (semMovimento()) { obj.v = valor; dd.textContent = formato(valor); return; }
    A.animate(obj, { v: valor, duration: 900, ease: "outExpo", onUpdate: () => { dd.textContent = formato(obj.v); } });
  }

  let modoPainel = null; // "geral" | "origem" | "porto"
  function preencherPainel() {
    const sel = estado.sel;
    const modo = sel ? sel.tipo : "geral";
    if (modo !== modoPainel) {
      lista.replaceChildren();
      numeros.replaceChildren();
      delete numeros.dataset.tipo;
      contadores.clear();
      modoPainel = modo;
    }
    abas.hidden = !!sel;
    numeros.hidden = !sel;
    const ano = rotuloAno(estado.ano);
    if (!sel) preencherRanking(ano);
    else if (sel.tipo === "origem") preencherOrigem(ano);
    else preencherPorto(ano);
  }

  function cabecalhoLista(esquerda, direita) {
    const c = $("fluxos-cabecalho-lista");
    c.firstChild.textContent = esquerda;
    c.lastChild.textContent = direita;
  }

  function preencherRanking(ano) {
    const r = recorte();
    const porPais = estado.visao === "origens";
    $("fluxos-painel-kicker").textContent = `${NOME_LONGO[estado.grupo]} · ${ano}`;
    titulo.textContent = porPais ? "Principais origens" : "Principais portos";
    $("fluxos-dica").textContent = porPais ? "Clique num país, no mapa ou na lista, para ver por quais portos ele entra." : "Clique num porto para ver de onde vem a carga.";
    $("fluxos-lista-titulo").textContent = "";
    cabecalhoLista("toneladas", porPais ? "% do total · var. a/a" : "% do total");
    const n = quantasOrigens();
    const itens = porPais
      ? r.origens.slice(0, n).map((o, k) => {
        const v = variacao(o[3], o[1]);
        return { chave: `o:${o[0]}`, pos: k + 1, nome: nomePais(o[0]), t: o[1], share: o[2], vari: v,
          rotulo: `${nomePais(o[0])}: ${falado(o[1])}, ${pct(o[2])} do total${v.falado ? `, ${v.falado}` : ""}. Enter mostra os portos de chegada.`, acao: () => selecionarOrigem(o[0]) };
      })
      : r.portos.slice(0, n).map((p, k) => ({ chave: `p:${p[0]}`, pos: k + 1, nome: `${porto(p[0]).nome} (${porto(p[0]).uf})`, t: p[1], share: r.t ? (100 * p[1]) / r.t : null,
        rotulo: `Porto ${porto(p[0]).nome}: ${falado(p[1])} recebidas, ${pct(r.t ? (100 * p[1]) / r.t : null)} do total. Enter mostra as origens.`, acao: () => selecionarPorto(p[0]) }));
    const soma = itens.reduce((s, x) => s + x.t, 0);
    const resto = Math.max(0, r.t - soma);
    if (resto > 0.5) {
      const qtd = (porPais ? r.origens.length : r.portos.length) - itens.length;
      const nome = porPais ? "Outras origens" : "Outros portos";
      const detalhe = porPais ? `${qtd > 0 ? `${qtd} ${qtd === 1 ? "país" : "países"} fora do ranking e ` : ""}origens com menos de 1.000 t` : "portos fora do ranking";
      itens.push({ chave: "resto", pos: "", nome, t: resto, share: r.t ? (100 * resto) / r.t : null, fixa: true, rotulo: `${nome}: ${falado(resto)} (${detalhe}).` });
    }
    atualizarLista(itens, Math.max(1, ...itens.map((x) => x.t)));
    $("fluxos-painel-nota").textContent = porPais
      ? "Variação sobre os mesmos meses do ano anterior; \"novo\" quando o país vendeu menos de 1.000 t naquele ano."
      : "URFs no mesmo lugar aparecem como um só porto.";
  }

  function preencherOrigem(ano) {
    const sel = estado.sel;
    const r = recorte();
    const o = r.origens.find((x) => x[0] === sel.co);
    $("fluxos-painel-kicker").textContent = `Origem · ${NOME_LONGO[estado.grupo]} · ${ano}`;
    titulo.textContent = nomePais(sel.co);
    $("fluxos-dica").textContent = "Clique num porto da lista para ver todas as origens dele. Esc volta.";
    const linha = (rotulo, id) => el("div", {}, el("dt", {}, rotulo), el("dd", { id }));
    const rotuloShare = estado.grupo === "TOTAL" ? "Share no total" : `Share dos ${DESCRICAO_GRUPO[estado.grupo]}`;
    if (numeros.dataset.tipo !== "origem") {
      numeros.replaceChildren(linha("Toneladas", "fx-t"), linha(rotuloShare, "fx-share"),
        linha("Frete", "fx-frete"), linha("Preço CIF", "fx-cif"), linha("Contra o ano anterior", "fx-var"));
      numeros.dataset.tipo = "origem";
    }
    $("fx-share").previousSibling.textContent = rotuloShare;
    contador("fx-t", o?.[1] ?? 0, formatoFixo(o?.[1] ?? 0));
    contador("fx-share", o?.[2] ?? null, pct);
    contador("fx-frete", o?.[4] ?? null, porTonelada);
    contador("fx-cif", o?.[5] ?? null, porTonelada);
    const v = variacao(o?.[3], o?.[1]);
    const dv = $("fx-var");
    dv.textContent = v.texto;
    dv.className = v.classe;
    const rotas = r.rotas.filter((x) => x[0] === sel.co);
    const somaRotas = rotas.reduce((s, x) => s + x[2], 0);
    $("fluxos-lista-titulo").textContent = rotas.length ? `Portos de chegada (rotas com ${textoCorte()})` : `Nenhuma rota com ${textoCorte()}`;
    cabecalhoLista("toneladas", "frete");
    const itens = rotas.map((x, k) => ({ chave: `p:${x[1]}`, pos: k + 1, nome: porto(x[1]).nome, t: x[2], extra: porTonelada(x[3]),
      rotulo: `${porto(x[1]).nome}: ${falado(x[2])}, frete ${porTonelada(x[3])}. Enter mostra as origens desse porto.`, acao: () => selecionarPorto(x[1]) }));
    const resto = (o?.[1] ?? 0) - somaRotas;
    if (resto > 0.5) itens.push({ chave: "resto", pos: "", nome: "Outras rotas", t: resto, fixa: true, rotulo: `Outras rotas: ${falado(resto)}.` });
    atualizarLista(itens, Math.max(1, ...itens.map((x) => x.t)));
    $("fluxos-painel-nota").textContent = o
      ? "Frete e CIF: valor ÷ toneladas no ano. A variação compara com os mesmos meses do ano anterior."
      : `Sem importação registrada de ${nomePais(sel.co)} em ${ano} neste grupo.`;
  }

  function preencherPorto(ano) {
    const sel = estado.sel;
    const r = recorte();
    const p = porto(sel.i);
    const tp = r.portos.find((x) => x[0] === sel.i)?.[1] ?? 0;
    $("fluxos-painel-kicker").textContent = `Porto · ${NOME_LONGO[estado.grupo]} · ${ano}`;
    titulo.textContent = `${p.nome} (${p.uf})`;
    $("fluxos-dica").textContent = "Clique num país da lista para ver todos os portos dele. Esc volta.";
    const linha = (rotulo, id) => el("div", {}, el("dt", {}, rotulo), el("dd", { id }));
    if (numeros.dataset.tipo !== "porto") {
      numeros.replaceChildren(linha("Toneladas recebidas", "fx-pt"), linha("Share no total importado", "fx-pshare"), linha("Origens acima do corte", "fx-pn"));
      numeros.dataset.tipo = "porto";
    }
    const rotas = r.rotas.filter((x) => x[1] === sel.i);
    contador("fx-pt", tp, formatoFixo(tp));
    contador("fx-pshare", r.t ? (100 * tp) / r.t : null, pct);
    contador("fx-pn", rotas.length, (v) => fmtInt.format(Math.round(v)));
    const somaRotas = rotas.reduce((s, x) => s + x[2], 0);
    $("fluxos-lista-titulo").textContent = rotas.length ? `Origens (rotas com ${textoCorte()})` : `Nenhuma rota com ${textoCorte()}`;
    cabecalhoLista("toneladas", "frete");
    const itens = rotas.map((x, k) => ({ chave: `o:${x[0]}`, pos: k + 1, nome: nomePais(x[0]), t: x[2], extra: porTonelada(x[3]),
      rotulo: `${nomePais(x[0])}: ${falado(x[2])}, frete ${porTonelada(x[3])}. Enter mostra os portos desse país.`, acao: () => selecionarOrigem(x[0]) }));
    if (tp - somaRotas > 0.5) itens.push({ chave: "resto", pos: "", nome: "Outras origens", t: tp - somaRotas, fixa: true, rotulo: `Outras origens: ${falado(tp - somaRotas)}.` });
    atualizarLista(itens, Math.max(1, ...itens.map((x) => x.t)));
    $("fluxos-painel-nota").textContent = `URF ${p.urfs.join(", ")}. URFs no mesmo lugar aparecem como um só porto.`;
  }

  // Lista com chave: as linhas que já existem trocam de lugar com animação (FLIP), as barras
  // crescem ou encolhem e as novas entram em sequência.
  function atualizarLista(itens, maior) {
    const antes = new Map();
    for (const li of lista.children) antes.set(li.dataset.chave, li.getBoundingClientRect().top);
    const existentes = new Map([...lista.children].map((li) => [li.dataset.chave, li]));
    const usados = new Set();
    const novos = [];
    for (const item of itens) {
      let li = existentes.get(item.chave);
      if (li && li.firstChild.classList.contains("fixa") !== !!item.fixa) { li.remove(); li = null; }
      if (!li) {
        const botao = el(item.fixa ? "div" : "button", item.fixa ? { classe: "linha fixa" } : { type: "button", classe: "linha" },
          el("span", { classe: "pos" }), el("span", { classe: "nome" }), el("span", { classe: "valor" }),
          el("span", { classe: "trilho" }, el("span")), el("span", { classe: "extra" }));
        li = el("li", {}, botao);
        li.dataset.chave = item.chave;
        li.querySelector(".trilho span").style.transform = "scaleX(0)";
        if (!item.fixa) {
          botao.dataset.chave = item.chave;
          botao.addEventListener("click", () => { pararPlay(); li.__acao?.(); });
          botao.addEventListener("pointerenter", () => destacar(item.chave));
          botao.addEventListener("pointerleave", () => destacar(null));
          botao.addEventListener("focus", () => destacar(item.chave));
          botao.addEventListener("blur", () => destacar(null));
        }
        novos.push(li);
      }
      li.__acao = item.acao;
      const linha = li.firstChild;
      if (item.rotulo) linha.setAttribute(item.fixa ? "title" : "aria-label", item.rotulo);
      linha.classList.toggle("destacado", !item.fixa && estado.destaque === item.chave);
      linha.querySelector(".pos").textContent = String(item.pos);
      linha.querySelector(".nome").textContent = item.nome;
      linha.querySelector(".valor").textContent = toneladas(item.t);
      const extra = linha.querySelector(".extra");
      extra.replaceChildren();
      if (item.extra) extra.append(item.extra);
      else {
        if (item.share != null) extra.append(pct(item.share));
        if (item.vari?.texto && item.vari.texto !== "–") extra.append(" ", el("span", { classe: item.vari.classe }, item.vari.texto));
      }
      const barra = linha.querySelector(".trilho span");
      const fracao = clamp(item.t / maior, 0.015, 1);
      if (semMovimento()) barra.style.transform = `scaleX(${fracao})`;
      else A.animate(barra, { scaleX: fracao, duration: 700, ease: "outExpo" });
      lista.append(li);
      usados.add(item.chave);
    }
    for (const [chave, li] of existentes) if (!usados.has(chave)) li.remove();
    if (estado.destaque && !marcadores.has(estado.destaque) && !lista.querySelector(`[data-chave="${estado.destaque}"]`)) destacar(null);
    if (semMovimento()) return;
    for (const li of lista.children) {
      const topo = antes.get(li.dataset.chave);
      if (topo == null) continue;
      const dy = topo - li.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) A.animate(li, { translateY: [dy, 0], duration: 650, ease: "outExpo" });
    }
    if (novos.length) A.animate(novos, { opacity: [0, 1], translateX: [-10, 0], duration: 500, delay: A.stagger(40), ease: "outQuad" });
  }

  function anunciar(texto) {
    if (tocando) return; // durante o play, só o ano final é anunciado
    anuncio.textContent = texto;
  }

  // --- Cartão de resumo -----------------------------------------------------------------------
  const total = { v: 0 };
  function contarTotal(valor, contar) {
    const alvo = $("fluxos-total");
    if (semMovimento() || !contar) { total.v = valor; alvo.textContent = milhoes(valor); return; }
    A.animate(total, { v: valor, duration: 900, ease: "outExpo", onUpdate: () => { alvo.textContent = milhoes(total.v); } });
  }
  function atualizarResumo({ contar = true, comTotal = true } = {}) {
    const r = recorte();
    const m = meses(estado.ano);
    $("fluxos-resumo-kicker").textContent = `${NOME_LONGO[estado.grupo]} · ${rotuloAno(estado.ano)}`;
    if (comTotal) contarTotal(r.t, contar);
    const umMilhao = Math.round(r.t / 1e5) / 10 < 2;
    $("fluxos-unidade").textContent = `${umMilhao ? "milhão" : "milhões"} de t importadas${m && m < 12 ? ` em jan–${MESES[m - 1]}` : ""}`;
    const top = r.origens.slice(0, 3);
    const soma = r.t ? (100 * top.reduce((s, o) => s + o[1], 0)) / r.t : 0;
    $("fluxos-resumo-texto").textContent = top.length
      ? `${listaNomes(top.map((o) => nomePais(o[0])))} ${top.length === 1 ? "responde por" : "somam"} ${fmt0.format(soma)}% do total.`
      : "Sem importação registrada neste ano.";
  }

  // --- Controles: grupo, ano, play e pausa -------------------------------------------------------
  function montarGrupos() {
    const indicador = el("span", { classe: "indicador", "aria-hidden": "true" });
    caixaGrupo.replaceChildren(indicador, ...ORDEM_GRUPOS.map((g) => {
      const b = el("button", { type: "button", "data-grupo": g, "aria-pressed": "false" }, NOMES_GRUPO[g]);
      b.addEventListener("click", () => {
        pararPlay();
        if (window.radarDefinirGrupo) window.radarDefinirGrupo(g); // mesmo estado e mesma URL dos filtros da página
        else definirGrupo(g);
      });
      return b;
    }));
    for (const b of abas.querySelectorAll("button")) b.addEventListener("click", () => definirVisao(b.dataset.visao));
  }

  function marcarSegmentado(caixa, atributo, valor, instantaneo) {
    const indicador = caixa.querySelector(".indicador");
    for (const b of caixa.querySelectorAll("button")) {
      const ativo = b.dataset[atributo] === valor;
      b.setAttribute("aria-pressed", String(ativo));
      if (ativo && indicador && b.offsetWidth) {
        if (instantaneo) indicador.style.transition = "none";
        indicador.style.width = `${b.offsetWidth}px`;
        indicador.style.transform = `translateX(${b.offsetLeft - indicador.offsetLeft}px)`;
        if (instantaneo) { void indicador.offsetWidth; indicador.style.transition = ""; }
      }
    }
  }

  let grupoPendente = 0;
  function definirGrupo(g) {
    if (!ORDEM_GRUPOS.includes(g) || g === estado.grupo) { marcarSegmentado(caixaGrupo, "grupo", estado.grupo, true); return; }
    estado.grupo = g;
    app.dataset.grupo = g;
    lerCores();
    marcarSegmentado(caixaGrupo, "grupo", g, false);
    if (!iniciado) {
      if (bloco) { atualizarResumo({ comTotal: false }); atualizarColunas(false); }
      return;
    }
    // a página redesenha todos os gráficos na mesma tarefa; as animações do mapa começam depois
    cancelAnimationFrame(grupoPendente);
    grupoPendente = requestAnimationFrame(() => setTimeout(() => {
      mudouRecorte();
      atualizarColunas(true);
    }, 0));
  }

  function montarAnos() {
    faixa.max = String(bloco.anos.length - 1);
    marcas.style.setProperty("--n", String(bloco.anos.length));
    faixa.parentElement.style.setProperty("--n", String(bloco.anos.length));
    marcas.replaceChildren(...bloco.anos.map((a) => {
      const li = el("li", { title: rotuloAno(a) }, el("span", { classe: "coluna" }, el("span")), el("span", { classe: "ano" }, `'${String(a).slice(2)}`));
      li.dataset.ano = String(a);
      li.classList.toggle("parcial", (meses(a) ?? 12) < 12);
      li.addEventListener("click", () => { pararPlay(); definirAno(a); });
      return li;
    }));
    faixa.addEventListener("input", () => {
      pararPlay();
      definirAno(bloco.anos[Number(faixa.value)]);
    });
  }

  function atualizarColunas(animado) {
    const colunas = [...marcas.querySelectorAll(".coluna span")];
    const alturas = bloco.anos.map((a) => clamp((bloco.dados[String(a)]?.[estado.grupo]?.t ?? 0) / ref.ano[estado.grupo], 0.03, 1));
    if (animado && !semMovimento()) A.animate(colunas, { scaleY: (_, i) => alturas[i], duration: 700, delay: A.stagger(30), ease: "outExpo" });
    else colunas.forEach((c, i) => { c.style.transform = `scaleY(${alturas[i]})`; });
    for (const li of marcas.children) li.classList.toggle("atual", li.dataset.ano === String(estado.ano));
  }

  function definirAno(ano) {
    if (ano === estado.ano) return;
    estado.ano = ano;
    faixa.value = String(bloco.anos.indexOf(ano));
    const m = meses(ano);
    saidaAno.replaceChildren(String(ano), m && m < 12 ? el("small", {}, `jan–${MESES[m - 1]}`) : "");
    faixa.setAttribute("aria-valuetext", rotuloAno(ano));
    for (const li of marcas.children) li.classList.toggle("atual", li.dataset.ano === String(ano));
    if (iniciado) mudouRecorte();
  }

  function mudouRecorte() {
    gravarUrl();
    atualizarIntensidades();
    atualizarResumo();
    preencherPainel();
    sincronizarMarcadores();
    sincronizarArcos();
    atualizarLegenda();
    // com uma seleção aberta, a câmera só se move se algum ponto novo cair fora do quadro
    if (estado.sel && !cameraAnimando && algumForaDaTela(alvosDaSelecao())) moverCamera(cameraDoEstado());
    else if (!estado.sel && estado.visao === "portos" && !cameraAnimando) moverCamera(cameraDoEstado());
    else if (!cameraAnimando) soltarNavios();
    tabela();
    if (!estado.sel) anunciar(`${NOME_LONGO[estado.grupo]}, ${rotuloAno(estado.ano)}: ${falado(recorte().t)} importadas.`);
  }

  let tocando = null;
  function rotularPlay(tocandoAgora) {
    botaoPlay.querySelector("span:last-child").textContent = tocandoAgora ? "Pausar" : "Reproduzir";
    botaoPlay.querySelector(".icone path").setAttribute("d", tocandoAgora ? "M5 3h2v10H5zM9 3h2v10H9z" : "M5 3v10l8-5z");
  }
  function tocar() {
    if (tocando) { pararPlay(); return; }
    // de 2015 até o último ano completo; o ano parcial fica para o clique
    const fim = Math.max(0, bloco.anos.indexOf(anoPadrao));
    let i = bloco.anos.indexOf(estado.ano);
    if (i >= fim) i = -1;
    rotularPlay(true);
    const passo = () => {
      i++;
      definirAno(bloco.anos[i]);
      if (i >= fim) pararPlay();
    };
    tocando = setInterval(passo, semMovimento() ? 1200 : 1800);
    passo();
  }
  function pararPlay() {
    if (!tocando) return;
    clearInterval(tocando);
    tocando = null;
    rotularPlay(false);
    if (iniciado && !estado.sel) anunciar(`${NOME_LONGO[estado.grupo]}, ${rotuloAno(estado.ano)}: ${falado(recorte().t)} importadas.`);
  }

  // "Pausar animação": para cometas e pulsos (a linha do tempo tem o próprio botão)
  function alternarPausa() {
    pausado = !pausado;
    botaoPausa.setAttribute("aria-pressed", String(pausado));
    botaoPausa.querySelector("span:last-child").textContent = pausado ? "Retomar animação" : "Pausar animação";
    palco.classList.toggle("pausado", pausado);
    if (pausado) { pulso?.pause(); pararNavios(); }
    else { iniciarPulso(); soltarNavios(); }
  }

  // --- URL (no mesmo hash dos filtros) -------------------------------------------------------
  function gravarUrl() {
    const q = new URLSearchParams(location.hash.slice(1));
    q.set("ano", String(estado.ano));
    q.delete("origem");
    q.delete("porto");
    if (estado.sel?.tipo === "origem") q.set("origem", estado.sel.co);
    if (estado.sel?.tipo === "porto") q.set("porto", slug(porto(estado.sel.i).nome));
    history.replaceState(null, "", `#${q}`);
  }

  function lerUrl() {
    const q = new URLSearchParams(location.hash.slice(1));
    const ano = Number(q.get("ano"));
    const origem = q.get("origem");
    const p = q.get("porto");
    return {
      ano: bloco.anos.includes(ano) ? ano : null,
      origem: origem && bloco.paises[origem] ? origem : null,
      porto: p ? bloco.portos.findIndex((x) => slug(x[0]) === p) : -1,
    };
  }

  // --- Tabela alternativa -----------------------------------------------------------------------
  function tabela() {
    const r = recorte();
    const cab = ["País de origem", "Toneladas", "Share %", "Var. % a/a", "Frete US$/t", "CIF US$/t"];
    const fmtN = (v, f) => (v == null ? "–" : f.format(v));
    const fmtVar = (o) => (variacao(o[3], o[1]).texto === "novo" ? "novo" : fmtN(o[3], fmt1));
    const t = el("table", {}, el("caption", { classe: "leitor" }, `Origens em ${rotuloAno(estado.ano)}, ${DESCRICAO_GRUPO[estado.grupo]}`),
      el("thead", {}, el("tr", {}, ...cab.map((c, i) => el("th", { classe: i ? "num" : null, scope: "col" }, c)))),
      el("tbody", {}, ...r.origens.map((o) => el("tr", {}, el("td", {}, nomePais(o[0])), el("td", { classe: "num" }, fmtN(o[1], fmtInt)),
        el("td", { classe: "num" }, fmtN(o[2], fmt1)), el("td", { classe: "num" }, fmtVar(o)), el("td", { classe: "num" }, fmtN(o[4], fmt0)),
        el("td", { classe: "num" }, fmtN(o[5], fmt0)))), el("tr", {}, el("td", {}, "Outros (menos de 1.000 t ou sem coordenada)"), el("td", { classe: "num" }, fmtN(r.outros_t, fmtInt)),
        el("td", {}), el("td", {}), el("td", {}), el("td", {}))));
    const rotas = el("table", {}, el("caption", { classe: "leitor" }, `Rotas com ${textoCorte()}`),
      el("thead", {}, el("tr", {}, ...["Origem", "Porto", "Toneladas", "Frete US$/t"].map((c, i) => el("th", { classe: i > 1 ? "num" : null, scope: "col" }, c)))),
      el("tbody", {}, ...r.rotas.map((x) => el("tr", {}, el("td", {}, nomePais(x[0])), el("td", {}, porto(x[1]).nome), el("td", { classe: "num" }, fmtN(x[2], fmtInt)), el("td", { classe: "num" }, fmtN(x[3], fmt0))))));
    $("tabela-fluxos").replaceChildren(t, el("h4", { classe: "subtitulo" }, `Rotas com ${textoCorte()}`), rotas);
  }

  // --- Cores e tamanho ----------------------------------------------------------------------------
  function lerCores() {
    cores = { ponto: variavel("--fluxo-ponto") || "#8e97ff", fluxo: variavel("--fluxo-cor") || "#b3a8ff" };
    pedirQuadro();
  }

  // Redimensionar: um ajuste por quadro, sem refazer a seleção (nem onda, nem anúncio)
  let redimPedido = 0;
  function redimensionar() {
    if (!pontos || redimPedido) return;
    redimPedido = requestAnimationFrame(() => {
      redimPedido = 0;
      animCamera?.pause();
      cameraAnimando = false;
      medir();
      Object.assign(cam, cameraDoEstado());
      reposicionarArcos();
      if (iniciado) {
        sincronizarMarcadores();
        sincronizarArcos({ desenharNovos: false });
        atualizarLegenda();
        if (!estado.sel) preencherPainel();
      }
      marcarSegmentado(caixaGrupo, "grupo", estado.grupo, true);
      marcarSegmentado(abas, "visao", estado.visao, true);
      pararNavios();
      soltarNavios();
      pedirQuadro();
    });
  }

  // --- Entrada ------------------------------------------------------------------------------------
  function entrar() {
    iniciado = true;
    carregando.remove();
    atualizarIntensidades(true);
    preencherPainel();
    tabela();
    atualizarColunas(!semMovimento());
    atualizarLegenda();
    marcarSegmentado(abas, "visao", estado.visao, true);
    const depois = () => {
      linhaEntrada = null;
      const url = lerUrl();
      iniciarPulso();
      if (url.origem) selecionarOrigem(url.origem);
      else if (url.porto >= 0) selecionarPorto(url.porto);
      else soltarNavios();
    };
    if (semMovimento()) {
      anim.entrada = 1;
      anim.brasil = 1;
      anim.hub = 1;
      atualizarResumo({ contar: false });
      sincronizarMarcadores();
      sincronizarArcos({ desenharNovos: false });
      pedirQuadro();
      depois();
      return;
    }
    // os pontos saem de um ruído disperso e se assentam em onda a partir do Brasil; o Brasil
    // acende, o total conta, os arcos chegam um a um (dos maiores aos menores) e os países
    // crescem no lugar
    linhaEntrada = A.createTimeline({ defaults: { ease: "linear" } });
    linhaEntrada.add(anim, { entrada: [0, 1], duration: 2200, onUpdate: () => { posicionarMarcadores(); pedirQuadro(); } }, 0)
      .add(anim, { brasil: [0, 1], hub: [0, 1], duration: 600, ease: "outQuad", onUpdate: () => { posicionarHub(); pedirQuadro(); } }, 1700)
      .call(() => { atualizarResumo(); sincronizarMarcadores({ entrada: true }); sincronizarArcos({ atraso: 0 }); }, 1900)
      .call(depois, 3200);
  }

  function talvezIniciar() {
    if (iniciado || !bloco || !pontos || !visivel) return;
    entrar();
  }

  // --- Ligação com a página -------------------------------------------------------------------------
  function receberDados(d) {
    if (bloco || !d?.mapa) {
      if (!d?.mapa) carregando.textContent = "Mapa de fluxos indisponível nestes dados.";
      return;
    }
    bloco = d.mapa;
    calcularReferencias();
    const url = lerUrl();
    anoPadrao = bloco.anos[bloco.anos.length - 1];
    for (let i = bloco.anos.length - 1; i >= 0; i--) if (bloco.n_meses[i] === 12) { anoPadrao = bloco.anos[i]; break; }
    montarGrupos();
    montarAnos();
    definirAno(url.ano ?? anoPadrao);
    marcarSegmentado(caixaGrupo, "grupo", estado.grupo, true);
    atualizarResumo({ comTotal: false }); // o resumo já aparece; o total conta na entrada
    preparar();
  }

  async function carregarGrade() {
    try {
      const resposta = await fetch("assets/mapa-pontos.json");
      if (resposta.ok) grade = await resposta.json();
    } catch {
      /* sem a base, a seção mostra o aviso */
    }
    if (!grade) carregando.textContent = "Não foi possível carregar a base do mapa.";
    preparar();
  }

  function preparar() {
    if (!bloco || !grade || pontos) return;
    lerCores();
    prepararPontos();
    medir();
    Object.assign(cam, geral);
    atualizarColunas(false);
    pedirQuadro();
    talvezIniciar();
  }

  document.addEventListener("radar:dados", (e) => receberDados(e.detail));
  if (window.radarDados) receberDados(window.radarDados);
  document.addEventListener("radar:filtros", (e) => definirGrupo(e.detail.grupo));
  botaoVoltar.addEventListener("click", voltar);
  botaoPlay.addEventListener("click", tocar);
  botaoPausa.addEventListener("click", alternarPausa);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && estado.sel) {
      pararPlay();
      voltar();
    }
  });
  palco.addEventListener("pointerleave", () => destacar(null));
  new IntersectionObserver((entradas) => {
    visivel = entradas[0].isIntersecting;
    palco.classList.toggle("parado", !visivel);
    if (visivel) {
      if (!pausado) pulso?.play();
      if (iniciado && !cameraAnimando) soltarNavios();
      talvezIniciar();
    } else {
      pulso?.pause();
      pararNavios();
      pararPlay();
    }
  }, { threshold: 0.2 }).observe(palco);
  new ResizeObserver(() => redimensionar()).observe(palco);
  const recolorir = () => lerCores();
  new MutationObserver(recolorir).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", recolorir);
  reduzido.addEventListener?.("change", () => {
    if (semMovimento()) {
      linhaEntrada?.complete(); // termina a entrada (e chama o que vem depois dela)
      pulso?.pause();
      pulso = null;
      pararNavios();
      anim.entrada = 1;
      anim.brasil = 1;
      anim.hub = iniciado && !estado.sel && estado.visao === "origens" ? 1 : 0;
      posicionarMarcadores();
      pedirQuadro();
    } else {
      iniciarPulso();
      soltarNavios();
    }
  });
  carregarGrade();
})();
