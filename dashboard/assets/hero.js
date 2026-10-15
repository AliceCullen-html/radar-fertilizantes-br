/* Hero e navegação do radar de fertilizantes.
 *
 * - Mapa-múndi em pontos num canvas, a partir de assets/mapa-pontos.json (gerado do Natural
 *   Earth 1:110m por `python -m src.mapa pontos`). Nenhum cálculo de geografia aqui: só desenho.
 * - Kicker e chip do Brasil com números do dashboard.json (evento "radar:dados" do script
 *   principal), nunca fixos no código.
 * - Nav em pílula com indicador deslizante e scroll-spy. Os links rolam até a seção sem mexer
 *   no hash da URL, que guarda os filtros (#grupo=...&de=...).
 * Com prefers-reduced-motion: sem onda de brilho, sem rolagem suave e sem transições.
 */
"use strict";
(() => {
  const reduzido = matchMedia("(prefers-reduced-motion: reduce)");
  const fmtCompacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  const fmtInteiro = new Intl.NumberFormat("pt-BR");
  const variavel = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();

  // --- Navegação ---------------------------------------------------------------------
  const barra = document.getElementById("barra-topo");
  const nav = document.getElementById("nav-secoes");
  const indicador = nav.querySelector(".nav-indicador");
  const links = [...nav.querySelectorAll("a[data-secao]")];
  const hero = document.getElementById("inicio");
  let ativa;

  function moverIndicador(link, instantaneo) {
    if (!link) {
      indicador.style.opacity = "0";
      return;
    }
    if (instantaneo) indicador.style.transition = "none";
    indicador.style.width = `${link.offsetWidth}px`;
    indicador.style.transform = `translateX(${link.offsetLeft - 4}px)`;
    indicador.style.opacity = "1";
    if (instantaneo) {
      void indicador.offsetWidth; // aplica a posição sem animar e devolve a transição
      indicador.style.transition = "";
    }
  }

  function ativar(id, instantaneo = false) {
    if (id === ativa) return;
    const primeira = ativa === undefined || ativa === null;
    ativa = id;
    let atual = null;
    for (const a of links) {
      if (a.dataset.secao === id) {
        a.setAttribute("aria-current", "location");
        atual = a;
      } else a.removeAttribute("aria-current");
    }
    moverIndicador(atual, instantaneo || primeira);
    if (atual && nav.scrollWidth > nav.clientWidth) {
      // no celular a pílula rola de lado: o item ativo fica à vista
      const alvo = atual.offsetLeft - (nav.clientWidth - atual.offsetWidth) / 2;
      nav.scrollTo({ left: alvo, behavior: reduzido.matches ? "auto" : "smooth" });
    }
  }

  function bordasDaNav() {
    nav.classList.toggle("mais-esquerda", nav.scrollLeft > 2);
    nav.classList.toggle("mais-direita", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
  }
  nav.addEventListener("scroll", bordasDaNav, { passive: true });

  function espiar() {
    const fimDoHero = hero.getBoundingClientRect().bottom;
    barra.classList.toggle("sobre-hero", fimDoHero > 56);
    if (fimDoHero > window.innerHeight * 0.4) {
      ativar(null);
      return;
    }
    let atual = null;
    for (const a of links) {
      const secao = document.getElementById(a.dataset.secao);
      if (secao && !secao.hidden && secao.getBoundingClientRect().top <= window.innerHeight * 0.35) atual = a.dataset.secao;
    }
    // no fim da página, a última seção curta (Método) também fica ativa
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) atual = links[links.length - 1].dataset.secao;
    ativar(atual);
  }

  let pedido = 0;
  const espiarNoProximoQuadro = () => {
    if (!pedido) pedido = requestAnimationFrame(() => { pedido = 0; espiar(); });
  };
  window.addEventListener("scroll", espiarNoProximoQuadro, { passive: true });
  window.addEventListener("resize", () => {
    bordasDaNav();
    moverIndicador(links.find((a) => a.dataset.secao === ativa), true);
    espiarNoProximoQuadro();
  });
  document.fonts?.ready.then(() => moverIndicador(links.find((a) => a.dataset.secao === ativa), true));

  // Links internos (nav, marca e botões do hero): rolam até a seção e põem o foco no título
  // dela, sem trocar o hash (que é dos filtros).
  document.addEventListener("click", (evento) => {
    const link = evento.target.closest?.("a[data-secao]");
    if (!link || evento.defaultPrevented || evento.button !== 0 || evento.metaKey || evento.ctrlKey || evento.shiftKey) return;
    const secao = document.getElementById(link.dataset.secao);
    if (!secao) return;
    evento.preventDefault();
    const comportamento = reduzido.matches ? "auto" : "smooth";
    if (link.dataset.secao === "inicio") window.scrollTo({ top: 0, behavior: comportamento });
    else secao.scrollIntoView({ behavior: comportamento, block: "start" });
    (document.getElementById(`t-${link.dataset.secao}`) ?? secao).focus?.({ preventScroll: true });
  });

  // --- Mapa de pontos ----------------------------------------------------------------
  const canvas = document.getElementById("mapa-hero");
  const ctx = canvas.getContext("2d");
  const RECORTE = { oeste: -95, leste: 150 }; // longitudes mostradas: das Américas ao Japão
  const BRASIL = "BRA";
  const CICLO_ONDA = 7000; // ms entre o começo de uma onda e o da seguinte
  const DURACAO_ONDA = 3600;
  let mapa = null;
  let pontos = []; // {x, y, brasil}, ordenados por x
  let geo = null; // {passo, x0, y0, c0} da última medida
  let base = null; // camada estática (canvas fora da tela)
  let largura = 0, altura = 0, escala = 1;
  let rodando = false, heroVisivel = true, inicioOnda = 0;

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
    geo = { passo, x0, y0, c0 };
    pontos = [];
    mapa.grade.forEach((trincas, linha) => {
      for (let i = 0; i < trincas.length; i += 3) {
        const brasil = mapa.paises[trincas[i + 2]] === BRASIL;
        for (let c = trincas[i]; c < trincas[i] + trincas[i + 1]; c++) {
          if (c < c0 || c >= c1) continue;
          pontos.push({ x: x0 + (c - c0 + 0.5) * passo, y: y0 + (linha + 0.5) * passo, brasil });
        }
      }
    });
    pontos.sort((a, b) => a.x - b.x);
  }

  function lonLat(lon, lat) {
    return {
      x: geo.x0 + ((lon - mapa.oeste) / mapa.passo - geo.c0) * geo.passo,
      y: geo.y0 + ((mapa.norte - lat) / mapa.passo) * geo.passo,
    };
  }

  function desenharBase() {
    base = document.createElement("canvas");
    base.width = canvas.width;
    base.height = canvas.height;
    const b = base.getContext("2d");
    b.setTransform(escala, 0, 0, escala, 0, 0);
    const lado = Math.max(1.4, geo.passo * 0.58);
    const meio = lado / 2;
    const corPonto = variavel("--hero-ponto");
    const corBrasil = variavel("--hero-ponto-destaque");
    b.fillStyle = corPonto;
    for (const p of pontos) if (!p.brasil) b.fillRect(p.x - meio, p.y - meio, lado, lado);
    b.fillStyle = corBrasil;
    for (const p of pontos) if (p.brasil) b.fillRect(p.x - meio, p.y - meio, lado, lado);
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

  function quadro(agora) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (base) ctx.drawImage(base, 0, 0);
    if (!rodando) return;
    const t = (agora - inicioOnda) % CICLO_ONDA;
    if (t < DURACAO_ONDA) {
      // Onda de brilho: uma faixa vertical que atravessa os pontos da esquerda para a direita.
      const minX = pontos.length ? pontos[0].x : 0;
      const maxX = pontos.length ? pontos[pontos.length - 1].x : 0;
      const largura = Math.max(40, geo.passo * 9);
      const progresso = 1 - (1 - t / DURACAO_ONDA) ** 2; // ease-out
      const centro = minX - largura * 2 + (maxX - minX + largura * 4) * progresso;
      const lado = Math.max(1.4, geo.passo * 0.58);
      const meio = lado / 2;
      const rgb = variavel("--hero-onda");
      ctx.setTransform(escala, 0, 0, escala, 0, 0);
      for (let i = primeiro(centro - largura * 2.5); i < pontos.length && pontos[i].x <= centro + largura * 2.5; i++) {
        const p = pontos[i];
        const d = (p.x - centro) / largura;
        const forca = Math.exp(-d * d) * (p.brasil ? 0.25 : 0.55);
        if (forca < 0.03) continue;
        ctx.fillStyle = `rgba(${rgb}, ${forca.toFixed(3)})`;
        ctx.fillRect(p.x - meio, p.y - meio, lado, lado);
      }
    }
    requestAnimationFrame(quadro);
  }

  function animar() {
    const deve = !reduzido.matches && heroVisivel && document.visibilityState === "visible" && pontos.length > 0;
    if (deve && !rodando) {
      rodando = true;
      inicioOnda = performance.now() - CICLO_ONDA + 600; // primeira onda logo depois de aparecer
      requestAnimationFrame(quadro);
    } else if (!deve && rodando) {
      rodando = false;
      requestAnimationFrame(quadro); // um último quadro limpo, sem a faixa
    }
  }

  function redesenhar() {
    if (!mapa) return;
    medir();
    projetar();
    desenharBase();
    quadro(performance.now());
    posicionarChip();
    animar();
  }

  // --- Dados (kicker, fonte e chip do Brasil) --------------------------------------
  let dados = null;

  function posicionarChip() {
    const chip = document.getElementById("chip-brasil");
    const ponto = mapa?.rotulos?.[BRASIL];
    if (!dados || !geo || !ponto || !chip.textContent) return;
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
    if (partes.length) document.getElementById("kicker-hero").textContent = partes.join(", ");
    if (!d.benchmark) document.getElementById("fonte-hero").textContent = "Fonte: Comex Stat/MDIC · Base cartográfica: Natural Earth";
    // Chip no Brasil: toneladas do último ano completo (12 meses), todos os grupos.
    const anual = d.anual;
    if (anual?.anos && anual.TOTAL?.t) {
      let i = anual.anos.length - 1;
      while (i >= 0 && anual.n_meses[i] !== 12) i--;
      if (i >= 0 && anual.TOTAL.t[i] != null) {
        const chip = document.getElementById("chip-brasil");
        chip.replaceChildren(document.createElement("strong"), ` · ${fmtCompacto.format(anual.TOTAL.t[i])} t importadas em ${anual.anos[i]}`);
        chip.firstChild.textContent = "Brasil";
      }
    }
    posicionarChip();
  }

  document.addEventListener("radar:dados", (e) => preencher(e.detail));
  if (window.radarDados) preencher(window.radarDados);

  // --- Início ------------------------------------------------------------------------
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

  carregarMapa();
  bordasDaNav();
  espiar();
})();
