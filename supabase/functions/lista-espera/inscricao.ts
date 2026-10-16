// Lista de espera do Graaos: CORS, validação, Turnstile, hash do IP, limite por hora e gravação.
//
// Sem dependências externas, para os testes rodarem sem rede e sem permissões; o index.ts só liga
// isto ao Deno.serve e ao supabase-js (cliente service_role). Regras:
// - só POST (outro método: 405) JSON vindo de uma das ORIGENS (sem Origin ou de outro site: 403);
// - sem TURNSTILE_SECRET_KEY, sem IP_HASH_SALT (32 ou mais caracteres) ou sem o banco: 503, antes
//   de ler o corpo, que tem limite de tamanho e de tempo;
// - campo isca ("website") preenchido: 200 sem gravar e sem chamar o Turnstile;
// - campos validados e normalizados com as regras das constraints de public.lista_espera;
// - token do Turnstile conferido no siteverify (sucesso, hostname e action) antes de tocar no banco;
// - ip_hash = SHA-256(IP + IP_HASH_SALT), no máximo LIMITE_POR_HORA inscrições por ip_hash por hora,
//   conferido de novo depois de gravar (envios simultâneos não passam do limite);
// - e-mail que já está na lista responde igual a um novo (não revela quem está inscrito);
// - logs só com o nome do evento e códigos: nunca e-mail, nome, IP, ip_hash, token nem segredos.

export const ORIGENS: readonly string[] = [
  "https://radar.graaos.cloud",
  "https://graaos.cloud",
  "https://mvsantos-dev.github.io",
];
// hostname que o siteverify devolve (onde o widget foi mostrado): os mesmos sites das ORIGENS
export const HOSTNAMES_TURNSTILE: readonly string[] = ORIGENS.map((origem) => new URL(origem).hostname);
export const ACAO_TURNSTILE = "lista-espera";
export const SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const TAMANHO_MAXIMO_TOKEN = 2048; // limite documentado pela Cloudflare
export const LIMITE_POR_HORA = 3;
export const JANELA_MS = 60 * 60 * 1000;
export const LIMITE_CORPO = 16 * 1024; // bytes; um envio legítimo tem menos de 4 KB
export const TAMANHO_MINIMO_SAL = 32; // ex.: openssl rand -hex 32 (64 caracteres)
export const PRAZO_CORPO_MS = 10_000;
const TEMPO_TURNSTILE_MS = 8000;
const TENTATIVAS_TURNSTILE = 2;

export const SEGMENTOS = [
  "revenda",
  "cooperativa",
  "trading",
  "industria",
  "logistica",
  "consultoria",
  "produtor",
  "outro",
] as const;
export const INTERESSES = ["alertas", "watchlist", "api", "relatorios", "mcp"] as const;
export type Segmento = typeof SEGMENTOS[number];
export type Interesse = typeof INTERESSES[number];

/** Campos validados de uma inscrição (id, criado_em e consentimento_em ficam com o default do banco). */
export interface Inscricao {
  nome: string;
  email: string;
  empresa: string | null;
  cargo: string | null;
  segmento: Segmento;
  interesses: Interesse[];
  origem: string | null;
  consentimento_lgpd: true;
}
/** Linha gravada em public.lista_espera. */
export interface Linha extends Inscricao {
  ip_hash: string;
}

// --- Validação --------------------------------------------------------------------------------
// Espelha as constraints da tabela (contagem em pontos de código, como o char_length do Postgres):
// nome 2 a 120 (sem os espaços das pontas), e-mail até 254 no padrão da lista_espera_email_check,
// empresa até 160, cargo até 120, segmento e interesses das listas fixas, origem até 200 e
// consentimento obrigatório. As regras a mais só recusam o que nenhum nome, empresa ou cargo de
// verdade tem: caracteres invisíveis (que escondem texto), acentos empilhados, links e < > (também
// nas formas largas e decompostas), texto sem letras e começo de fórmula de planilha (= + - @).
// Exibir ou exportar estes campos pede escape do mesmo jeito: eles vêm de quem preenche.

export const MENSAGENS = {
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
  tipo: "Valor inválido.",
  segmento: "Escolha o seu segmento.",
  interesses: "Escolha os interesses da lista.",
  consentimento: "Para entrar na lista, aceite o uso dos dados para este aviso.",
} as const;

// Invisíveis: controles C0/C1 (o NUL o Postgres nem aceita), formatação (largura zero, hífen
// suave, controles bidirecionais, tags Unicode que escondem texto), surrogates soltos, uso privado,
// não atribuídos, todos os Default_Ignorable e o braille em branco.
const INVISIVEIS = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Default_Ignorable_Code_Point}\u2800]/u;
// texto "zalgo": 4 ou mais acentos sobre a mesma letra depois de decompor (o vietnamita usa até 2)
const MARCAS_EMPILHADAS = /\p{M}{4,}/u;
const ESPACOS = /\s+/gu; // inclui quebras de linha, tabs e o espaço sem quebra: viram um espaço só
const MARCACAO = /[<>]|:\/\/|www\.|javascript:/iu;
const FORMULA = /^[=+\-@]/u;
// mesmo padrão da constraint (~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$'), já em minúsculas
const EMAIL = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;
const ASCII_VISIVEL = /^[\x21-\x7e]+$/;
// origem: só utm_source=…&utm_campaign=… (cada valor até 80, só letras, números e . _ ~ + - espaço)
const VALOR_UTM = /^[\p{L}\p{N} _.~+-]{1,80}$/u;
const CHAVES_UTM = ["utm_source", "utm_campaign"];

type Campo<T> = { ok: true; valor: T } | { ok: false; erro: string };
const aceito = <T>(valor: T): Campo<T> => ({ ok: true, valor });
const recusado = <T>(erro: string): Campo<T> => ({ ok: false, erro });
const vazio = (valor: unknown) => valor === undefined || valor === null || valor === "";
const pontosDeCodigo = (texto: string) => [...texto].length;

export function eObjeto(valor: unknown): valor is Record<string, unknown> {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}
/** Só as chaves do próprio objeto (nada vindo do protótipo). */
export function campo(corpo: Record<string, unknown>, nome: string): unknown {
  return Object.hasOwn(corpo, nome) ? corpo[nome] : undefined;
}

/** As formas em que um texto pode ser lido depois: como está, larga (＜ → <) e sem acentos (≮ → <). */
const formas = (texto: string) => [texto, texto.normalize("NFKC"), texto.normalize("NFD").replace(/\p{M}/gu, "")];

function textoLivre(
  bruto: unknown,
  regra: { minimo: number; maximo: number; vazio: string | null; tamanho: string; letras: RegExp },
): Campo<string | null> {
  if (vazio(bruto)) return regra.vazio ? recusado(regra.vazio) : aceito(null);
  if (typeof bruto !== "string") return recusado(MENSAGENS.tipo);
  if (bruto.length > 4 * regra.maximo + 64) return recusado(regra.tamanho); // nem normaliza texto enorme
  const texto = bruto.normalize("NFC").replace(ESPACOS, " ").trim();
  if (texto === "") return regra.vazio ? recusado(regra.vazio) : aceito(null);
  if (INVISIVEIS.test(texto) || MARCAS_EMPILHADAS.test(texto.normalize("NFD"))) {
    return recusado(MENSAGENS.invisiveis);
  }
  if (formas(texto).some((f) => MARCACAO.test(f))) return recusado(MENSAGENS.marcacao);
  if (formas(texto).some((f) => FORMULA.test(f))) return recusado(MENSAGENS.formula);
  const n = pontosDeCodigo(texto);
  if (n < regra.minimo || n > regra.maximo) return recusado(regra.tamanho);
  return regra.letras.test(texto) ? aceito(texto) : recusado(MENSAGENS.semLetras);
}
const DUAS_LETRAS = /\p{L}.*\p{L}/su;
const LETRA_OU_NUMERO = /[\p{L}\p{N}]/u;

export function validarEmail(bruto: unknown): Campo<string> {
  if (vazio(bruto)) return recusado(MENSAGENS.emailVazio);
  if (typeof bruto !== "string" || bruto.length > 400) return recusado(MENSAGENS.emailFormato);
  const aparado = bruto.trim();
  // só ASCII visível antes de passar para minúsculas (o "K" de Kelvin, por exemplo, viraria "k")
  if (!ASCII_VISIVEL.test(aparado)) return recusado(MENSAGENS.emailFormato);
  const email = aparado.toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) return recusado(MENSAGENS.emailFormato);
  // regras que nenhum endereço real quebra: até 64 antes do @, sem ponto nas pontas nem repetido,
  // domínio sem parte vazia nem hífen nas pontas de uma parte
  const [local, dominio] = email.split("@");
  if (local.length > 64 || local.startsWith(".") || local.endsWith(".") || local.includes("..")) {
    return recusado(MENSAGENS.emailFormato);
  }
  if (dominio.split(".").some((parte) => parte === "" || parte.startsWith("-") || parte.endsWith("-"))) {
    return recusado(MENSAGENS.emailFormato);
  }
  return aceito(email);
}

function validarSegmento(bruto: unknown): Campo<Segmento> {
  const segmento = SEGMENTOS.find((s) => s === bruto);
  return segmento ? aceito(segmento) : recusado(MENSAGENS.segmento);
}

function validarInteresses(bruto: unknown): Campo<Interesse[]> {
  if (bruto === undefined || bruto === null) return aceito([]);
  if (!Array.isArray(bruto) || bruto.length > 2 * INTERESSES.length) return recusado(MENSAGENS.interesses);
  if (!bruto.every((i) => INTERESSES.some((valido) => valido === i))) return recusado(MENSAGENS.interesses);
  return aceito(INTERESSES.filter((i) => bruto.includes(i))); // sem repetição, na ordem da lista
}

/**
 * Origem da visita, montada pela página: "utm_source=x", "utm_campaign=y" ou os dois, nessa ordem.
 * Fora desse formato (ou com valor estranho), fica null: a origem nunca recusa o envio.
 */
export function normalizarOrigem(bruto: unknown): string | null {
  if (typeof bruto !== "string" || bruto.length > 400) return null;
  const texto = bruto.normalize("NFC");
  const partes = texto.split("&");
  if (partes.length > CHAVES_UTM.length) return null;
  let anterior = -1;
  for (const parte of partes) {
    const igual = parte.indexOf("=");
    const posicao = CHAVES_UTM.indexOf(parte.slice(0, igual));
    // chave desconhecida (-1), repetida ou fora de ordem; valor fora do padrão
    if (igual < 0 || posicao <= anterior || !VALOR_UTM.test(parte.slice(igual + 1))) return null;
    anterior = posicao;
  }
  return texto;
}

export type Validacao = { ok: true; dados: Inscricao } | { ok: false; campos: Record<string, string> };

export function validarInscricao(corpo: Record<string, unknown>): Validacao {
  const nome = textoLivre(campo(corpo, "nome"), {
    minimo: 2,
    maximo: 120,
    vazio: MENSAGENS.nomeVazio,
    tamanho: MENSAGENS.nomeTamanho,
    letras: DUAS_LETRAS,
  });
  const email = validarEmail(campo(corpo, "email"));
  const empresa = textoLivre(campo(corpo, "empresa"), {
    minimo: 1,
    maximo: 160,
    vazio: null,
    tamanho: MENSAGENS.empresaTamanho,
    letras: LETRA_OU_NUMERO,
  });
  const cargo = textoLivre(campo(corpo, "cargo"), {
    minimo: 1,
    maximo: 120,
    vazio: null,
    tamanho: MENSAGENS.cargoTamanho,
    letras: LETRA_OU_NUMERO,
  });
  const segmento = validarSegmento(campo(corpo, "segmento"));
  const interesses = validarInteresses(campo(corpo, "interesses"));
  const consentimento = campo(corpo, "consentimento_lgpd") === true;

  const campos: Record<string, string> = {};
  if (!nome.ok) campos.nome = nome.erro;
  if (!email.ok) campos.email = email.erro;
  if (!empresa.ok) campos.empresa = empresa.erro;
  if (!cargo.ok) campos.cargo = cargo.erro;
  if (!segmento.ok) campos.segmento = segmento.erro;
  if (!interesses.ok) campos.interesses = interesses.erro;
  if (!consentimento) campos.consentimento_lgpd = MENSAGENS.consentimento;
  if (!nome.ok || !email.ok || !empresa.ok || !cargo.ok || !segmento.ok || !interesses.ok || !consentimento) {
    return { ok: false, campos };
  }
  return {
    ok: true,
    dados: {
      nome: nome.valor as string,
      email: email.valor,
      empresa: empresa.valor,
      cargo: cargo.valor,
      segmento: segmento.valor,
      interesses: interesses.valor,
      origem: normalizarOrigem(campo(corpo, "origem")),
      consentimento_lgpd: true,
    },
  };
}

// --- IP do cliente e hash ---------------------------------------------------------------------
export type Ip = { versao: 4; octetos: number[] } | { versao: 6; grupos: number[] };

const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;

function lerIpv4(texto: string): number[] | null {
  const m = IPV4.exec(texto);
  if (!m) return null;
  const octetos = m.slice(1).map(Number);
  return octetos.every((o) => o <= 255) ? octetos : null;
}

function lerIpv6(texto: string): number[] | null {
  if (!/^[0-9a-f:.]+$/.test(texto)) return null;
  const metades = texto.split("::");
  if (metades.length > 2) return null;
  // cada metade: grupos hexadecimais; só a última parte do endereço pode ser um IPv4 (::ffff:1.2.3.4)
  const grupos = (parte: string, podeTerIpv4: boolean): number[] | null => {
    if (parte === "") return [];
    const pedacos = parte.split(":");
    const saida: number[] = [];
    for (const [i, pedaco] of pedacos.entries()) {
      if (podeTerIpv4 && i === pedacos.length - 1 && pedaco.includes(".")) {
        const v4 = lerIpv4(pedaco);
        if (!v4) return null;
        saida.push((v4[0] << 8) | v4[1], (v4[2] << 8) | v4[3]);
      } else if (/^[0-9a-f]{1,4}$/.test(pedaco)) {
        saida.push(parseInt(pedaco, 16));
      } else {
        return null;
      }
    }
    return saida;
  };
  if (metades.length === 1) {
    const todos = grupos(metades[0], true);
    return todos && todos.length === 8 ? todos : null;
  }
  const esquerda = grupos(metades[0], false);
  const direita = grupos(metades[1], true);
  if (!esquerda || !direita) return null;
  const faltam = 8 - esquerda.length - direita.length;
  return faltam >= 1 ? [...esquerda, ...Array(faltam).fill(0), ...direita] : null;
}

/** IPv4 ou IPv6 válido (sem zona); IPv4 mapeado em IPv6 (::ffff:a.b.c.d) volta a ser IPv4. */
export function lerIp(bruto: string | null | undefined): Ip | null {
  const texto = (bruto ?? "").trim().toLowerCase();
  if (texto === "" || texto.length > 64) return null;
  const v4 = lerIpv4(texto);
  if (v4) return { versao: 4, octetos: v4 };
  const v6 = lerIpv6(texto.split("%")[0]);
  if (!v6) return null;
  if (v6.slice(0, 5).every((g) => g === 0) && v6[5] === 0xffff) {
    return { versao: 4, octetos: [v6[6] >> 8, v6[6] & 255, v6[7] >> 8, v6[7] & 255] };
  }
  return { versao: 6, grupos: v6 };
}

export function textoDoIp(ip: Ip): string {
  return ip.versao === 4 ? ip.octetos.join(".") : ip.grupos.map((g) => g.toString(16)).join(":");
}

/**
 * O que entra no hash: o IPv4 inteiro; no IPv6, o prefixo /64, porque uma conexão costuma receber
 * um /64 inteiro e trocaria de endereço a cada envio para escapar do limite. Sem IP, um balde só.
 */
export function chaveDoLimite(ip: Ip | null): string {
  if (!ip) return "sem-ip";
  if (ip.versao === 4) return textoDoIp(ip);
  return `${ip.grupos.slice(0, 4).map((g) => g.toString(16)).join(":")}::/64`;
}

export type FonteDoIp = "cf-connecting-ip" | "x-real-ip";

/**
 * IP de quem enviou, só de cabeçalhos que o cliente não escreve: na frente do Supabase está a
 * Cloudflare, que grava o CF-Connecting-IP (e sobrescreve o que o cliente mandar com esse nome), e
 * o X-Real-IP vem do gateway do Supabase. O X-Forwarded-For fica de fora: a Cloudflare mantém no
 * começo dele o que o próprio cliente mandou. Sem nenhum dos dois, um balde só para todos.
 */
export function ipDoCliente(cabecalhos: Headers): { ip: Ip | null; fonte: FonteDoIp | null } {
  for (const fonte of ["cf-connecting-ip", "x-real-ip"] as const) {
    const ip = lerIp(cabecalhos.get(fonte));
    if (ip) return { ip, fonte };
  }
  return { ip: null, fonte: null };
}

/** SHA-256(chave + sal), em hexadecimal (64 caracteres; a coluna aceita até 128). */
export async function hashDoIp(chave: string, sal: string): Promise<string> {
  const resumo = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(chave + sal));
  return Array.from(new Uint8Array(resumo), (b) => b.toString(16).padStart(2, "0")).join("");
}

// --- Logs ---------------------------------------------------------------------------------------
export type Detalhes = Record<string, string | number | boolean>;
export type Registro = (evento: string, detalhes?: Detalhes, nivel?: "info" | "aviso" | "erro") => void;

/** Uma linha JSON por evento. Só nomes de evento, códigos e contagens: nunca dados da pessoa. */
export const registrarNoConsole: Registro = (evento, detalhes = {}, nivel = "info") => {
  const linha = JSON.stringify({ funcao: "lista-espera", evento, ...detalhes });
  if (nivel === "erro") console.error(linha);
  else if (nivel === "aviso") console.warn(linha);
  else console.info(linha);
};

const seguro = (valor: unknown, padrao: RegExp) => typeof valor === "string" && padrao.test(valor) ? valor : "outro";

// --- Turnstile ----------------------------------------------------------------------------------
export type Verificacao = "ok" | "recusado" | "indisponivel";

interface ContextoTurnstile {
  fetch: typeof fetch;
  esperar: (ms: number) => Promise<void>;
  log: Registro;
}

/**
 * Confere o token no siteverify. Repete uma vez (com a mesma idempotency_key, que a Cloudflare
 * aceita para repetir sem gastar o token) quando não há resposta, quando a resposta é 5xx ou
 * quando vem internal-error. Segredo errado ou ausente para a Cloudflare: indisponível (503).
 * Sem remoteip (opcional): o navegador pode resolver o desafio por IPv6 e chamar a função por
 * IPv4, e a conferência do IP recusaria quem não fez nada de errado; o IP também não sai daqui.
 */
export async function verificarTurnstile(
  ctx: ContextoTurnstile,
  segredo: string,
  token: string,
): Promise<Verificacao> {
  const idempotencia = crypto.randomUUID();
  for (let tentativa = 1; tentativa <= TENTATIVAS_TURNSTILE; tentativa++) {
    const resultado = await consultarSiteverify(ctx, segredo, token, idempotencia);
    if (resultado !== "repetir") return resultado;
    if (tentativa < TENTATIVAS_TURNSTILE) await ctx.esperar(400);
  }
  return "indisponivel";
}

async function consultarSiteverify(
  ctx: ContextoTurnstile,
  segredo: string,
  token: string,
  idempotencia: string,
): Promise<Verificacao | "repetir"> {
  const corpo = new URLSearchParams({ secret: segredo, response: token, idempotency_key: idempotencia });
  const controle = new AbortController();
  const relogio = setTimeout(() => controle.abort(), TEMPO_TURNSTILE_MS);
  try {
    const resposta = await ctx.fetch(SITEVERIFY, { method: "POST", body: corpo, signal: controle.signal });
    if (!resposta.ok) {
      await resposta.body?.cancel();
      ctx.log("turnstile_http", { status: resposta.status }, "aviso");
      return resposta.status >= 500 ? "repetir" : "indisponivel";
    }
    return interpretarSiteverify(ctx, await resposta.json());
  } catch {
    ctx.log("turnstile_sem_resposta", {}, "aviso");
    return "repetir";
  } finally {
    clearTimeout(relogio);
  }
}

function interpretarSiteverify(ctx: ContextoTurnstile, dados: unknown): Verificacao | "repetir" {
  if (!eObjeto(dados)) return "repetir";
  const bruto = dados["error-codes"];
  const codigos = Array.isArray(bruto) ? bruto.map((c) => seguro(c, /^[a-z-]{1,40}$/)) : [];
  if (dados.success !== true) {
    ctx.log("turnstile_recusado", { codigos: codigos.join(",") || "nenhum" });
    if (codigos.includes("internal-error")) return "repetir";
    if (codigos.includes("missing-input-secret") || codigos.includes("invalid-input-secret")) {
      ctx.log("turnstile_segredo_invalido", {}, "erro");
      return "indisponivel";
    }
    return "recusado";
  }
  if (!HOSTNAMES_TURNSTILE.includes(dados.hostname as string)) {
    ctx.log("turnstile_hostname", { hostname: seguro(dados.hostname, /^[a-z0-9.-]{1,100}$/) }, "aviso");
    return "recusado";
  }
  if (dados.action !== ACAO_TURNSTILE) {
    ctx.log("turnstile_action", { action: seguro(dados.action, /^[a-z0-9_-]{1,32}$/i) }, "aviso");
    return "recusado";
  }
  return "ok";
}

// --- Banco ----------------------------------------------------------------------------------
export type Insercao = { resultado: "nova"; id: string } | { resultado: "repetida" };

export interface Banco {
  /** Linhas com esse ip_hash criadas a partir de `desde` (ISO 8601). */
  contarRecentes(ipHash: string, desde: string): Promise<number>;
  /** "repetida" quando o e-mail já estava na lista (índice único em lower(email)). */
  inserir(linha: Linha): Promise<Insercao>;
  /** Apaga a linha que esta função acabou de gravar (só pelo id, nada da pessoa na URL). */
  remover(id: string): Promise<void>;
}

/** Erro do banco só com o código (SQLSTATE ou PGRST): a mensagem e os detalhes do Postgres podem
 * trazer os valores da linha, inclusive o e-mail, e nunca vão para o log nem para a resposta. */
export class ErroBanco extends Error {
  constructor(readonly codigo: string) {
    super(`erro do banco (${codigo})`);
    this.name = "ErroBanco";
  }
}

interface ErroPostgrest {
  code?: string | null;
}
export interface FiltroContagem extends PromiseLike<{ count: number | null; error: ErroPostgrest | null }> {
  eq(coluna: string, valor: string): FiltroContagem;
  gte(coluna: string, valor: string): FiltroContagem;
}
/** O pedaço do cliente do supabase-js que a função usa (facilita o cliente falso dos testes). */
export interface ClienteSupabase {
  from(tabela: string): {
    select(colunas: string, opcoes: { count: "exact"; head: true }): FiltroContagem;
    insert(linha: Linha): {
      select(
        colunas: string,
      ): { single(): PromiseLike<{ data: { id?: unknown } | null; error: ErroPostgrest | null }> };
    };
    delete(): { eq(coluna: string, valor: string): PromiseLike<{ error: ErroPostgrest | null }> };
  };
}

const codigoSeguro = (codigo: unknown) => seguro(codigo, /^(?:[0-9A-Z]{5}|PGRST\d{3})$/);

export function bancoSupabase(cliente: ClienteSupabase): Banco {
  return {
    async contarRecentes(ipHash, desde) {
      const { count, error } = await cliente
        .from("lista_espera")
        .select("id", { count: "exact", head: true })
        .eq("ip_hash", ipHash)
        .gte("criado_em", desde);
      if (error) throw new ErroBanco(codigoSeguro(error.code));
      if (typeof count !== "number") throw new ErroBanco("sem-contagem");
      return count;
    },
    async inserir(linha) {
      // só o id volta (para desfazer a inscrição se o limite estourar com envios simultâneos)
      const { data, error } = await cliente.from("lista_espera").insert(linha).select("id").single();
      if (!error && typeof data?.id === "string") return { resultado: "nova", id: data.id };
      if (error?.code === "23505") return { resultado: "repetida" };
      throw new ErroBanco(error ? codigoSeguro(error.code) : "sem-id");
    },
    async remover(id) {
      const { error } = await cliente.from("lista_espera").delete().eq("id", id);
      if (error) throw new ErroBanco(codigoSeguro(error.code));
    },
  };
}

/**
 * Chave do cliente service_role. Primeiro a secret key "default" de SUPABASE_SECRET_KEYS (o formato
 * novo de chaves do Supabase); sem ela, SUPABASE_SERVICE_ROLE_KEY, a legada, que o Supabase diz que
 * funciona até o fim de 2026. As duas já vêm no ambiente das Edge Functions.
 */
export function chaveDeServico(env: (nome: string) => string | undefined): string | null {
  try {
    const chaves: unknown = JSON.parse(env("SUPABASE_SECRET_KEYS") ?? "null");
    const padrao = eObjeto(chaves) ? chaves["default"] : null;
    if (typeof padrao === "string" && padrao.trim()) return padrao.trim();
  } catch {
    // JSON inválido: tenta a chave legada
  }
  return env("SUPABASE_SERVICE_ROLE_KEY")?.trim() || null;
}

// --- Pedido HTTP ----------------------------------------------------------------------------
export interface Dependencias {
  env: (nome: string) => string | undefined;
  banco: Banco | null;
  fetch?: typeof fetch;
  agora?: () => number;
  esperar?: (ms: number) => Promise<void>;
  log?: Registro;
  prazoCorpoMs?: number;
}
interface Contexto extends ContextoTurnstile {
  env: (nome: string) => string | undefined;
  banco: Banco | null;
  agora: () => number;
  prazoCorpoMs: number;
}

function origemPermitida(pedido: Request): string | null {
  const origem = pedido.headers.get("origin");
  return origem !== null && ORIGENS.includes(origem) ? origem : null;
}

function responder(
  status: number,
  corpo: Record<string, unknown>,
  origem: string | null,
  extras: Record<string, string> = {},
): Response {
  const cabecalhos = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    Vary: "Origin",
    ...extras,
  });
  if (origem) cabecalhos.set("Access-Control-Allow-Origin", origem);
  return new Response(JSON.stringify(corpo), { status, headers: cabecalhos });
}

function preflight(origem: string | null): Response {
  if (!origem) return new Response(null, { status: 403, headers: { Vary: "Origin" } });
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": origem,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
      "Access-Control-Max-Age": "600",
      Vary: "Origin",
    },
  });
}

type Corpo = { texto: string } | { erro: "grande" | "codificacao" | "interrompido" };

/**
 * Lê o corpo até `limite` bytes e `prazoMs` milissegundos, sem confiar no Content-Length (que pode
 * faltar ou mentir). Corpo que para no meio ou demora demais: "interrompido" (400, sem erro no log).
 */
async function lerCorpo(pedido: Request, limite: number, prazoMs: number): Promise<Corpo> {
  const declarado = pedido.headers.get("content-length");
  if (declarado !== null && !(Number(declarado) <= limite)) return { erro: "grande" };
  if (!pedido.body) return { texto: "" };
  const leitor = pedido.body.getReader();
  const partes: Uint8Array[] = [];
  let total = 0;
  let estourou = false;
  const relogio = setTimeout(() => {
    estourou = true;
    leitor.cancel().catch(() => {});
  }, prazoMs);
  try {
    for (;;) {
      const { done, value } = await leitor.read();
      if (estourou) return { erro: "interrompido" };
      if (done) break;
      total += value.byteLength;
      if (total > limite) {
        await leitor.cancel();
        return { erro: "grande" };
      }
      partes.push(value);
    }
  } catch {
    return { erro: "interrompido" };
  } finally {
    clearTimeout(relogio);
  }
  const bytes = new Uint8Array(total);
  let posicao = 0;
  for (const parte of partes) {
    bytes.set(parte, posicao);
    posicao += parte.byteLength;
  }
  try {
    return { texto: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return { erro: "codificacao" };
  }
}

export function criarTratador(dep: Dependencias): (pedido: Request) => Promise<Response> {
  const ctx: Contexto = {
    env: dep.env,
    banco: dep.banco,
    fetch: dep.fetch ?? ((url, opcoes) => fetch(url, opcoes)),
    agora: dep.agora ?? (() => Date.now()),
    esperar: dep.esperar ?? ((ms) => new Promise((resolver) => setTimeout(resolver, ms))),
    log: dep.log ?? registrarNoConsole,
    prazoCorpoMs: dep.prazoCorpoMs ?? PRAZO_CORPO_MS,
  };
  return async (pedido) => {
    const origem = origemPermitida(pedido);
    try {
      return await tratar(pedido, origem, ctx);
    } catch (erro) {
      // só o tipo do erro: a mensagem pode trazer dados do pedido
      ctx.log("erro_inesperado", { tipo: erro instanceof Error ? erro.name : typeof erro }, "erro");
      return responder(500, { ok: false, erro: "interno" }, origem);
    }
  };
}

async function tratar(pedido: Request, origem: string | null, ctx: Contexto): Promise<Response> {
  if (pedido.method === "OPTIONS") return preflight(origem);
  if (pedido.method !== "POST") {
    return responder(405, { ok: false, erro: "metodo" }, origem, { Allow: "POST, OPTIONS" });
  }
  if (!origem) {
    ctx.log("origem_recusada", { origem: seguro(pedido.headers.get("origin"), /^[a-z0-9.:/-]{1,100}$/) });
    return responder(403, { ok: false, erro: "origem" }, null);
  }

  const segredo = ctx.env("TURNSTILE_SECRET_KEY")?.trim() ?? "";
  // o mesmo valor aparado na conferência e no hash (uma quebra de linha no fim não muda nada)
  const sal = ctx.env("IP_HASH_SALT")?.trim() ?? "";
  const banco = ctx.banco;
  if (segredo === "" || sal.length < TAMANHO_MINIMO_SAL || !banco) {
    ctx.log("configuracao_incompleta", {
      turnstile: segredo !== "",
      sal: sal.length >= TAMANHO_MINIMO_SAL,
      banco: banco !== null,
    }, "erro");
    return responder(503, { ok: false, erro: "indisponivel" }, origem);
  }

  const tipo = (pedido.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (tipo !== "application/json") return responder(415, { ok: false, erro: "tipo" }, origem);
  const lido = await lerCorpo(pedido, LIMITE_CORPO, ctx.prazoCorpoMs);
  if ("erro" in lido) {
    if (lido.erro === "interrompido") ctx.log("corpo_interrompido");
    return responder(lido.erro === "grande" ? 413 : 400, { ok: false, erro: lido.erro }, origem);
  }
  let corpo: unknown;
  try {
    corpo = JSON.parse(lido.texto);
  } catch {
    return responder(400, { ok: false, erro: "json" }, origem);
  }
  if (!eObjeto(corpo)) return responder(400, { ok: false, erro: "json" }, origem);

  // Isca para robôs: o campo fica fora da tela e fora do teclado e dos leitores de tela.
  if (!vazio(campo(corpo, "website"))) {
    ctx.log("isca_preenchida");
    return responder(200, { ok: true }, origem);
  }

  // Campos antes do Turnstile: um erro de digitação não gasta o token (vale por 300 s).
  const validacao = validarInscricao(corpo);
  if (!validacao.ok) {
    return responder(422, { ok: false, erro: "campos", campos: validacao.campos }, origem);
  }

  const token = campo(corpo, "turnstile");
  if (typeof token !== "string" || token === "" || token.length > TAMANHO_MAXIMO_TOKEN) {
    return responder(403, { ok: false, erro: "verificacao" }, origem);
  }
  const verificacao = await verificarTurnstile(ctx, segredo, token);
  if (verificacao === "recusado") return responder(403, { ok: false, erro: "verificacao" }, origem);
  if (verificacao === "indisponivel") return responder(503, { ok: false, erro: "indisponivel" }, origem);

  const { ip, fonte } = ipDoCliente(pedido.headers);
  if (!ip) ctx.log("sem_ip", {}, "aviso");
  else if (fonte !== "cf-connecting-ip") ctx.log("ip_sem_cloudflare", { fonte: fonte ?? "nenhuma" }, "aviso");
  const ipHash = await hashDoIp(chaveDoLimite(ip), sal);
  const desde = new Date(ctx.agora() - JANELA_MS).toISOString();
  const limite = () =>
    responder(429, { ok: false, erro: "limite" }, origem, {
      "Retry-After": String(JANELA_MS / 1000),
      "Access-Control-Expose-Headers": "Retry-After",
    });
  try {
    if (await banco.contarRecentes(ipHash, desde) >= LIMITE_POR_HORA) {
      ctx.log("limite_por_hora");
      return limite();
    }
    // e-mail repetido responde igual a um novo: a lista não revela quem já está nela
    const insercao = await banco.inserir({ ...validacao.dados, ip_hash: ipHash });
    if (insercao.resultado === "repetida") {
      ctx.log("inscricao_repetida");
    } else if (await banco.contarRecentes(ipHash, desde) > LIMITE_POR_HORA) {
      // Envios simultâneos passam juntos pela primeira contagem: depois de gravar, quem vê mais
      // do que o limite desfaz a própria linha. A última a contar vê todas as que ficaram, então
      // nunca sobram mais do que LIMITE_POR_HORA na janela.
      await banco.remover(insercao.id);
      ctx.log("limite_por_hora_simultaneo");
      return limite();
    } else {
      ctx.log("inscricao");
    }
  } catch (erro) {
    if (!(erro instanceof ErroBanco)) throw erro;
    ctx.log("erro_banco", { codigo: erro.codigo }, "erro");
    return responder(503, { ok: false, erro: "indisponivel" }, origem);
  }
  return responder(200, { ok: true }, origem);
}
