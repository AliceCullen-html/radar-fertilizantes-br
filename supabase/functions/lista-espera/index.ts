// Edge Function da lista de espera do Graaos (Supabase, Deno). A lógica e as regras de segurança
// estão em inscricao.ts; os testes, em supabase/functions/tests/ (deno test, sem rede).
//
// O deploy é feito por fora deste repositório, sem verificação de JWT: o formulário do dashboard é
// público e não manda chave nenhuma. A proteção fica no código (CORS restrito, Turnstile, isca e
// limite por IP). O supabase/config.toml já traz verify_jwt = false; na linha de comando:
//   supabase functions deploy lista-espera --no-verify-jwt
// Secrets da função (painel do Supabase ou supabase secrets set --env-file; nunca no repositório):
//   TURNSTILE_SECRET_KEY   secret key do widget do Turnstile (sem ela, a função responde 503)
//   IP_HASH_SALT           sal aleatório de 32 ou mais caracteres (ex.: openssl rand -hex 32)
// SUPABASE_URL e a chave de serviço (SUPABASE_SECRET_KEYS, ou a legada SUPABASE_SERVICE_ROLE_KEY)
// já vêm no ambiente das Edge Functions.
import { createClient } from "npm:@supabase/supabase-js@2.117.2";
import { bancoSupabase, chaveDeServico, type ClienteSupabase, criarTratador } from "./inscricao.ts";

const env = (nome: string) => Deno.env.get(nome);
const url = env("SUPABASE_URL");
const chave = chaveDeServico(env);
// cliente service_role: grava na tabela, que tem RLS ligado e nenhuma policy para anon
const cliente = url && chave
  ? createClient(url, chave, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } })
  : null;
// Comparar o tipo inteiro do supabase-js com a interface mínima estoura o limite de instanciação do
// TypeScript. As chamadas que inscricao.ts faz (from, select, eq, gte, insert, single, delete) são as
// da API pública do cliente e estão cobertas pelo cliente falso dos testes.
const banco = cliente ? bancoSupabase(cliente as unknown as ClienteSupabase) : null;

Deno.serve(criarTratador({ env, banco }));
