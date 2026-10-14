"""Fase 5: abstração de provedor de LLM (traga a sua chave, BYOK) para o boletim mensal.

O provedor vem de RADAR_LLM_PROVIDER:

- "anthropic" (padrão): Messages API da Anthropic. Lê só ANTHROPIC_API_KEY.
- "openai": qualquer API compatível com OpenAI (chat completions). Lê só OPENAI_API_KEY e
  OPENAI_BASE_URL (padrão: https://api.openai.com/v1; num servidor local a chave é opcional).
- "ollama": servidor local do Ollama, sem chave. Lê só OLLAMA_HOST (padrão: localhost:11434). O
  pedido leva options.num_ctx com contexto para o prompt inteiro (no mínimo 16k tokens).

O modelo vem de RADAR_LLM_MODEL. Para "anthropic" o padrão é claude-sonnet-5-5 (ID conferido em
https://platform.claude.com/docs/en/about-claude/models/overview); "openai" e "ollama" exigem
o nome do modelo, porque ele depende do servidor.

Só biblioteca padrão (urllib): o job do GitHub Actions que recebe a chave não instala
dependências de terceiros. A chave nunca vai para log, repr ou mensagem de erro, e
redirecionamentos HTTP são recusados (num POST eles levariam a chave para outra origem).
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ENV_PROVEDOR = "RADAR_LLM_PROVIDER"
ENV_MODELO = "RADAR_LLM_MODEL"
PROVEDORES = ("anthropic", "openai", "ollama")
PROVEDOR_PADRAO = "anthropic"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSAO = "2023-06-01"
ANTHROPIC_MODELO_PADRAO = "claude-sonnet-5-5"
# Inclui o thinking adaptativo, que conta no limite mesmo sem aparecer na resposta.
ANTHROPIC_MAX_TOKENS = 16000
OPENAI_BASE_PADRAO = "https://api.openai.com/v1"
OLLAMA_HOST_PADRAO = "http://localhost:11434"
OLLAMA_PORTA_PADRAO = 11434
# O Ollama usa 4k de contexto por padrão em máquinas com menos de 24 GiB de VRAM e corta o começo
# do prompt (as regras). Pedimos contexto para o prompt inteiro mais a resposta.
OLLAMA_NUM_CTX_MIN = 16384
OLLAMA_RESERVA_SAIDA = 4096

TIMEOUT_S = 180.0
TIMEOUT_OLLAMA_S = 600.0  # modelos locais podem ser lentos
MAX_TENTATIVAS = 3
ESPERA_BASE_S = 5.0
ESPERA_MAX_S = 60.0
# 429 (limite), 5xx e 529 (Anthropic sobrecarregada) valem nova tentativa; os demais 4xx não.
STATUS_REPETIR = {408, 409, 429, 500, 502, 503, 504, 529}

# (url, headers, corpo, timeout) -> (status, headers em minúsculas, corpo)
Transporte = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


class ErroLLM(RuntimeError):
    """Falha ao gerar texto (rede, HTTP, recusa ou resposta cortada)."""


class ErroConfig(ErroLLM):
    """Configuração ausente ou inválida (provedor, modelo, chave)."""


@dataclass(frozen=True)
class Resposta:
    texto: str
    modelo: str  # o modelo que a API diz ter usado


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """Não segue redirecionamentos: num POST eles viram GET e levariam a chave para outra origem."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # o 3xx volta como HTTPError e vira erro não repetível


def _transporte_urllib(url: str, headers: dict[str, str], corpo: bytes, timeout: float):
    pedido = urllib.request.Request(url, data=corpo, headers=headers, method="POST")
    abridor = urllib.request.build_opener(_SemRedirecionar)
    try:
        with abridor.open(pedido, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as erro:
        return erro.code, {k.lower(): v for k, v in (erro.headers or {}).items()}, erro.read() or b""


def _conferir_chave(provedor: str, variavel: str, chave: str) -> None:
    """Recusa chave com espaço, quebra de linha ou acento, sem repetir o valor na mensagem."""
    if not re.fullmatch(r"[\x21-\x7e]+", chave):
        raise ErroConfig(f"{provedor}: {variavel} tem caractere inválido (espaço, quebra de linha ou acento); confira o valor salvo.")


class Provedor:
    """Base: monta a requisição de cada API, envia com novas tentativas e extrai o texto."""

    nome = ""

    def __init__(self, modelo: str, *, transporte: Transporte | None = None, dormir=time.sleep, timeout: float = TIMEOUT_S):
        self.modelo = modelo
        self._transporte = transporte or _transporte_urllib
        self._dormir = dormir
        self._timeout = timeout
        self._segredos: tuple[str, ...] = ()

    def __repr__(self) -> str:  # nunca inclui a chave
        return f"{type(self).__name__}(modelo={self.modelo!r})"

    def gerar(self, sistema: str, usuario: str, *, esquema: dict | None = None) -> Resposta:
        url, headers, corpo = self._requisicao(sistema, usuario, esquema)
        return self._extrair(self._enviar(url, headers, json.dumps(corpo, ensure_ascii=False).encode("utf-8")))

    # --- a implementar por provedor -------------------------------------------------------
    def _requisicao(self, sistema: str, usuario: str, esquema: dict | None) -> tuple[str, dict[str, str], dict]:
        raise NotImplementedError

    def _extrair(self, dados: dict) -> Resposta:
        raise NotImplementedError

    def _mensagem_de_erro(self, dados) -> str:
        if isinstance(dados, dict):
            erro = dados.get("error")
            if isinstance(erro, dict):
                return str(erro.get("message") or erro.get("type") or "")
            if isinstance(erro, str):
                return erro
        return ""

    # --- comum ---------------------------------------------------------------------------
    def _limpar(self, texto: str) -> str:
        """Tira qualquer segredo de um texto que vai para log ou exceção."""
        for segredo in self._segredos:
            if segredo:
                texto = texto.replace(segredo, "***")
        return texto

    def _enviar(self, url: str, headers: dict[str, str], corpo: bytes) -> dict:
        destino = urlsplit(url)
        onde = f"{destino.scheme}://{destino.netloc}{destino.path}"
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            espera = None
            try:
                status, cabecalhos, bruto = self._transporte(url, headers, corpo, self._timeout)
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as erro:
                motivo = self._limpar(f"falha de rede ({type(erro).__name__}: {getattr(erro, 'reason', erro)})")
            except (ValueError, UnicodeError) as erro:
                # Cabeçalho inválido (ex.: chave com quebra de linha): a mensagem original repetiria a chave.
                raise ErroLLM(f"{self.nome}: requisição inválida ({type(erro).__name__}) para {onde}.") from None
            else:
                try:
                    dados = json.loads(bruto.decode("utf-8")) if bruto else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    dados = None
                if 200 <= status < 300:
                    if not isinstance(dados, dict):
                        raise ErroLLM(f"{self.nome}: resposta que não é JSON em {onde}.")
                    return dados
                if 300 <= status < 400:
                    raise ErroLLM(f"{self.nome}: HTTP {status} em {onde}: redirecionamento recusado (confira a URL do provedor).")
                detalhe = self._limpar(self._mensagem_de_erro(dados))[:300]
                motivo = f"HTTP {status}" + (f" ({detalhe})" if detalhe else "")
                if status not in STATUS_REPETIR:
                    raise ErroLLM(f"{self.nome}: {motivo} em {onde}.")
                try:
                    espera = float(cabecalhos.get("retry-after", ""))
                except ValueError:
                    espera = None
            if tentativa == MAX_TENTATIVAS:
                raise ErroLLM(f"{self.nome}: {motivo} em {onde}, após {MAX_TENTATIVAS} tentativas.")
            espera = min(ESPERA_MAX_S, espera if espera is not None else ESPERA_BASE_S * 3 ** (tentativa - 1))
            log.warning("%s: %s; tentativa %d/%d em %.0f s.", self.nome, motivo, tentativa + 1, MAX_TENTATIVAS, espera)
            self._dormir(espera)
        raise AssertionError("inalcançável")


class Anthropic(Provedor):
    """Messages API (POST /v1/messages), com saída JSON restrita por schema (structured outputs)."""

    nome = "anthropic"

    def __init__(self, chave: str, modelo: str = ANTHROPIC_MODELO_PADRAO, **kwargs):
        super().__init__(modelo, **kwargs)
        if not chave:
            raise ErroConfig("anthropic: defina ANTHROPIC_API_KEY (ou use --mock, que não chama nenhuma API).")
        _conferir_chave("anthropic", "ANTHROPIC_API_KEY", chave)
        self._chave = chave
        self._segredos = (chave,)

    def _requisicao(self, sistema, usuario, esquema):
        headers = {
            "content-type": "application/json",
            "x-api-key": self._chave,
            "anthropic-version": ANTHROPIC_VERSAO,
        }
        # Sem temperature/top_p: o Sonnet 5.5 recusa valores fora do padrão. Thinking e effort ficam
        # no padrão do modelo (adaptativo, effort high).
        corpo = {
            "model": self.modelo,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "system": sistema,
            "messages": [{"role": "user", "content": usuario}],
        }
        if esquema is not None:
            corpo["output_config"] = {"format": {"type": "json_schema", "schema": esquema}}
        return ANTHROPIC_URL, headers, corpo

    def _extrair(self, dados):
        motivo = dados.get("stop_reason")
        if motivo == "refusal":
            categoria = (dados.get("stop_details") or {}).get("category")
            raise ErroLLM(f"anthropic: o modelo recusou o pedido (categoria: {categoria or 'não informada'}).")
        if motivo == "max_tokens":
            raise ErroLLM(f"anthropic: resposta cortada no limite de {ANTHROPIC_MAX_TOKENS} tokens.")
        texto = "".join(b.get("text", "") for b in dados.get("content") or [] if b.get("type") == "text")
        if not texto.strip():
            raise ErroLLM("anthropic: resposta sem texto.")
        return Resposta(texto, str(dados.get("model") or self.modelo))


class OpenAICompativel(Provedor):
    """Chat completions (POST {base_url}/chat/completions) de qualquer API compatível com OpenAI."""

    nome = "openai"

    def __init__(self, chave: str | None, modelo: str, base_url: str | None = None, **kwargs):
        super().__init__(modelo, **kwargs)
        self.base_url = (base_url or OPENAI_BASE_PADRAO).rstrip("/")
        if urlsplit(self.base_url).scheme not in ("http", "https"):
            raise ErroConfig("openai: OPENAI_BASE_URL precisa começar com http:// ou https://.")
        if not chave and self.base_url == OPENAI_BASE_PADRAO:
            raise ErroConfig("openai: defina OPENAI_API_KEY (ou OPENAI_BASE_URL de um servidor que não exija chave).")
        if chave:
            _conferir_chave("openai", "OPENAI_API_KEY", chave)
        self._chave = chave or ""
        self._segredos = (self._chave,)

    def __repr__(self) -> str:
        return f"OpenAICompativel(modelo={self.modelo!r}, base_url={self.base_url!r})"

    def _requisicao(self, sistema, usuario, esquema):
        headers = {"content-type": "application/json"}
        if self._chave:
            headers["authorization"] = f"Bearer {self._chave}"
        # Só o essencial, para funcionar em qualquer servidor compatível: o formato JSON é pedido no
        # prompt e conferido pela mesma validação de todos os provedores.
        corpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        }
        return f"{self.base_url}/chat/completions", headers, corpo

    def _extrair(self, dados):
        escolhas = dados.get("choices") or []
        if not escolhas:
            raise ErroLLM("openai: resposta sem choices.")
        escolha = escolhas[0]
        if escolha.get("finish_reason") == "length":
            raise ErroLLM("openai: resposta cortada no limite de tokens.")
        mensagem = escolha.get("message") or {}
        texto = mensagem.get("content")
        if not texto:
            recusa = mensagem.get("refusal")
            raise ErroLLM("openai: o modelo recusou o pedido." if recusa else "openai: resposta sem texto.")
        return Resposta(str(texto), str(dados.get("model") or self.modelo))


class Ollama(Provedor):
    """Servidor local do Ollama (POST {host}/api/chat), sem chave."""

    nome = "ollama"

    def __init__(self, modelo: str, host: str | None = None, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT_OLLAMA_S)
        super().__init__(modelo, **kwargs)
        host = (host or OLLAMA_HOST_PADRAO).strip().rstrip("/")
        if "://" not in host:  # como o próprio Ollama: host[:porta], com a porta 11434 se faltar
            host = f"http://{host}"
            try:
                sem_porta = urlsplit(host).port is None
            except ValueError:
                raise ErroConfig("ollama: OLLAMA_HOST inválido; use host:porta ou http://host:porta.") from None
            if sem_porta:
                host = f"{host}:{OLLAMA_PORTA_PADRAO}"
        self.host = host

    def __repr__(self) -> str:
        return f"Ollama(modelo={self.modelo!r}, host={self.host!r})"

    def _requisicao(self, sistema, usuario, esquema):
        corpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "stream": False,
            "format": esquema if esquema is not None else "json",
            "options": {"num_ctx": num_ctx(sistema + usuario)},
        }
        return f"{self.host}/api/chat", {"content-type": "application/json"}, corpo

    def _extrair(self, dados):
        if dados.get("done_reason") == "length":
            raise ErroLLM("ollama: resposta cortada no limite de tokens.")
        texto = (dados.get("message") or {}).get("content")
        if not texto:
            raise ErroLLM("ollama: resposta sem texto.")
        return Resposta(str(texto), str(dados.get("model") or self.modelo))


def num_ctx(prompt: str) -> int:
    """Contexto pedido ao Ollama: o prompt (estimado em 1 token a cada 3 caracteres) mais a resposta,
    arredondado para cima em múltiplos de 4096 e nunca abaixo de 16384."""
    estimado = math.ceil(len(prompt) / 3) + OLLAMA_RESERVA_SAIDA
    return max(OLLAMA_NUM_CTX_MIN, math.ceil(estimado / 4096) * 4096)


def provedor_do_ambiente(env: Mapping[str, str] | None = None, **kwargs) -> Provedor:
    """Escolhe o provedor por RADAR_LLM_PROVIDER e lê só as variáveis desse provedor."""
    env = os.environ if env is None else env
    nome = (env.get(ENV_PROVEDOR) or PROVEDOR_PADRAO).strip().lower()
    modelo = (env.get(ENV_MODELO) or "").strip()
    if nome == "anthropic":
        return Anthropic((env.get("ANTHROPIC_API_KEY") or "").strip(), modelo or ANTHROPIC_MODELO_PADRAO, **kwargs)
    if nome not in PROVEDORES:
        # O valor não é repetido na mensagem: se alguém colar uma chave aqui por engano, ela não vai para o log.
        raise ErroConfig(f"{ENV_PROVEDOR} inválido; use {', '.join(PROVEDORES)}.")
    if not modelo:
        raise ErroConfig(f"{nome}: defina {ENV_MODELO} com o nome do modelo no seu servidor.")
    if nome == "openai":
        chave = (env.get("OPENAI_API_KEY") or "").strip()
        return OpenAICompativel(chave, modelo, (env.get("OPENAI_BASE_URL") or "").strip() or None, **kwargs)
    return Ollama(modelo, (env.get("OLLAMA_HOST") or "").strip() or None, **kwargs)
