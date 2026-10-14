"""Testes da abstração de provedores (src/llm.py). Nenhum teste usa rede nem chave real."""

import json
import urllib.error

import pytest

from src import llm

CHAVE = "chave-ficticia-de-teste"


class EnvRegistrado(dict):
    """Ambiente que registra quais variáveis foram lidas."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lidas = set()

    def get(self, chave, padrao=None):
        self.lidas.add(chave)
        return super().get(chave, padrao)

    def __getitem__(self, chave):
        self.lidas.add(chave)
        return super().__getitem__(chave)


class TransporteFalso:
    """Devolve as respostas na ordem dada e guarda as requisições."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def __call__(self, url, headers, corpo, timeout):
        self.chamadas.append({"url": url, "headers": headers, "corpo": json.loads(corpo), "timeout": timeout})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        status, dados, *resto = resposta
        return status, (resto[0] if resto else {}), json.dumps(dados).encode()


def resposta_anthropic(texto='{"ok": true}', **extra):
    return 200, {
        "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": texto}],
        **extra,
    }


# --- Seleção do provedor ---------------------------------------------------------------------


def test_padrao_e_anthropic_com_sonnet_5_5():
    env = EnvRegistrado(ANTHROPIC_API_KEY=CHAVE, OPENAI_API_KEY="outra", OLLAMA_HOST="x")
    p = llm.provedor_do_ambiente(env)
    assert isinstance(p, llm.Anthropic) and p.modelo == "claude-sonnet-5-5"
    # cada provedor lê só a própria variável
    assert env.lidas == {"RADAR_LLM_PROVIDER", "RADAR_LLM_MODEL", "ANTHROPIC_API_KEY"}


@pytest.mark.parametrize(
    "env, classe, lidas",
    [
        (
            {"RADAR_LLM_PROVIDER": "openai", "RADAR_LLM_MODEL": "modelo-x", "OPENAI_API_KEY": CHAVE},
            llm.OpenAICompativel,
            {"OPENAI_API_KEY", "OPENAI_BASE_URL"},
        ),
        (
            {"RADAR_LLM_PROVIDER": " Ollama ", "RADAR_LLM_MODEL": "modelo-local"},
            llm.Ollama,
            {"OLLAMA_HOST"},
        ),
    ],
)
def test_selecao_le_so_as_variaveis_do_provedor(env, classe, lidas):
    env = EnvRegistrado(env, ANTHROPIC_API_KEY="nao-deve-ser-lida")
    assert isinstance(llm.provedor_do_ambiente(env), classe)
    assert env.lidas == {"RADAR_LLM_PROVIDER", "RADAR_LLM_MODEL", *lidas}


def test_modelo_configuravel():
    p = llm.provedor_do_ambiente({"ANTHROPIC_API_KEY": CHAVE, "RADAR_LLM_MODEL": "claude-opus-5-5"})
    assert p.modelo == "claude-opus-5-5"


@pytest.mark.parametrize(
    "env, trecho",
    [
        ({}, "ANTHROPIC_API_KEY"),
        ({"RADAR_LLM_PROVIDER": "openai"}, "RADAR_LLM_MODEL"),
        ({"RADAR_LLM_PROVIDER": "openai", "RADAR_LLM_MODEL": "m"}, "OPENAI_API_KEY"),
        ({"RADAR_LLM_PROVIDER": "ollama"}, "RADAR_LLM_MODEL"),
        ({"RADAR_LLM_PROVIDER": "openai", "RADAR_LLM_MODEL": "m", "OPENAI_BASE_URL": "ftp://x"}, "http"),
    ],
)
def test_configuracao_incompleta_da_erro_claro(env, trecho):
    with pytest.raises(llm.ErroConfig, match=trecho):
        llm.provedor_do_ambiente(env)


def test_provedor_invalido_nao_repete_o_valor():
    with pytest.raises(llm.ErroConfig) as erro:
        llm.provedor_do_ambiente({"RADAR_LLM_PROVIDER": CHAVE})
    assert CHAVE not in str(erro.value) and "anthropic, openai, ollama" in str(erro.value)


def test_repr_nunca_mostra_a_chave():
    p = llm.provedor_do_ambiente({"ANTHROPIC_API_KEY": CHAVE})
    assert CHAVE not in repr(p) and CHAVE not in str(vars(p).get("modelo"))


# --- Formato das requisições ---------------------------------------------------------------


def test_requisicao_anthropic_com_schema():
    t = TransporteFalso(resposta_anthropic('{"titulo": "x"}'))
    p = llm.Anthropic(CHAVE, transporte=t)
    r = p.gerar("sistema", "usuario", esquema={"type": "object"})
    assert r == llm.Resposta('{"titulo": "x"}', "claude-sonnet-5-5")
    chamada = t.chamadas[0]
    assert chamada["url"] == "https://api.anthropic.com/v1/messages"
    assert chamada["headers"]["x-api-key"] == CHAVE
    assert chamada["headers"]["anthropic-version"] == "2023-06-01"
    corpo = chamada["corpo"]
    assert corpo["model"] == "claude-sonnet-5-5" and corpo["system"] == "sistema"
    assert corpo["messages"] == [{"role": "user", "content": "usuario"}]
    assert corpo["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "temperature" not in corpo and "thinking" not in corpo  # o Sonnet 5.5 recusa sampling fora do padrão


def test_requisicao_openai_compativel_com_base_url_e_sem_chave_local():
    t = TransporteFalso((200, {"model": "m", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}))
    p = llm.OpenAICompativel(None, "m", "http://localhost:1234/v1/", transporte=t)
    assert p.gerar("s", "u").texto == "{}"
    chamada = t.chamadas[0]
    assert chamada["url"] == "http://localhost:1234/v1/chat/completions"
    assert "authorization" not in chamada["headers"]
    assert chamada["corpo"]["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    t = TransporteFalso((200, {"choices": [{"message": {"content": "{}"}}]}))
    llm.OpenAICompativel(CHAVE, "m", transporte=t).gerar("s", "u")
    assert t.chamadas[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert t.chamadas[0]["headers"]["authorization"] == f"Bearer {CHAVE}"


def test_requisicao_ollama_com_host_sem_esquema_e_formato_json():
    t = TransporteFalso((200, {"model": "m", "message": {"role": "assistant", "content": "{}"}, "done": True}))
    p = llm.Ollama("m", "127.0.0.1:11434", transporte=t)
    p.gerar("s", "u", esquema={"type": "object"})
    chamada = t.chamadas[0]
    assert chamada["url"] == "http://127.0.0.1:11434/api/chat"
    assert chamada["corpo"]["stream"] is False and chamada["corpo"]["format"] == {"type": "object"}
    assert chamada["timeout"] == llm.TIMEOUT_OLLAMA_S
    assert llm.Ollama("m").host == "http://localhost:11434"


# --- Respostas e erros --------------------------------------------------------------------


@pytest.mark.parametrize(
    "dados, trecho",
    [
        ({"stop_reason": "refusal", "stop_details": {"category": "cyber"}, "content": []}, "recusou.*cyber"),
        ({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}, "cortada"),
        ({"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""}]}, "sem texto"),
    ],
)
def test_anthropic_recusa_corte_e_resposta_vazia(dados, trecho):
    p = llm.Anthropic(CHAVE, transporte=TransporteFalso((200, dados)))
    with pytest.raises(llm.ErroLLM, match=trecho):
        p.gerar("s", "u")


def test_repete_em_429_e_529_respeitando_retry_after():
    esperas = []
    t = TransporteFalso(
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "devagar"}}, {"retry-after": "7"}),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "sobrecarga"}}),
        resposta_anthropic(),
    )
    p = llm.Anthropic(CHAVE, transporte=t, dormir=esperas.append)
    assert p.gerar("s", "u").texto == '{"ok": true}'
    assert esperas == [7.0, llm.ESPERA_BASE_S * 3]


def test_erro_de_rede_repete_e_desiste_com_mensagem_clara():
    esperas = []
    falha = urllib.error.URLError("sem rota")
    p = llm.Anthropic(CHAVE, transporte=TransporteFalso(falha, falha, falha), dormir=esperas.append)
    with pytest.raises(llm.ErroLLM, match="falha de rede.*3 tentativas"):
        p.gerar("s", "u")
    assert len(esperas) == 2


def test_erro_4xx_nao_repete_e_nao_vaza_a_chave():
    # mesmo que a API devolvesse a chave na mensagem, ela seria trocada por ***
    t = TransporteFalso((401, {"type": "error", "error": {"type": "authentication_error", "message": f"chave {CHAVE} inválida"}}))
    p = llm.Anthropic(CHAVE, transporte=t, dormir=lambda s: pytest.fail("não deveria esperar"))
    with pytest.raises(llm.ErroLLM) as erro:
        p.gerar("s", "u")
    assert "HTTP 401" in str(erro.value) and CHAVE not in str(erro.value) and "***" in str(erro.value)
    assert len(t.chamadas) == 1


def test_openai_resposta_cortada_ou_recusada():
    cortada = (200, {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]})
    with pytest.raises(llm.ErroLLM, match="cortada"):
        llm.OpenAICompativel(CHAVE, "m", transporte=TransporteFalso(cortada)).gerar("s", "u")
    recusa = (200, {"choices": [{"message": {"content": None, "refusal": "não"}, "finish_reason": "stop"}]})
    with pytest.raises(llm.ErroLLM, match="recusou"):
        llm.OpenAICompativel(CHAVE, "m", transporte=TransporteFalso(recusa)).gerar("s", "u")


def test_modulo_so_usa_biblioteca_padrao():
    import ast
    from pathlib import Path

    arvore = ast.parse(Path(llm.__file__).read_text(encoding="utf-8"))
    modulos = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(arvore)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert modulos <= {"__future__", "json", "logging", "os", "time", "urllib", "collections", "dataclasses"}
