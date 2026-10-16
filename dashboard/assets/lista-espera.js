/* Lista de espera do Graaos: o formulário "Receba alertas do seu mercado".
 *
 * Envia para a Edge Function do Supabase (supabase/functions/lista-espera), que confere o
 * Turnstile, valida de novo e grava; as regras dos campos aqui são as mesmas de lá. A URL da
 * função (data-funcao do formulário) e a site key do Turnstile (data-sitekey) ficam no HTML e são
 * públicas: nenhum segredo passa pela página. O script do Turnstile vem da Cloudflare sem SRI (a
 * Cloudflare atualiza o arquivo e não permite fixar nem copiar a versão) e só carrega quando alguém
 * usa o formulário (foco, toque ou envio): quem só passa pela página não fala com a Cloudflare. A
 * origem da visita (utm_source e utm_campaign da URL) vai junto, para medir qual post trouxe cada
 * inscrição. Sem JavaScript, o botão fica desligado (nada sai pela URL).
 */
(() => {
  "use strict";

  const SCRIPT_TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=radarTurnstilePronto";
  const ACAO = "lista-espera";
  const ESPERA_TOKEN_MS = 20000;
  const TEMPO_ENVIO_MS = 25000;

  // as mesmas mensagens e regras da função (supabase/functions/lista-espera/inscricao.ts)
  const MENSAGENS = {
    nomeVazio: "Informe o seu nome.",
    nomeTamanho: "O nome precisa ter de 2 a 120 caracteres.",
    emailVazio: "Informe o seu e-mail.",
    emailFormato: "Confira o e-mail: ele precisa ter o formato nome@empresa.com.br.",
    empresaTamanho: "Use até 160 caracteres.",
    cargoTamanho: "Use até 120 caracteres.",
    invisiveis: "Há caracteres invisíveis ou de controle no texto.",
    marcacao: "Não use links nem os sinais < e >.",
    formula: "Não comece com =, +, - ou @.",
    semLetras: "Escreva com letras.",
    segmento: "Escolha o seu segmento.",
    interesses: "Escolha os interesses da lista.",
    consentimento: "Para entrar na lista, aceite o uso dos dados para este aviso.",
  };
  const ERROS = {
    verificacao: "A verificação anti-robô expirou ou não passou. Ela recomeça sozinha: tente de novo em alguns segundos.",
    limite: "Já saíram muitas inscrições desta rede na última hora. Tente de novo mais tarde ou escreva para contato@graaos.cloud.",
    indisponivel: "A lista está fora do ar agora. Tente de novo em alguns minutos.",
    rede: "Não foi possível falar com a lista. Confira a conexão e tente de novo.",
    turnstile: "A verificação anti-robô da Cloudflare não carregou. Se usar um bloqueador, libere este site, ou tente de outra rede.",
    generico: "Não foi possível enviar agora. Tente de novo.",
  };
  // invisíveis: controles, formatação (largura zero, bidirecionais, tags Unicode), surrogates soltos,
  // uso privado, não atribuídos, todos os Default_Ignorable e o braille em branco
  const INVISIVEIS = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Default_Ignorable_Code_Point}\u2800]/u;
  const MARCAS_EMPILHADAS = /\p{M}{4,}/u; // "zalgo", conferido no texto decomposto (NFD)
  const ESPACOS = /\s+/gu;
  const MARCACAO = /[<>]|:\/\/|www\.|javascript:/iu;
  const FORMULA = /^[=+\-@]/u;
  const DUAS_LETRAS = /\p{L}.*\p{L}/su;
  const LETRA_OU_NUMERO = /[\p{L}\p{N}]/u;
  // como o texto pode ser lido depois: como está, largo (＜ vira <) e sem acentos (≮ vira <)
  const formas = (t) => [t, t.normalize("NFKC"), t.normalize("NFD").replace(/\p{M}/gu, "")];
  const EMAIL = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;
  const ASCII_VISIVEL = /^[\x21-\x7e]+$/;
  const SEGMENTOS = ["revenda", "cooperativa", "trading", "industria", "logistica", "consultoria", "produtor", "outro"];
  const INTERESSES = ["alertas", "watchlist", "api", "relatorios", "mcp"];
  // campo do JSON -> id do controle na página (o erro fica em `${id}-erro`)
  const CAMPOS = {
    nome: "le-nome",
    email: "le-email",
    empresa: "le-empresa",
    cargo: "le-cargo",
    segmento: "le-segmento",
    interesses: "le-interesses",
    consentimento_lgpd: "le-consentimento",
  };

  const form = document.getElementById("le-form");
  if (!form) return;
  const $ = (id) => document.getElementById(id);
  const caixaTurnstile = $("le-turnstile");
  const botao = $("le-enviar");
  const rotulo = $("le-enviar-rotulo");
  const status = $("le-status");
  const erroGeral = $("le-erro");
  const notaTurnstile = $("le-turnstile-nota");
  const notaPadrao = notaTurnstile.textContent;
  const FUNCAO = form.dataset.funcao;

  // --- Origem da visita: utm_source e utm_campaign, limpos e curtos -------------------------
  const ORIGEM = (() => {
    const q = new URLSearchParams(location.search);
    // só o que a função aceita (letras, números, espaço e . _ ~ + -), até 80 por valor
    const limpar = (v) => [...(v ?? "").normalize("NFC").replace(/[^\p{L}\p{N} _.~+-]/gu, "").trim()].slice(0, 80).join("").trim();
    const partes = [["utm_source", limpar(q.get("utm_source"))], ["utm_campaign", limpar(q.get("utm_campaign"))]]
      .filter(([, valor]) => valor)
      .map(([chave, valor]) => `${chave}=${valor}`);
    return partes.join("&") || null;
  })();

  // --- Validação (igual à da função) ----------------------------------------------------------
  const vazio = (v) => v === undefined || v === null || v === "";
  function textoLivre(bruto, minimo, maximo, msgVazio, msgTamanho, letras) {
    const texto = String(bruto ?? "").normalize("NFC").replace(ESPACOS, " ").trim();
    if (texto === "") return msgVazio ? { erro: msgVazio } : { valor: null };
    if (INVISIVEIS.test(texto) || MARCAS_EMPILHADAS.test(texto.normalize("NFD"))) return { erro: MENSAGENS.invisiveis };
    if (formas(texto).some((f) => MARCACAO.test(f))) return { erro: MENSAGENS.marcacao };
    if (formas(texto).some((f) => FORMULA.test(f))) return { erro: MENSAGENS.formula };
    const n = [...texto].length; // pontos de código, como o char_length do Postgres
    if (n < minimo || n > maximo) return { erro: msgTamanho };
    return letras.test(texto) ? { valor: texto } : { erro: MENSAGENS.semLetras };
  }
  function validarEmail(bruto) {
    const aparado = String(bruto ?? "").trim();
    if (aparado === "") return { erro: MENSAGENS.emailVazio };
    if (!ASCII_VISIVEL.test(aparado)) return { erro: MENSAGENS.emailFormato };
    const email = aparado.toLowerCase();
    if (email.length > 254 || !EMAIL.test(email)) return { erro: MENSAGENS.emailFormato };
    const [local, dominio] = email.split("@");
    const localRuim = local.length > 64 || local.startsWith(".") || local.endsWith(".") || local.includes("..");
    const dominioRuim = dominio.split(".").some((p) => p === "" || p.startsWith("-") || p.endsWith("-"));
    return localRuim || dominioRuim ? { erro: MENSAGENS.emailFormato } : { valor: email };
  }
  function coletar() {
    const marcados = [...form.querySelectorAll('input[name="interesses"]:checked')].map((i) => i.value);
    const resultado = {
      nome: textoLivre($("le-nome").value, 2, 120, MENSAGENS.nomeVazio, MENSAGENS.nomeTamanho, DUAS_LETRAS),
      email: validarEmail($("le-email").value),
      empresa: textoLivre($("le-empresa").value, 1, 160, null, MENSAGENS.empresaTamanho, LETRA_OU_NUMERO),
      cargo: textoLivre($("le-cargo").value, 1, 120, null, MENSAGENS.cargoTamanho, LETRA_OU_NUMERO),
      segmento: SEGMENTOS.includes($("le-segmento").value) ? { valor: $("le-segmento").value } : { erro: MENSAGENS.segmento },
      interesses: marcados.every((i) => INTERESSES.includes(i)) ? { valor: INTERESSES.filter((i) => marcados.includes(i)) } : { erro: MENSAGENS.interesses },
      consentimento_lgpd: $("le-consentimento").checked ? { valor: true } : { erro: MENSAGENS.consentimento },
    };
    const erros = Object.fromEntries(Object.entries(resultado).filter(([, r]) => r.erro).map(([k, r]) => [k, r.erro]));
    const dados = Object.fromEntries(Object.entries(resultado).map(([k, r]) => [k, r.valor]));
    return { erros, dados: { ...dados, origem: ORIGEM } };
  }

  // --- Mensagens na página ------------------------------------------------------------------
  function erroDeCampo(nome, mensagem) {
    const id = CAMPOS[nome];
    if (!id) return;
    const caixa = $(`${id}-erro`);
    caixa.textContent = mensagem || "";
    caixa.hidden = !mensagem;
    const controle = $(id);
    if (controle && nome !== "interesses") {
      if (mensagem) controle.setAttribute("aria-invalid", "true");
      else controle.removeAttribute("aria-invalid");
    }
  }
  function mostrarErros(erros) {
    for (const nome of Object.keys(CAMPOS)) erroDeCampo(nome, erros[nome]);
    const primeiro = Object.keys(CAMPOS).find((nome) => erros[nome]);
    if (!primeiro) return false;
    const alvo = primeiro === "interesses" ? form.querySelector('input[name="interesses"]') : $(CAMPOS[primeiro]);
    alvo?.focus();
    return true;
  }
  function erro(mensagem) {
    erroGeral.textContent = mensagem || "";
    erroGeral.hidden = !mensagem;
  }
  function enviando(sim, texto = "") {
    form.setAttribute("aria-busy", String(sim));
    botao.setAttribute("aria-disabled", String(sim));
    botao.classList.toggle("enviando", sim);
    rotulo.textContent = sim ? "Enviando…" : "Entrar na lista";
    status.textContent = texto;
  }
  // os erros somem quando a pessoa mexe no campo
  form.addEventListener("input", (evento) => {
    const nome = evento.target.name === "website" ? null : evento.target.name;
    if (nome && CAMPOS[nome]) erroDeCampo(nome, "");
  });

  // --- Turnstile ----------------------------------------------------------------------------
  let carregado = false;
  let widget = null;
  let token = null;
  let ocupado = false;
  const esperas = new Set();

  const temaAtual = () => document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  function nota(mensagem) {
    notaTurnstile.textContent = mensagem || notaPadrao;
    notaTurnstile.classList.toggle("alerta", Boolean(mensagem));
  }
  function receberToken(novo) {
    token = novo;
    nota("");
    for (const espera of esperas) espera.cumprir(novo);
    esperas.clear();
  }
  function falharEsperas() {
    for (const espera of esperas) espera.falhar(new Error("turnstile"));
    esperas.clear();
  }
  function renderizar() {
    if (!window.turnstile || widget !== null) return;
    widget = window.turnstile.render(caixaTurnstile, {
      sitekey: caixaTurnstile.dataset.sitekey,
      action: ACAO,
      theme: temaAtual(),
      language: "pt-br",
      size: caixaTurnstile.clientWidth >= 300 ? "flexible" : "compact",
      "response-field": false,
      callback: receberToken,
      "expired-callback": () => { token = null; },
      "timeout-callback": () => { token = null; },
      "error-callback": () => { token = null; nota(ERROS.turnstile); },
      "unsupported-callback": () => { token = null; nota(ERROS.turnstile); falharEsperas(); },
    });
  }
  function carregarTurnstile() {
    if (carregado) return;
    carregado = true;
    window.radarTurnstilePronto = renderizar;
    const script = document.createElement("script");
    script.src = SCRIPT_TURNSTILE;
    script.async = true;
    script.onerror = () => { nota(ERROS.turnstile); falharEsperas(); };
    document.head.append(script);
  }
  // o token vale uma vez: depois de cada envio que chegou a ser conferido, um novo desafio
  function novoToken() {
    token = null;
    if (widget !== null) window.turnstile?.reset(widget);
  }
  function esperarToken() {
    if (token) return Promise.resolve(token);
    carregarTurnstile();
    return new Promise((cumprir, falhar) => {
      const espera = {
        cumprir: (t) => { clearTimeout(espera.relogio); cumprir(t); },
        falhar: (e) => { clearTimeout(espera.relogio); falhar(e); },
      };
      espera.relogio = setTimeout(() => { esperas.delete(espera); falhar(new Error("turnstile")); }, ESPERA_TOKEN_MS);
      esperas.add(espera);
    });
  }
  // o widget acompanha o tema (refaz o desafio, que costuma passar sozinho)
  const trocarTema = () => {
    if (widget === null || ocupado || !window.turnstile) return;
    window.turnstile.remove(widget);
    widget = null;
    token = null;
    renderizar();
  };
  new MutationObserver(trocarTema).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", trocarTema);

  // carrega na primeira interação com o formulário (o envio também carrega, em esperarToken)
  for (const evento of ["focusin", "pointerdown"]) form.addEventListener(evento, carregarTurnstile, { once: true });

  // --- Envio --------------------------------------------------------------------------------
  async function postar(corpo) {
    const controle = new AbortController();
    const relogio = setTimeout(() => controle.abort(), TEMPO_ENVIO_MS);
    try {
      const resposta = await fetch(FUNCAO, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corpo),
        mode: "cors",
        credentials: "omit",
        cache: "no-store",
        signal: controle.signal,
      });
      let json = {};
      try {
        const lido = await resposta.json();
        if (lido && typeof lido === "object" && !Array.isArray(lido)) json = lido;
      } catch { /* resposta sem JSON */ }
      return { status: resposta.status, json };
    } finally {
      clearTimeout(relogio);
    }
  }

  function sucesso() {
    form.hidden = true;
    const painel = $("le-sucesso");
    painel.hidden = false;
    painel.focus();
  }

  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    if (ocupado) return;
    erro("");
    const { erros, dados } = coletar();
    if (mostrarErros(erros)) return;
    ocupado = true;
    try {
      enviando(true, token ? "Enviando a sua inscrição…" : "Aguardando a verificação anti-robô…");
      let verificado;
      try {
        verificado = await esperarToken();
      } catch {
        erro(ERROS.turnstile);
        return;
      }
      enviando(true, "Enviando a sua inscrição…");
      let resposta;
      try {
        resposta = await postar({ ...dados, turnstile: verificado, website: $("le-website").value });
      } catch {
        novoToken();
        erro(ERROS.rede);
        return;
      }
      if (resposta.status === 200 && resposta.json.ok === true) {
        sucesso();
        return;
      }
      // 422: a função recusa os campos antes de conferir o Turnstile, então o token não foi gasto
      if (resposta.status === 422 && typeof resposta.json.campos === "object" && resposta.json.campos) {
        if (!mostrarErros(resposta.json.campos)) erro(ERROS.generico);
        return;
      }
      novoToken();
      const codigo = String(resposta.json.erro ?? "");
      const porStatus = resposta.status === 429 ? ERROS.limite : resposta.status === 503 ? ERROS.indisponivel : ERROS.generico;
      erro(["verificacao", "limite", "indisponivel"].includes(codigo) ? ERROS[codigo] : porStatus);
    } finally {
      ocupado = false;
      if (!form.hidden) enviando(false);
    }
  });
  // o HTML traz o botão desligado: sem este script, o formulário não envia nada
  botao.disabled = false;
})();
