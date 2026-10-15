/* Navegação e movimento da página do radar de fertilizantes (o mapa do hero, com as rotas e os
 * dados por continente, fica em assets/mapa-hero.js).
 *
 * - Nav em pílula com indicador "líquido" que segue a rolagem, barra de progresso e rolagem
 *   animada pelo anime.js até a seção. Os links não mexem no hash da URL, que guarda os filtros
 *   (#grupo=...&de=...).
 * - Entrada das seções ao aparecer na tela (kicker decodificado, título por palavras, cartões).
 * - Animações ligadas à rolagem (onScroll do anime.js): hero, fundo de pontos, títulos, gráficos e
 *   mapa de fluxos acompanham a rolagem nos dois sentidos.
 * - Ícones dos botões do hero que se redesenham e o ícone do tema que gira.
 * Com prefers-reduced-motion: sem rolagem animada, sem entradas e sem nada ligado à rolagem.
 */
"use strict";
(() => {
  const reduzido = matchMedia("(prefers-reduced-motion: reduce)");

  // --- Navegação ---------------------------------------------------------------------
  const A = window.anime ?? null;
  const semMovimento = () => reduzido.matches || !A;
  const limitar = (v, a, b) => Math.min(b, Math.max(a, v));
  const misturar = (a, b, t) => a + (b - a) * t;
  const suavizar = (t) => t * t * (3 - 2 * t);
  const barra = document.getElementById("barra-topo");
  const nav = document.getElementById("nav-secoes");
  const indicador = nav.querySelector(".nav-indicador");
  const progresso = barra.querySelector(".barra-progresso");
  const links = [...nav.querySelectorAll("a[data-secao]")];
  const hero = document.getElementById("inicio");
  let ativa = null;

  // Posição de cada item na pílula (refeita no resize e quando as fontes carregam).
  let geoLinks = [];
  function medirLinks() {
    geoLinks = links.map((a) => ({ l: a.offsetLeft - 4, r: a.offsetLeft - 4 + a.offsetWidth }));
  }

  // Indicador "líquido": segue a rolagem. Perto do fim de cada seção, a borda da frente avança
  // para o próximo item antes da de trás (estica e encolhe). O que aparece persegue o alvo com
  // uma mola leve, para a roda do mouse (que rola em degraus) não dar trancos.
  const mostrado = { l: 0, r: 0, o: 0 };
  const alvo = { l: 0, r: 0, o: 0 };
  let laco = 0, anterior = 0;
  function aplicarIndicador() {
    indicador.style.transform = `translateX(${mostrado.l.toFixed(2)}px)`;
    indicador.style.width = `${Math.max(0, mostrado.r - mostrado.l).toFixed(2)}px`;
    indicador.style.opacity = mostrado.o.toFixed(3);
  }
  function seguirIndicador(agora) {
    const dt = Math.min(64, agora - (anterior || agora - 16.7));
    anterior = agora;
    const k = 1 - Math.pow(0.78, dt / 16.7);
    let resto = 0;
    for (const c of ["l", "r", "o"]) {
      mostrado[c] += (alvo[c] - mostrado[c]) * k;
      resto = Math.max(resto, Math.abs(alvo[c] - mostrado[c]) * (c === "o" ? 100 : 1));
    }
    if (resto < 0.2) Object.assign(mostrado, alvo);
    aplicarIndicador();
    laco = resto < 0.2 ? 0 : requestAnimationFrame(seguirIndicador);
    if (!laco) anterior = 0;
  }
  function moverIndicador(instantaneo) {
    // aparecendo do nada, o indicador nasce no lugar (não desliza desde a esquerda)
    if (mostrado.o < 0.05) { mostrado.l = alvo.l; mostrado.r = alvo.r; }
    if (instantaneo || semMovimento()) {
      cancelAnimationFrame(laco);
      laco = 0;
      Object.assign(mostrado, alvo);
      aplicarIndicador();
      return;
    }
    if (!laco) laco = requestAnimationFrame(seguirIndicador);
  }

  function ativar(id) {
    if (id === ativa) return;
    ativa = id;
    let atual = null;
    for (const a of links) {
      if (a.dataset.secao === id) {
        a.setAttribute("aria-current", "location");
        atual = a;
      } else a.removeAttribute("aria-current");
    }
    if (atual && nav.scrollWidth > nav.clientWidth) {
      // no celular a pílula rola de lado: o item ativo fica à vista
      const x = atual.offsetLeft - (nav.clientWidth - atual.offsetWidth) / 2;
      nav.scrollTo({ left: x, behavior: semMovimento() ? "auto" : "smooth" });
    }
  }

  function bordasDaNav() {
    nav.classList.toggle("mais-esquerda", nav.scrollLeft > 2);
    nav.classList.toggle("mais-direita", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
  }
  nav.addEventListener("scroll", bordasDaNav, { passive: true });

  function espiar(instantaneo = false) {
    const fimDoHero = hero.getBoundingClientRect().bottom;
    barra.classList.toggle("sobre-hero", fimDoHero > 56);
    const doc = document.documentElement;
    const maximo = doc.scrollHeight - window.innerHeight;
    progresso.style.transform = `scaleX(${maximo > 0 ? limitar(window.scrollY / maximo, 0, 1).toFixed(4) : 0})`;
    // seções visíveis, na ordem da nav, e o topo de cada uma em relação à linha de leitura
    const linha = window.innerHeight * 0.35;
    const itens = links.map((a, i) => ({ a, i, s: document.getElementById(a.dataset.secao) }))
      .filter((x) => x.s && !x.s.hidden && x.s.getClientRects().length);
    const topos = itens.map((x) => x.s.getBoundingClientRect().top - linha);
    let k = -1;
    topos.forEach((t, j) => { if (t <= 0) k = j; });
    const noFim = window.innerHeight + window.scrollY >= doc.scrollHeight - 4;
    if (noFim && itens.length) k = itens.length - 1; // a última seção, curta, também fica ativa
    if (fimDoHero > window.innerHeight * 0.4 || k < 0) {
      ativar(null);
      alvo.o = 0;
      moverIndicador(instantaneo);
      return;
    }
    let t = 0;
    if (!noFim && k < itens.length - 1) {
      const p = -topos[k] / (topos[k + 1] - topos[k]); // quanto da seção k já passou da linha
      t = limitar((p - 0.72) / 0.28, 0, 1);
    }
    const a = geoLinks[itens[k].i];
    const b = geoLinks[itens[Math.min(k + 1, itens.length - 1)].i];
    alvo.l = misturar(a.l, b.l, suavizar(limitar(t * 1.6 - 0.6, 0, 1)));
    alvo.r = misturar(a.r, b.r, suavizar(limitar(t * 1.6, 0, 1)));
    alvo.o = 1;
    ativar(itens[t >= 0.5 ? Math.min(k + 1, itens.length - 1) : k].a.dataset.secao);
    moverIndicador(instantaneo);
  }

  let pedido = 0;
  const espiarNoProximoQuadro = () => {
    if (!pedido) pedido = requestAnimationFrame(() => { pedido = 0; espiar(); });
  };
  window.addEventListener("scroll", espiarNoProximoQuadro, { passive: true });
  window.addEventListener("resize", () => {
    bordasDaNav();
    medirLinks();
    espiar(true);
  });
  document.fonts?.ready.then(() => { medirLinks(); espiar(true); });

  // Rolagem até uma seção: animada pelo anime.js (começo e fim suaves, duração pela distância).
  // Qualquer gesto da pessoa (roda, toque, tecla, clique) interrompe e devolve o controle.
  let rolagem = null;
  function pararRolagem() {
    if (!rolagem) return;
    rolagem.pause();
    rolagem = null;
  }
  for (const tipo of ["wheel", "touchstart", "keydown", "pointerdown"]) window.addEventListener(tipo, pararRolagem, { passive: true });
  function rolarAte(y) {
    const maximo = document.documentElement.scrollHeight - window.innerHeight;
    const destino = Math.round(limitar(y, 0, Math.max(0, maximo)));
    pararRolagem();
    if (semMovimento()) { window.scrollTo(0, destino); return; }
    const posicao = { y: window.scrollY };
    const distancia = Math.abs(destino - posicao.y);
    if (distancia < 2) return;
    rolagem = A.animate(posicao, {
      y: destino, duration: limitar(480 + distancia * 0.12, 650, 1500), ease: "inOutQuart",
      onUpdate: () => window.scrollTo(0, posicao.y),
      onComplete: () => { rolagem = null; },
    });
  }

  // Links internos (nav, marca e botões do hero): rolam até a seção e põem o foco no título
  // dela, sem trocar o hash (que é dos filtros).
  document.addEventListener("click", (evento) => {
    const link = evento.target.closest?.("a[data-secao]");
    if (!link || evento.defaultPrevented || evento.button !== 0 || evento.metaKey || evento.ctrlKey || evento.shiftKey) return;
    const secao = document.getElementById(link.dataset.secao);
    if (!secao) return;
    evento.preventDefault();
    const margem = parseFloat(getComputedStyle(secao).scrollMarginTop) || 0;
    rolarAte(link.dataset.secao === "inicio" ? 0 : secao.getBoundingClientRect().top + window.scrollY - margem);
    (document.getElementById(`t-${link.dataset.secao}`) ?? secao).focus?.({ preventScroll: true });
  });

  // --- Entrada das seções --------------------------------------------------------------
  // Ao entrar na tela: o kicker "decodifica" (scrambleText), as palavras do título sobem saindo
  // do desfoque (splitText) e os cartões deslizam para cima, em sequência. Sem movimento (ou sem
  // o anime.js), nada fica escondido.
  function prepararEntradas() {
    const raiz = document.documentElement;
    if (!raiz.classList.contains("movimento")) return;
    if (semMovimento()) { raiz.classList.remove("movimento"); return; }
    const alvos = [...document.querySelectorAll(".bloco-cabecalho, .cartao")];
    for (const el of alvos) {
      el.style.opacity = "0";
      el.classList.add("preparado");
    }
    const observador = new IntersectionObserver((entradas) => {
      let n = 0;
      for (const e of entradas) {
        if (!e.isIntersecting) continue;
        observador.unobserve(e.target);
        revelar(e.target, n++ * 90);
      }
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.06 });
    alvos.forEach((el) => observador.observe(el));
    // se a pessoa ligar "reduzir movimento" no meio, tudo aparece na hora
    reduzido.addEventListener?.("change", () => {
      if (!reduzido.matches) return;
      observador.disconnect();
      for (const el of alvos) el.style.removeProperty("opacity");
      raiz.classList.remove("movimento");
    });
  }

  function revelar(el, atraso) {
    const limpar = () => { el.style.removeProperty("opacity"); el.style.removeProperty("transform"); };
    if (!el.classList.contains("bloco-cabecalho")) {
      A.animate(el, { opacity: [0, 1], translateY: [26, 0], duration: 900, delay: atraso, ease: "outExpo", onComplete: limpar });
      return;
    }
    el.style.opacity = "1";
    const kicker = el.querySelector(".kicker");
    const titulo = el.querySelector(".titulo-bloco");
    const lead = el.querySelector(".bloco-lead");
    if (kicker) A.animate(kicker, { textContent: A.scrambleText({ chars: "A-Z0-9", cursor: true }), delay: atraso });
    if (titulo) {
      const { words } = A.splitText(titulo, { words: true });
      A.utils.set(words, { opacity: 0, translateY: "0.35em", filter: "blur(8px)" });
      A.animate(words, { opacity: 1, translateY: "0em", filter: "blur(0px)", duration: 850, delay: A.stagger(55, { start: atraso + 120 }), ease: "outExpo" });
    }
    if (lead) {
      A.utils.set(lead, { opacity: 0, translateY: 10 });
      A.animate(lead, { opacity: 1, translateY: 0, duration: 800, delay: atraso + 320, ease: "outQuad", onComplete: () => { lead.style.removeProperty("opacity"); lead.style.removeProperty("transform"); } });
    }
  }

  // --- Animações ligadas à rolagem -------------------------------------------------------------
  // Diferente das entradas (que tocam uma vez), estas acompanham a rolagem nos dois sentidos, com
  // o onScroll do anime.js sincronizado (e uma suavização leve): o hero "mergulha" ao sair, o fundo
  // de pontos anda devagar, os títulos flutuam, os gráficos se desenham da esquerda para a direita
  // enquanto sobem na tela e o mapa de fluxos se abre como uma janela.
  // O anime.js recalcula as posições sozinho quando a página muda de altura.
  const ligadas = []; // [animação, observador]
  function ligarRolagem() {
    if (semMovimento() || !A.onScroll) return;
    const aRolagem = (alvo, valores, parametros) => {
      if (!alvo) return;
      const observador = A.onScroll({ sync: 0.15, ...parametros });
      ligadas.push([A.animate(alvo, { ...valores, ease: "linear", autoplay: observador }), observador]);
    };
    const saidaDoHero = { target: hero, enter: "start start", leave: "start end" };
    // o mapa do hero (canvas e rotas, de assets/mapa-hero.js) cresce e esmaece junto
    aRolagem([document.getElementById("mapa-hero"), document.getElementById("rotas-hero")].filter(Boolean), { scale: [1, 1.14], opacity: [1, 0.3] }, saidaDoHero);
    aRolagem(hero.querySelector(".hero-conteudo"), { translateY: [0, -120], opacity: [1, 0] }, saidaDoHero);
    aRolagem(document.querySelector(".fundo-pontos"), { translateY: [0, -840] }, { target: document.body, enter: "start start", leave: "end end", sync: 0.1 });
    for (const t of document.querySelectorAll(".titulo-bloco")) {
      aRolagem(t, { translateY: [26, -26] }, { target: t, enter: "end start", leave: "start end", sync: 0.25 });
    }
    // "min+=": no fim da página, o gráfico que não chega a 65% da tela termina de abrir na rolagem máxima
    // (o diagrama de rotas tem a sua própria entrada: as fitas escorrem, no script principal)
    for (const g of document.querySelectorAll(".grafico")) {
      aRolagem(g, { clipPath: ["inset(0% 100% 0% 0%)", "inset(0% 0% 0% 0%)"] }, { target: g, enter: "end start", leave: "min+=65% start", sync: 0.2 });
    }
    const palcoFluxos = document.getElementById("fluxos-palco");
    aRolagem(palcoFluxos, { clipPath: ["inset(16% 24% 16% 24% round 28px)", "inset(0% 0% 0% 0% round 8px)"] },
      { target: palcoFluxos, enter: "end start", leave: "min+=60% start", sync: 0.2 });
    // ligar "reduzir movimento" no meio desfaz tudo (estado final, sem movimento)
    reduzido.addEventListener?.("change", () => {
      if (!reduzido.matches) return;
      for (const [animacao, observador] of ligadas) { observador.revert(); animacao.revert(); }
      ligadas.length = 0;
    });
  }

  // --- Ícones dos botões do hero -------------------------------------------------------------
  // Ao passar o mouse ou focar um botão, o ícone se redesenha: os traços correm de novo
  // (svg.createDrawable, criado já desenhado) e os pontos nascem com uma mola.
  function ligarIcones() {
    if (!A?.svg) return;
    for (const botao of document.querySelectorAll(".hero .botao-contorno")) {
      const desenho = botao.querySelector(".icone svg");
      if (!desenho) continue;
      const tracos = A.svg.createDrawable(desenho.querySelectorAll("path"), 0, 1);
      const pontos = [...desenho.querySelectorAll("circle")];
      let tocando = null;
      const tocar = () => {
        if (semMovimento() || tocando) return;
        tocando = A.createTimeline({ onComplete: () => { tocando = null; } })
          .add(tracos, { draw: ["0 0", "0 1"], duration: 650, ease: "inOutCubic", delay: A.stagger(70) }, 0);
        if (pontos.length) tocando.add(pontos, { scale: [0, 1], duration: 520, ease: "outBack(3)", delay: A.stagger(380) }, 0);
      };
      botao.addEventListener("pointerenter", tocar);
      botao.addEventListener("focus", tocar);
    }
    // botão do tema: o ícone de meio círculo gira meia volta, com uma mola, a cada troca
    const iconeTema = document.querySelector("#botao-tema svg");
    let giro = 0;
    document.getElementById("botao-tema")?.addEventListener("click", () => {
      if (semMovimento() || !iconeTema) return;
      giro += 180;
      A.animate(iconeTema, { rotate: giro, ease: A.createSpring ? A.createSpring({ bounce: 0.45, duration: 650 }) : "outBack(2)", duration: 900 });
    });
  }

  medirLinks();
  bordasDaNav();
  espiar(true);
  prepararEntradas();
  ligarRolagem();
  ligarIcones();
})();
