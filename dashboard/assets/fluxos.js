/* Mapa de fluxos: de que país vem o adubo e por qual porto ele entra, ano a ano.
 *
 * - Pontos (assets/mapa-pontos.json, Natural Earth 1:110m) desenhados num <canvas>; por cima,
 *   um <svg> com arcos, navios, marcadores focáveis e rótulos.
 * - Dados só do bloco "mapa" do dashboard.json (gerado pelo transform a partir de
 *   share_origem_ano, share_urf_ano, rotas_ano e precos_mes_grupo_origem). Rota abaixo do
 *   corte não ganha arco: fica em "outras".
 * - Animações com anime.js v4 (animate, createTimeline, stagger, svg.createDrawable,
 *   svg.createMotionPath, utils, cubicBezier): ele anima objetos JS (câmera, entrada, pulso,
 *   onda) e o canvas é redesenhado a cada quadro em que algo mudou.
 * - prefers-reduced-motion (ou sem anime.js): sem câmera nem entrada animadas, sem navios,
 *   só transições curtas de opacidade.
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
  const lerp = (a, b, t) => a + (b - a) * t;
  const suave = (t) => 1 - (1 - t) ** 3; // ease-out cúbico, por ponto
  const fmtInt = new Intl.NumberFormat("pt-BR");
  const fmt0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const fmt1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtCompacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  const MENOS = "−";
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const NOMES_GRUPO = { TOTAL: "Todos", N: "N", P: "P", K: "K", NPK: "NPK" };
  const DESCRICAO_GRUPO = { TOTAL: "todos os grupos", N: "nitrogenados", P: "fosfatados", K: "potássicos", NPK: "compostos (NPK)" };
  const ORDEM_GRUPOS = ["TOTAL", "N", "P", "K", "NPK"];
  const variavel = (nome) => getComputedStyle(app).getPropertyValue(nome).trim();
  const toneladas = (t) => (t == null ? "–" : `${fmtCompacto.format(t)} t`);
  const porTonelada = (v) => (v == null ? "–" : `US$ ${fmt0.format(v)}/t`);
  const pct = (v) => (v == null ? "–" : `${fmt1.format(v)}%`);
  function variacao(v) {
    if (v == null || !Number.isFinite(v)) return { texto: "–", classe: "" };
    const t = fmt1.format(Math.abs(v));
    if (!/[1-9]/.test(t)) return { texto: `■ ${t}%`, classe: "" };
    return v > 0 ? { texto: `▲ +${t}%`, classe: "ganho" } : { texto: `▼ ${MENOS}${t}%`, classe: "perda" };
  }
  const slug = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  function el(tag, attrs = {}, ...filhos) {
    const svgTags = new Set(["g", "path", "circle", "rect", "text", "title"]);
    const e = svgTags.has(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "classe") e.setAttribute("class", v);
      else e.setAttribute(k, v);
    }
    for (const f of filhos) if (f != null) e.append(f);
    return e;
  }

  // Animação de um objeto JS (ou elementos) com anime.js; sem movimento, aplica o fim na hora.
  function animar(alvo, parametros) {
    if (semMovimento()) {
      const { onUpdate, onComplete, duration, delay, ease, loop, ...valores } = parametros;
      for (const alvoUnico of Array.isArray(alvo) ? alvo : [alvo]) {
        for (const [k, v] of Object.entries(valores)) {
          if (typeof alvoUnico === "object" && !(alvoUnico instanceof Element)) alvoUnico[k] = Array.isArray(v) ? v[v.length - 1] : v;
        }
      }
      onUpdate?.();
      onComplete?.();
      return null;
    }
    return A.animate(alvo, parametros);
  }

  // --- Elementos ---------------------------------------------------------------------------
  const palco = document.getElementById("fluxos-palco");
  const canvas = document.getElementById("fluxos-canvas");
  const ctx = canvas.getContext("2d");
  const svg = document.getElementById("fluxos-svg");
  const chipBrasil = document.getElementById("fluxos-chip-brasil");
  const carregando = document.getElementById("fluxos-carregando");
  const painel = document.getElementById("fluxos-painel");
  const botaoVoltar = document.getElementById("fluxos-voltar");
  const botaoPlay = document.getElementById("fluxos-play");
  const faixa = document.getElementById("fluxos-ano");
  const saidaAno = document.getElementById("fluxos-ano-valor");
  const marcas = document.getElementById("fluxos-marcas");
  const caixaGrupo = document.getElementById("fluxos-grupo");
  const camadaArcos = el("g", { classe: "camada-arcos" });
  const camadaNavios = el("g", { classe: "camada-navios", "aria-hidden": "true" });
  const camadaMarcadores = el("g", { classe: "camada-marcadores" });
  svg.append(camadaArcos, camadaNavios, camadaMarcadores);

  // --- Estado ------------------------------------------------------------------------------
  let bloco = null; // dashboard.json -> mapa
  let grade = null; // assets/mapa-pontos.json
  let pontos = null; // arrays por ponto
  const estado = { ano: null, grupo: "TOTAL", sel: null }; // sel: {tipo: "origem", co} | {tipo: "porto", i}
  const cam = { x: 0, y: 0, z: 1 }; // centro (em células da grade) e pixels por célula
  let geral = { x: 0, y: 0, z: 1 };
  let W = 0, H = 0, dpr = 1;
  const anim = { entrada: 0, brasil: 0, fase: 0, onda: 1 };
  let iniciado = false, visivel = false, quadroPedido = 0, cameraAnimando = false;
  const intensidade = new Map(); // ISO3 -> {v}: brilho do país (0 a 1), interpolado
  const marcadores = new Map(); // chave -> {g, tipo, dados}
  const arcos = new Map(); // chave -> {g, path, w, o, dados}
  let navios = []; // animações dos navios
  let pulso = null; // animação do pulso
  let cores = {};
  let ref = { rota: {}, origem: {} };

  // --- Geometria ---------------------------------------------------------------------------
  const gradeDe = (lon, lat) => ({ gx: (lon - grade.oeste) / grade.passo, gy: (grade.norte - lat) / grade.passo });
  const tela = (gx, gy) => ({ x: (gx - cam.x) * cam.z + W / 2, y: (gy - cam.y) * cam.z + H / 2 });

  function enquadrar(caixa, folga = 0.14, comPainel = true) {
    // caixa em células: {x0, y0, x1, y1}; devolve a câmera que mostra tudo com folga. Com o
    // painel aberto (desktop), o enquadramento fica na parte do mapa à esquerda dele.
    const bw = Math.max(caixa.x1 - caixa.x0, 4), bh = Math.max(caixa.y1 - caixa.y0, 4);
    const larguraUtil = comPainel && W > 820 ? W - Math.min(330, W * 0.38) - 24 : W;
    const minimo = comPainel ? geral.z : 0.01;
    const z = clamp(Math.min(larguraUtil / (bw * (1 + folga * 2)), H / (bh * (1 + folga * 2))), minimo, Math.max(minimo, geral.z * 7));
    const deslocamento = (W - larguraUtil) / 2 / z; // abre espaço para o painel à direita
    return { x: (caixa.x0 + caixa.x1) / 2 + deslocamento, y: (caixa.y0 + caixa.y1) / 2, z };
  }

  function medir() {
    const r = palco.getBoundingClientRect();
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    // visão geral: das Américas ao leste da Ásia; no celular, um recorte mais justo
    const estreito = W < 600;
    const a = gradeDe(estreito ? -105 : -135, 74), b = gradeDe(estreito ? 125 : 155, -52);
    geral = enquadrar({ x0: a.gx, y0: a.gy, x1: b.gx, y1: b.gy }, 0.01, false);
    geral.x = (a.gx + b.gx) / 2;
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

  function desenhar() {
    quadroPedido = 0;
    if (!pontos) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const lado = clamp(cam.z * 0.56, 1.1, 7.5), meio = lado / 2;
    const e = anim.entrada, fase = anim.fase * Math.PI * 2;
    const brilho = new Float32Array(grade.paises.length);
    grade.paises.forEach((iso, k) => { brilho[k] = intensidade.get(iso)?.v ?? 0; });
    const selIso = estado.sel?.tipo === "origem" ? bloco.paises[estado.sel.co]?.[1] : null;
    const selIdx = selIso ? grade.paises.indexOf(selIso) : -1;
    ctx.fillStyle = cores.ponto;
    const cx = W / 2 - cam.x * cam.z, cy = H / 2 - cam.y * cam.z;
    for (let j = 0; j < pontos.n; j++) {
      let gx = pontos.gx[j], gy = pontos.gy[j], a = 0.2;
      if (e < 1) {
        const k = suave(clamp((e - pontos.d0[j] * 0.6) / 0.4, 0, 1));
        gx += pontos.nx[j] * (1 - k);
        gy += pontos.ny[j] * (1 - k);
        a *= k;
      }
      const x = gx * cam.z + cx, y = gy * cam.z + cy;
      if (x < -lado || y < -lado || x > W + lado || y > H + lado) continue;
      const p = pontos.pais[j];
      const b = brilho[p];
      if (b > 0) a += b * (0.22 + 0.3 * (0.5 + 0.5 * Math.sin(fase - pontos.dc[j] * 3)));
      if (p === selIdx) a = Math.max(a, 0.25 + 0.7 * suave(clamp((anim.onda - pontos.dc[j] * 0.75) / 0.25, 0, 1)));
      if (p === pontos.brasil) a = Math.max(a, anim.brasil * 0.95);
      if (a <= 0.01) continue;
      ctx.globalAlpha = a > 1 ? 1 : a;
      ctx.fillRect(x - meio, y - meio, lado, lado);
    }
    ctx.globalAlpha = 1;
  }

  // --- Dados do ano e grupo ----------------------------------------------------------------
  const vazio = { t: 0, origens: [], outros_t: 0, rotas: [], portos: [] };
  const recorte = (ano = estado.ano, grupo = estado.grupo) => bloco.dados[String(ano)]?.[grupo] ?? vazio;
  const nomePais = (co) => bloco.paises[co]?.[0] ?? co;
  const porto = (i) => ({ nome: bloco.portos[i][0], uf: bloco.portos[i][1], lon: bloco.portos[i][2], lat: bloco.portos[i][3], urfs: bloco.portos[i][4] });
  function rotuloAno(ano) {
    const i = bloco.anos.indexOf(Number(ano));
    const meses = bloco.n_meses[i];
    return meses && meses < 12 ? `${ano} (jan–${MESES[meses - 1]})` : String(ano);
  }
  function calcularReferencias() {
    for (const g of ORDEM_GRUPOS) {
      let rota = 1, origem = 1;
      for (const a of bloco.anos) {
        const r = bloco.dados[String(a)]?.[g];
        if (!r) continue;
        for (const x of r.rotas) rota = Math.max(rota, x[2]);
        for (const x of r.origens) origem = Math.max(origem, x[1]);
      }
      ref.rota[g] = rota;
      ref.origem[g] = origem;
    }
  }

  // --- Intensidade (pulso) dos países de origem ---------------------------------------------
  function atualizarIntensidades(instantaneo = false) {
    const r = recorte();
    const alvo = new Map();
    for (const o of r.origens) {
      const iso = bloco.paises[o[0]]?.[1];
      if (iso) alvo.set(iso, Math.sqrt(o[1] / ref.origem[estado.grupo]));
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
    if (semMovimento() || pulso) return;
    pulso = A.animate(anim, { fase: [0, 1], duration: 2600, ease: "linear", loop: true, onUpdate: pedirQuadro });
    if (!visivel) pulso.pause();
  }

  // --- Marcadores (origens e portos, focáveis) ----------------------------------------------
  function listaMarcadores() {
    const r = recorte();
    const itens = [];
    const comRota = new Set(r.rotas.map((x) => x[0]));
    r.origens.slice(0, 16).forEach((o, i) => {
      if (i < 10 || comRota.has(o[0]) || estado.sel?.co === o[0]) itens.push({ chave: `o:${o[0]}`, tipo: "origem", co: o[0], t: o[1], share: o[2], ordem: i });
    });
    r.portos.slice(0, 14).forEach((p, i) => itens.push({ chave: `p:${p[0]}`, tipo: "porto", i: p[0], t: p[1], ordem: i }));
    return itens;
  }

  function criarMarcador(item) {
    const g = el("g", { classe: `marcador ${item.tipo}`, tabindex: "0", role: "button" });
    if (item.tipo === "origem") {
      g.append(el("circle", { classe: "anel", r: "6" }), el("circle", { classe: "nucleo", r: "3" }));
    } else {
      g.append(el("rect", { classe: "nucleo", x: "-3.5", y: "-3.5", width: "7", height: "7" }));
    }
    g.append(el("circle", { classe: "foco", r: "11" }), el("circle", { classe: "alvo", r: "16" }), el("text", { classe: "rotulo", x: "10", y: "4" }));
    const selecionar = () => (item.tipo === "origem" ? selecionarOrigem(g.__item.co) : selecionarPorto(g.__item.i));
    g.addEventListener("click", () => { pararPlay(); selecionar(); });
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        pararPlay();
        selecionar();
      }
    });
    return g;
  }

  function sincronizarMarcadores() {
    const itens = listaMarcadores();
    const chaves = new Set(itens.map((x) => x.chave));
    for (const [chave, m] of marcadores) {
      if (!chaves.has(chave)) {
        m.g.remove();
        marcadores.delete(chave);
      }
    }
    const anoTxt = rotuloAno(estado.ano);
    for (const item of itens) {
      let m = marcadores.get(item.chave);
      if (!m) {
        m = { g: criarMarcador(item) };
        marcadores.set(item.chave, m);
        camadaMarcadores.append(m.g);
      }
      m.g.__item = item;
      m.dados = item;
      if (item.tipo === "origem") {
        const nome = nomePais(item.co);
        const raio = 2.5 + 5.5 * Math.sqrt(item.t / ref.origem[estado.grupo]);
        m.g.querySelector(".nucleo").setAttribute("r", raio.toFixed(1));
        m.g.querySelector(".anel").setAttribute("r", (raio + 2).toFixed(1));
        m.g.querySelector(".anel").style.animationDuration = `${(3.4 - 1.6 * Math.sqrt(item.t / ref.origem[estado.grupo])).toFixed(2)}s`;
        m.g.querySelector(".rotulo").textContent = nome;
        m.g.setAttribute("aria-label", `${nome}: ${toneladas(item.t)} em ${anoTxt}, ${pct(item.share)} de ${DESCRICAO_GRUPO[estado.grupo]}. Enter mostra as rotas até os portos.`);
      } else {
        const p = porto(item.i);
        m.g.querySelector(".rotulo").textContent = p.nome;
        m.g.setAttribute("aria-label", `Porto ${p.nome} (${p.uf}): ${toneladas(item.t)} recebidas em ${anoTxt}. Enter mostra as origens.`);
      }
      const ativo = (estado.sel?.tipo === item.tipo) && (item.tipo === "origem" ? estado.sel.co === item.co : estado.sel.i === item.i);
      m.g.classList.toggle("ativo", ativo);
    }
    // ordem de tabulação: origens por toneladas, depois os portos (sem perder o foco atual)
    const focado = camadaMarcadores.contains(document.activeElement) ? document.activeElement : null;
    [...marcadores.values()].sort((a, b) => (a.dados.tipo === b.dados.tipo ? a.dados.ordem - b.dados.ordem : a.dados.tipo === "origem" ? -1 : 1))
      .forEach((m) => camadaMarcadores.append(m.g));
    if (focado?.isConnected) focado.focus({ preventScroll: true });
    posicionarMarcadores();
  }

  function posicionarMarcadores() {
    const sel = estado.sel;
    const rotulosVisiveis = new Set();
    const r = recorte();
    if (!sel) r.origens.slice(0, W < 600 ? 4 : 7).forEach((o) => rotulosVisiveis.add(`o:${o[0]}`));
    if (!sel) r.portos.slice(0, W < 600 ? 2 : 4).forEach((p) => rotulosVisiveis.add(`p:${p[0]}`));
    if (sel?.tipo === "origem") {
      rotulosVisiveis.add(`o:${sel.co}`);
      r.rotas.filter((x) => x[0] === sel.co).slice(0, 6).forEach((x) => rotulosVisiveis.add(`p:${x[1]}`));
    }
    if (sel?.tipo === "porto") {
      rotulosVisiveis.add(`p:${sel.i}`);
      r.rotas.filter((x) => x[1] === sel.i).slice(0, 6).forEach((x) => rotulosVisiveis.add(`o:${x[0]}`));
    }
    const ocupados = []; // caixas dos rótulos já colocados (sem sobreposição)
    const ordem = [...marcadores.entries()].sort((a, b) => (b[1].g.classList.contains("ativo") - a[1].g.classList.contains("ativo")) || b[1].dados.t - a[1].dados.t);
    for (const [chave, m] of ordem) {
      const d = m.dados;
      const pos = d.tipo === "origem" ? (() => { const v = bloco.paises[d.co]; return gradeDe(v[2], v[3]); })() : (() => { const p = porto(d.i); return gradeDe(p.lon, p.lat); })();
      const s = tela(pos.gx, pos.gy);
      m.g.setAttribute("transform", `translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})`);
      const esmaecido = sel && !rotulosVisiveis.has(chave) && !m.g.classList.contains("ativo");
      m.g.style.opacity = anim.entrada < 1 ? String(clamp((anim.entrada - 0.55) / 0.45, 0, 1)) : esmaecido ? "0.45" : "1";
      const rotulo = m.g.querySelector(".rotulo");
      // rótulo à esquerda quando o ponto está perto da borda direita
      const aEsquerda = s.x > W - 140;
      rotulo.setAttribute("text-anchor", aEsquerda ? "end" : "start");
      rotulo.setAttribute("x", aEsquerda ? "-10" : "10");
      let mostrar = rotulosVisiveis.has(chave) || m.g.matches(":focus-visible");
      if (mostrar) {
        const largura = rotulo.textContent.length * 6.7 + 4;
        const caixa = { x0: aEsquerda ? s.x - 10 - largura : s.x + 6, x1: aEsquerda ? s.x - 6 : s.x + 10 + largura, y0: s.y - 7, y1: s.y + 9 };
        if (!m.g.matches(":focus-visible") && !m.g.classList.contains("ativo") && ocupados.some((o) => caixa.x0 < o.x1 && caixa.x1 > o.x0 && caixa.y0 < o.y1 && caixa.y1 > o.y0)) mostrar = false;
        else ocupados.push(caixa);
      }
      rotulo.style.display = mostrar ? "" : "none";
    }
    posicionarChip();
  }

  function posicionarChip() {
    const br = centroIso("BRA");
    if (!br || anim.brasil < 0.5) return;
    const s = tela(br.gx, br.gy);
    // no celular o chip fica no canto de baixo do mapa; no desktop, à esquerda do Brasil
    const fixo = W < 600;
    chipBrasil.classList.toggle("fixo", fixo);
    chipBrasil.style.left = fixo ? "10px" : `${Math.round(s.x)}px`;
    chipBrasil.style.top = fixo ? `${H - 10}px` : `${Math.round(s.y)}px`;
    chipBrasil.hidden = false;
    chipBrasil.style.opacity = estado.sel ? "0" : "1";
  }

  function atualizarChip() {
    const r = recorte();
    chipBrasil.replaceChildren(el("strong", {}, "Brasil"), ` · ${fmt1.format(r.t / 1e6)} Mt importadas em ${rotuloAno(estado.ano)}`);
  }

  // --- Arcos e navios ------------------------------------------------------------------------
  function listaArcos() {
    const r = recorte();
    const sel = estado.sel;
    let rotas;
    if (sel?.tipo === "origem") rotas = r.rotas.filter((x) => x[0] === sel.co);
    else if (sel?.tipo === "porto") rotas = r.rotas.filter((x) => x[1] === sel.i);
    else rotas = r.rotas.slice(0, W < 600 ? 8 : 14);
    return rotas.map((x) => ({ chave: `${x[0]}|${x[1]}`, co: x[0], i: x[1], t: x[2], frete: x[3] }));
  }

  function geometria(arco) {
    const v = bloco.paises[arco.co], p = porto(arco.i);
    const a = gradeDe(v[2], v[3]), b = gradeDe(p.lon, p.lat);
    const s = tela(a.gx, a.gy), f = tela(b.gx, b.gy);
    const dx = f.x - s.x, dy = f.y - s.y, d = Math.hypot(dx, dy) || 1;
    let nx = -dy / d, ny = dx / d;
    if (ny > 0) { nx = -nx; ny = -ny; } // curva para cima
    const c = { x: (s.x + f.x) / 2 + nx * d * 0.22, y: (s.y + f.y) / 2 + ny * d * 0.22 };
    return `M${s.x.toFixed(1)} ${s.y.toFixed(1)} Q${c.x.toFixed(1)} ${c.y.toFixed(1)} ${f.x.toFixed(1)} ${f.y.toFixed(1)}`;
  }

  function sincronizarArcos(desenharNovos = true) {
    const lista = listaArcos();
    const chaves = new Set(lista.map((x) => x.chave));
    const sel = !!estado.sel;
    for (const [chave, a] of arcos) {
      if (chaves.has(chave)) continue;
      arcos.delete(chave);
      const fim = () => a.path.remove();
      if (semMovimento()) fim();
      else A.animate(a.path, { opacity: 0, duration: 400, ease: "outQuad", onComplete: fim });
    }
    let novos = 0;
    for (const item of lista) {
      const largura = 0.8 + 7 * Math.sqrt(item.t / ref.rota[estado.grupo]);
      const opacidade = sel ? 0.85 : 0.42;
      let a = arcos.get(item.chave);
      if (!a) {
        const path = el("path", { classe: "arco", d: geometria(item), "stroke-width": largura.toFixed(2) });
        path.style.opacity = String(opacidade);
        camadaArcos.append(path);
        a = { path, dados: item };
        arcos.set(item.chave, a);
        if (desenharNovos && !semMovimento()) {
          const [linha] = A.svg.createDrawable(path);
          A.animate(linha, { draw: ["0 0", "0 1"], duration: 1100, delay: 120 + novos * 90, ease: "inOutQuad" });
        }
        novos++;
      } else {
        a.dados = item;
        if (semMovimento()) {
          a.path.setAttribute("stroke-width", largura.toFixed(2));
          a.path.style.opacity = String(opacidade);
        } else {
          A.animate(a.path, { strokeWidth: largura, opacity: opacidade, duration: 800, ease: "inOutQuad" });
        }
      }
      a.path.dataset.t = String(item.t);
    }
    reposicionarArcos();
  }

  function reposicionarArcos() {
    for (const a of arcos.values()) a.path.setAttribute("d", geometria(a.dados));
  }

  function pararNavios() {
    for (const n of navios) n.revert?.();
    navios = [];
    camadaNavios.replaceChildren();
  }

  function soltarNavios() {
    pararNavios();
    if (semMovimento() || !visivel) return;
    const lista = [...arcos.values()].sort((a, b) => b.dados.t - a.dados.t).slice(0, W < 600 ? 4 : 8);
    lista.forEach((a, k) => {
      const comprimento = a.path.getTotalLength();
      const duracao = clamp(comprimento * 9, 1800, 6000);
      [0, 1, 2, 3].forEach((rastro) => {
        const c = el("circle", { classe: "navio", r: String(rastro ? 2.2 - rastro * 0.45 : 2.6), cx: "0", cy: "0" });
        c.style.opacity = String(rastro ? 0.55 - rastro * 0.14 : 1);
        camadaNavios.append(c);
        navios.push(A.animate(c, { ...A.svg.createMotionPath(a.path), duration: duracao, delay: k * 260 + rastro * 70, ease: "inOutSine", loop: true }));
      });
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

  // --- Seleção ---------------------------------------------------------------------------------
  function selecionarOrigem(co, { instantaneo = false } = {}) {
    if (!bloco.paises[co]) return;
    estado.sel = { tipo: "origem", co };
    gravarUrl();
    const r = recorte();
    const rotas = r.rotas.filter((x) => x[0] === co);
    const v = bloco.paises[co];
    const alvos = [gradeDe(v[2], v[3]), ...(rotas.length ? rotas.map((x) => { const p = porto(x[1]); return gradeDe(p.lon, p.lat); }) : [centroIso("BRA")])];
    abrirPainel();
    sincronizarMarcadores();
    sincronizarArcos();
    anim.onda = 0;
    animar(anim, { onda: 1, duration: 1100, delay: 250, ease: "linear", onUpdate: pedirQuadro });
    const alvo = enquadrar(caixaDe(alvos));
    if (instantaneo) { Object.assign(cam, alvo); reposicionarArcos(); posicionarMarcadores(); soltarNavios(); pedirQuadro(); }
    else moverCamera(alvo);
    botaoVoltar.hidden = false;
  }

  function selecionarPorto(i, { instantaneo = false } = {}) {
    if (!bloco.portos[i]) return;
    estado.sel = { tipo: "porto", i };
    gravarUrl();
    const r = recorte();
    const rotas = r.rotas.filter((x) => x[1] === i);
    const p = porto(i);
    const alvos = [gradeDe(p.lon, p.lat), ...rotas.map((x) => { const v = bloco.paises[x[0]]; return gradeDe(v[2], v[3]); })];
    abrirPainel();
    sincronizarMarcadores();
    sincronizarArcos();
    const alvo = enquadrar(caixaDe(alvos.length > 1 ? alvos : [alvos[0], centroIso("BRA")]));
    if (instantaneo) { Object.assign(cam, alvo); reposicionarArcos(); posicionarMarcadores(); soltarNavios(); pedirQuadro(); }
    else moverCamera(alvo);
    botaoVoltar.hidden = false;
  }

  function voltar() {
    if (!estado.sel) return;
    estado.sel = null;
    gravarUrl();
    fecharPainel();
    sincronizarMarcadores();
    sincronizarArcos();
    moverCamera({ ...geral });
    botaoVoltar.hidden = true;
    pedirQuadro();
  }

  // --- Painel ---------------------------------------------------------------------------------
  const contadores = new Map(); // id -> {v}
  function contador(id, valor, formato) {
    const dd = document.getElementById(id);
    if (!dd) return;
    if (valor == null) { dd.textContent = "–"; contadores.delete(id); return; }
    const obj = contadores.get(id) ?? { v: 0 };
    contadores.set(id, obj);
    animar(obj, { v: valor, duration: 1000, ease: "outExpo", onUpdate: () => { dd.textContent = formato(obj.v); } });
    if (semMovimento()) dd.textContent = formato(valor);
  }

  function abrirPainel() {
    preencherPainel();
    painel.hidden = false;
    requestAnimationFrame(() => painel.classList.add("aberto"));
  }

  function fecharPainel() {
    painel.classList.remove("aberto");
    const esconder = () => { if (!estado.sel) painel.hidden = true; };
    if (semMovimento()) esconder();
    else setTimeout(esconder, 650);
  }

  function preencherPainel() {
    const sel = estado.sel;
    if (!sel) return;
    const r = recorte();
    const ano = rotuloAno(estado.ano);
    const numeros = document.getElementById("fluxos-numeros");
    const lista = document.getElementById("fluxos-lista");
    const linha = (rotulo, id) => el("div", {}, el("dt", {}, rotulo), el("dd", { id }));
    if (sel.tipo === "origem") {
      const o = r.origens.find((x) => x[0] === sel.co);
      document.getElementById("fluxos-painel-kicker").textContent = `Origem · ${NOMES_GRUPO[estado.grupo]} · ${ano}`;
      document.getElementById("fluxos-painel-titulo").textContent = nomePais(sel.co);
      if (!numeros.dataset.tipo || numeros.dataset.tipo !== "origem") {
        numeros.replaceChildren(linha("Toneladas", "fx-t"), linha(`Share de ${DESCRICAO_GRUPO[estado.grupo]}`, "fx-share"),
          linha("Frete", "fx-frete"), linha("Preço CIF", "fx-cif"), linha("Contra o ano anterior", "fx-var"));
        numeros.dataset.tipo = "origem";
        contadores.clear();
      }
      numeros.querySelector("#fx-share").previousSibling.textContent = `Share de ${DESCRICAO_GRUPO[estado.grupo]}`;
      contador("fx-t", o?.[1] ?? 0, toneladas);
      contador("fx-share", o?.[2] ?? null, pct);
      contador("fx-frete", o?.[4] ?? null, porTonelada);
      contador("fx-cif", o?.[5] ?? null, porTonelada);
      const v = variacao(o?.[3]);
      const dv = document.getElementById("fx-var");
      dv.textContent = v.texto;
      dv.className = v.classe;
      const rotas = r.rotas.filter((x) => x[0] === sel.co);
      const somaRotas = rotas.reduce((s, x) => s + x[2], 0);
      document.getElementById("fluxos-lista-titulo").textContent = rotas.length ? "Portos de chegada (rotas com pelo menos 50 mil t no ano)" : "Nenhuma rota acima do corte neste ano";
      const maior = Math.max(1, ...rotas.map((x) => x[2]), (o?.[1] ?? 0) - somaRotas);
      const itens = rotas.map((x) => itemLista(porto(x[1]).nome, `${toneladas(x[2])} · frete ${porTonelada(x[3])}`, x[2] / maior));
      const resto = (o?.[1] ?? 0) - somaRotas;
      if (resto > 0.5) itens.push(itemLista("Outras rotas", toneladas(resto), resto / maior, true));
      lista.replaceChildren(...itens);
      document.getElementById("fluxos-painel-nota").textContent = o
        ? "Frete e CIF: valor ÷ toneladas no ano. A variação compara com os mesmos meses do ano anterior."
        : `Sem importação registrada de ${nomePais(sel.co)} em ${ano} neste grupo.`;
    } else {
      const p = porto(sel.i);
      const tp = r.portos.find((x) => x[0] === sel.i)?.[1] ?? 0;
      document.getElementById("fluxos-painel-kicker").textContent = `Porto · ${NOMES_GRUPO[estado.grupo]} · ${ano}`;
      document.getElementById("fluxos-painel-titulo").textContent = `${p.nome} (${p.uf})`;
      if (numeros.dataset.tipo !== "porto") {
        numeros.replaceChildren(linha("Toneladas recebidas", "fx-pt"), linha("Share do Brasil", "fx-pshare"), linha("Origens com rota", "fx-pn"), linha("URFs", "fx-urfs"));
        numeros.dataset.tipo = "porto";
        contadores.clear();
      }
      const rotas = r.rotas.filter((x) => x[1] === sel.i);
      contador("fx-pt", tp, toneladas);
      contador("fx-pshare", r.t ? (100 * tp) / r.t : null, pct);
      contador("fx-pn", rotas.length, (v) => fmtInt.format(Math.round(v)));
      document.getElementById("fx-urfs").textContent = p.urfs.join(", ");
      const somaRotas = rotas.reduce((s, x) => s + x[2], 0);
      document.getElementById("fluxos-lista-titulo").textContent = rotas.length ? "Origens (rotas com pelo menos 50 mil t no ano)" : "Nenhuma rota acima do corte neste ano";
      const maior = Math.max(1, ...rotas.map((x) => x[2]), tp - somaRotas);
      const itens = rotas.map((x) => itemLista(nomePais(x[0]), `${toneladas(x[2])} · frete ${porTonelada(x[3])}`, x[2] / maior));
      if (tp - somaRotas > 0.5) itens.push(itemLista("Outras origens", toneladas(tp - somaRotas), (tp - somaRotas) / maior, true));
      lista.replaceChildren(...itens);
      document.getElementById("fluxos-painel-nota").textContent = "URFs no mesmo lugar aparecem como um só porto. Coordenadas: Natural Earth.";
    }
    if (!semMovimento()) A.animate(lista.querySelectorAll(".barra"), { scaleX: [0, 1], duration: 700, delay: A.stagger(45), ease: "outExpo" });
  }

  function itemLista(nome, valor, fracao, outras = false) {
    const barra = el("span", { classe: "barra" });
    barra.style.width = `${(100 * clamp(fracao, 0.02, 1)).toFixed(1)}%`;
    return el("li", { classe: outras ? "outras" : null }, el("span", {}, nome), el("span", { classe: "valor" }, valor), barra);
  }

  // --- Controles: grupo, ano e play ------------------------------------------------------------
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
  }

  function marcarGrupo(instantaneo) {
    const indicador = caixaGrupo.querySelector(".indicador");
    for (const b of caixaGrupo.querySelectorAll("button")) {
      const ativo = b.dataset.grupo === estado.grupo;
      b.setAttribute("aria-pressed", String(ativo));
      if (ativo) {
        if (instantaneo) indicador.style.transition = "none";
        indicador.style.width = `${b.offsetWidth}px`;
        indicador.style.transform = `translateX(${b.offsetLeft - 3}px)`;
        if (instantaneo) { void indicador.offsetWidth; indicador.style.transition = ""; }
      }
    }
  }

  function definirGrupo(g) {
    if (!ORDEM_GRUPOS.includes(g) || g === estado.grupo) { marcarGrupo(true); return; }
    estado.grupo = g;
    marcarGrupo(false);
    if (iniciado) mudouRecorte();
  }

  function montarAnos() {
    faixa.max = String(bloco.anos.length - 1);
    marcas.replaceChildren(...bloco.anos.map((a) => el("li", {}, `'${String(a).slice(2)}`)));
    faixa.addEventListener("input", () => {
      pararPlay();
      definirAno(bloco.anos[Number(faixa.value)]);
    });
  }

  function definirAno(ano, deTocar = false) {
    if (ano === estado.ano) return;
    estado.ano = ano;
    faixa.value = String(bloco.anos.indexOf(ano));
    saidaAno.textContent = rotuloAno(ano);
    faixa.setAttribute("aria-valuetext", rotuloAno(ano));
    if (iniciado) mudouRecorte(deTocar);
  }

  function mudouRecorte() {
    gravarUrl();
    atualizarIntensidades();
    atualizarChip();
    sincronizarMarcadores();
    sincronizarArcos();
    if (estado.sel) preencherPainel();
    if (!cameraAnimando) soltarNavios();
    tabela();
  }

  let tocando = null;
  function tocar() {
    if (tocando) { pararPlay(); return; }
    let i = bloco.anos.indexOf(estado.ano);
    if (i >= bloco.anos.length - 1) i = -1;
    botaoPlay.querySelector("span:last-child").textContent = "Pausa";
    botaoPlay.setAttribute("aria-label", "Pausar a linha do tempo");
    const passo = () => {
      i++;
      definirAno(bloco.anos[i], true);
      if (i >= bloco.anos.length - 1) pararPlay();
    };
    passo();
    tocando = setInterval(passo, semMovimento() ? 1200 : 1700);
  }
  function pararPlay() {
    if (!tocando) return;
    clearInterval(tocando);
    tocando = null;
    botaoPlay.querySelector("span:last-child").textContent = "Play";
    botaoPlay.setAttribute("aria-label", "Reproduzir a linha do tempo");
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
    const t = el("table", {}, el("caption", { classe: "oculto" }, `Origens em ${rotuloAno(estado.ano)}, ${DESCRICAO_GRUPO[estado.grupo]}`),
      el("thead", {}, el("tr", {}, ...cab.map((c, i) => el("th", { classe: i ? "num" : null, scope: "col" }, c)))),
      el("tbody", {}, ...r.origens.map((o) => el("tr", {}, el("td", {}, nomePais(o[0])), el("td", { classe: "num" }, fmtN(o[1], fmtInt)),
        el("td", { classe: "num" }, fmtN(o[2], fmt1)), el("td", { classe: "num" }, fmtN(o[3], fmt1)), el("td", { classe: "num" }, fmtN(o[4], fmt0)),
        el("td", { classe: "num" }, fmtN(o[5], fmt0)))), el("tr", {}, el("td", {}, "Outros (menos de 1.000 t ou sem coordenada)"), el("td", { classe: "num" }, fmtN(r.outros_t, fmtInt)),
        el("td", {}), el("td", {}), el("td", {}), el("td", {}))));
    const rotas = el("table", {}, el("caption", { classe: "oculto" }, "Rotas acima do corte"),
      el("thead", {}, el("tr", {}, ...["Origem", "Porto", "Toneladas", "Frete US$/t"].map((c, i) => el("th", { classe: i > 1 ? "num" : null, scope: "col" }, c)))),
      el("tbody", {}, ...r.rotas.map((x) => el("tr", {}, el("td", {}, nomePais(x[0])), el("td", {}, porto(x[1]).nome), el("td", { classe: "num" }, fmtN(x[2], fmtInt)), el("td", { classe: "num" }, fmtN(x[3], fmt0))))));
    document.getElementById("tabela-fluxos").replaceChildren(t, el("h4", { classe: "subtitulo" }, "Rotas com pelo menos 50 mil t no ano"), rotas);
  }

  // --- Cores e tamanho ----------------------------------------------------------------------------
  function lerCores() {
    cores = { ponto: variavel("--fluxo-ponto") || "#ffffff" };
  }

  function redimensionar() {
    if (!pontos) return;
    const antes = W;
    medir();
    if (!estado.sel || !antes) Object.assign(cam, geral);
    else if (estado.sel.tipo === "origem") selecionarOrigem(estado.sel.co, { instantaneo: true });
    else selecionarPorto(estado.sel.i, { instantaneo: true });
    reposicionarArcos();
    sincronizarMarcadores();
    marcarGrupo(true);
    soltarNavios();
    pedirQuadro();
  }

  // --- Entrada ------------------------------------------------------------------------------------
  function entrar() {
    iniciado = true;
    carregando.remove();
    atualizarIntensidades(true);
    atualizarChip();
    sincronizarMarcadores();
    tabela();
    const depois = () => {
      const url = lerUrl();
      sincronizarArcos();
      iniciarPulso();
      if (url.origem) selecionarOrigem(url.origem);
      else if (url.porto >= 0) selecionarPorto(url.porto);
      else soltarNavios();
    };
    if (semMovimento()) {
      anim.entrada = 1;
      anim.brasil = 1;
      posicionarMarcadores();
      pedirQuadro();
      depois();
      return;
    }
    // os pontos saem de um ruído disperso e se assentam em onda a partir do Brasil; o Brasil
    // acende por último, com o rótulo
    const linha = A.createTimeline({ defaults: { ease: "linear" } });
    linha.add(anim, { entrada: [0, 1], duration: 2400, onUpdate: () => { posicionarMarcadores(); pedirQuadro(); } }, 0)
      .add(anim, { brasil: [0, 1], duration: 700, ease: "outQuad", onUpdate: () => { posicionarChip(); pedirQuadro(); } }, 2100)
      .call(depois, 2500);
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
    let anoPadrao = bloco.anos[bloco.anos.length - 1];
    for (let i = bloco.anos.length - 1; i >= 0; i--) if (bloco.n_meses[i] === 12) { anoPadrao = bloco.anos[i]; break; }
    montarGrupos();
    montarAnos();
    definirAno(url.ano ?? anoPadrao);
    marcarGrupo(true);
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
    pedirQuadro();
    talvezIniciar();
  }

  document.addEventListener("radar:dados", (e) => receberDados(e.detail));
  if (window.radarDados) receberDados(window.radarDados);
  document.addEventListener("radar:filtros", (e) => definirGrupo(e.detail.grupo));
  botaoVoltar.addEventListener("click", voltar);
  botaoPlay.addEventListener("click", tocar);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && estado.sel) {
      pararPlay();
      voltar();
    }
  });
  new IntersectionObserver((entradas) => {
    visivel = entradas[0].isIntersecting;
    palco.classList.toggle("parado", !visivel);
    if (visivel) {
      pulso?.play();
      if (iniciado && !cameraAnimando) soltarNavios();
      talvezIniciar();
    } else {
      pulso?.pause();
      pararNavios();
      pararPlay();
    }
  }, { threshold: 0.2 }).observe(palco);
  new ResizeObserver(() => redimensionar()).observe(palco);
  const recolorir = () => { lerCores(); pedirQuadro(); };
  new MutationObserver(recolorir).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", recolorir);
  reduzido.addEventListener?.("change", () => {
    if (semMovimento()) { pulso?.pause(); pulso = null; pararNavios(); anim.entrada = 1; anim.brasil = 1; pedirQuadro(); }
    else iniciarPulso();
  });
  carregarGrade();
})();
