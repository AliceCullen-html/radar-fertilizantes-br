"""Fase 5: boletim mensal escrito por IA, com números inseridos e verificados pelo programa.

Fluxo:

1. Monta um "pacote de fatos" só com dados já calculados (data/processed/dashboard.json e
   troca_mensal.csv): chaves nomeadas, como troca_map_ultimo ou share_russia_k_ano, com
   valores já formatados em pt-BR e uma descrição de cada uma.
2. Pede à IA um JSON com título, lead e 4 a 6 parágrafos que citam números SÓ por
   marcadores {chave}. Número literal é proibido.
3. Valida a resposta, com a mesma regra para todos os provedores: todo marcador existe no
   pacote; fora dos marcadores, só letras e pontuação comum, sem dígito, número por extenso,
   nome de mês, contagem, recorde, unidade, sinal ou palavra de direção (a direção entra só
   pelos marcadores _sentido e _posicao, colados à própria variação); país, porto, grupo,
   produto e período escritos por extenso ligados aos números da frase; a relação de troca
   sempre "no porto" e nunca como preço ao produtor; texto numa linha só, sem Markdown, HTML ou
   link; sem os termos de uma lista fixa de recomendação e tom alarmista; e tamanho máximo. Se
   reprovar, tenta mais uma vez com os erros no prompt; se reprovar de novo, falha sem publicar.
4. Troca os marcadores pelos valores e grava boletins/AAAA-MM.md e boletins/latest.json.

--mock usa uma resposta fixa de exemplo, sem chamar nenhuma API, e grava em boletins/mock/
(fora do Git). Só biblioteca padrão: o job que recebe a chave não instala dependências.

Uso:
    python -m src.boletim --mock
    python -m src.boletim              # provedor de RADAR_LLM_PROVIDER (padrão: anthropic)
    python -m src.boletim --fatos      # só imprime o pacote de fatos

Fonte: Comex Stat/MDIC.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from . import config, llm

log = logging.getLogger(__name__)

RODAPE = "Texto gerado por IA a partir de dados do Comex Stat/MDIC; números inseridos e verificados automaticamente."
FONTE = "Comex Stat/MDIC"
# Fontes além do Comex Stat, pelo prefixo das chaves citadas no texto.
FONTE_PINK, FONTE_PTAX = "Banco Mundial (Pink Sheet)", "BCB/PTAX"
CHAVES_PINK = ("benchmark_", "base_benchmark_", "spread_")
CHAVES_PTAX = ("ptax_", "var_ptax_", "cif_brl_", "var_cif_brl_")
NOTA_BENCHMARK = (
    "Benchmark: Pink Sheet do Banco Mundial (CC BY 4.0), preço num porto de referência ou no destino e num"
    " momento de contrato diferentes dos do Brasil; o spread mistura origem, frete, prazo de contrato e"
    " defasagem e não é ágio."
)
NOTA_MAP = "O MAP é comparado com o DAP do Banco Mundial, que não publica série de MAP."
NOTA_PTAX = "CIF em reais pela PTAX venda média do mês (BCB, SGS 3698), e não pelo câmbio da data de cada declaração."
REPOSITORIO = "https://github.com/AliceCullen-html/radar-fertilizantes-br"
DASHBOARD = "https://alicecullen-html.github.io/radar-fertilizantes-br/"
NOTA_TROCA = (
    "Relação de troca medida no porto: soja exportada (FOB) x fertilizante importado (CIF). Não é o"
    " preço pago pelo produtor, que inclui frete interno, impostos e margens."
)

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
GRUPOS = {"TOTAL": "todos os grupos", "N": "nitrogenados (N)", "P": "fosfatados (P)", "K": "potássicos (K)", "NPK": "compostos (NPK)"}
PRODUTOS_TROCA = {"kcl": "KCl (cloreto de potássio)", "ureia": "ureia", "map": "MAP (fosfato monoamônico)"}
LIMIAR_HHI = 1800

# Limites do texto publicado (depois de trocar os marcadores pelos valores).
MAX_TITULO, MAX_LEAD, MAX_PARAGRAFO, MAX_TOTAL = 140, 450, 800, 4500
MIN_PARAGRAFOS, MAX_PARAGRAFOS = 4, 6
MIN_MARCADORES = 6
TENTATIVAS = 2  # a primeira e mais uma com os erros no prompt

ESQUEMA_RESPOSTA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "lead": {"type": "string"},
        "paragrafos": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["titulo", "lead", "paragrafos"],
    "additionalProperties": False,
}


class ErroBoletim(RuntimeError):
    """Dados insuficientes ou resposta reprovada na validação."""


# --- Formatação pt-BR ------------------------------------------------------------------------


def _arredondar(valor: float, casas: int = 0) -> Decimal:
    """Meio para cima, a partir do número como o JSON o grava (repr), como faz o Intl do
    navegador: o boletim e o dashboard mostram o mesmo número (396,5 vira 397 nos dois)."""
    absoluto = Decimal(repr(abs(float(valor)))).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    return -absoluto if valor < 0 else absoluto


def _num(valor: float, casas: int = 0) -> str:
    numero = _arredondar(valor, casas)
    texto = f"{abs(numero):,.{casas}f}"
    sinal = "-" if numero < 0 else ""
    return sinal + texto.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_t(t: float) -> str:
    if t >= 1e6:
        milhoes = t / 1e6
        return f"{_num(milhoes, 2)} {'milhão' if milhoes < 2 else 'milhões'} de t"
    if t >= 1e4:
        return f"{_num(t / 1e3)} mil t"
    if t >= 1e3:
        return f"{_num(t / 1e3, 1)} mil t"
    return f"{_num(t)} t"


def fmt_pct(valor: float) -> str:
    return f"{_num(valor, 1)}%"


def fmt_pp(valor: float) -> str:
    return f"{_num(valor, 1)} p.p."


def fmt_usd_t(valor: float) -> str:
    return f"US$ {_num(valor)}/t"


def fmt_brl_t(valor: float) -> str:
    return f"R$ {_num(valor)}/t"


def fmt_razao(valor: float) -> str:
    return f"{_num(valor, 2)} t de soja"


def mes_extenso(periodo: str) -> str:
    return f"{MESES[int(periodo[5:7]) - 1]} de {periodo[:4]}"


def mes_curto(periodo: str) -> str:
    return f"{MESES[int(periodo[5:7]) - 1][:3]}/{periodo[:4]}"


def sentido(variacao: float) -> str:
    """Direção de uma variação, para a IA não precisar (nem poder) inferir pelo sinal."""
    if abs(variacao) < 0.05:
        return "estabilidade"
    return "alta" if variacao > 0 else "queda"


def posicao(desvio_pct: float) -> str:
    if abs(desvio_pct) < 1:
        return "em linha com a média histórica"
    return "acima da média histórica" if desvio_pct > 0 else "abaixo da média histórica"


def posicao_benchmark(spread_pct: float) -> str:
    """Pelo valor já arredondado a uma casa, o mesmo que vai no texto: "1,0%" nunca sai "em linha"."""
    spread_pct = float(_arredondar(spread_pct, 1))
    if abs(spread_pct) < 1:
        return "em linha com o benchmark"
    return "acima do benchmark" if spread_pct > 0 else "abaixo do benchmark"


def slug(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


_ACENTOS = {"PARANAGUA": "Paranaguá", "SAO": "São", "LUIS": "Luís", "VITORIA": "Vitória", "MACEIO": "Maceió", "SANTAREM": "Santarém", "BELEM": "Belém"}
_MINUSCULAS = {"DE", "DO", "DA", "DOS", "DAS", "E"}
_SIGLAS = {"ALF", "IRF"}


def nome_urf(nome: str) -> str:
    """'PORTO DE PARANAGUA' -> 'Paranaguá'; 'IRF SAO LUIS' -> 'IRF São Luís' (como no dashboard)."""
    partes = re.split(r"(\s+|/|-)", nome)
    saida = []
    for i, parte in enumerate(partes):
        p = parte.upper()
        if not re.search(r"[A-Za-zÀ-ÿ]", parte):
            saida.append(parte)
        elif p in _SIGLAS:
            saida.append(p)
        elif p in _ACENTOS:
            saida.append(_ACENTOS[p])
        elif p in _MINUSCULAS and i > 0:
            saida.append(p.lower())
        else:
            saida.append(parte.capitalize())
    return re.sub(r"(^|- )Porto d[eo] ", r"\1", "".join(saida))


# --- Pacote de fatos -------------------------------------------------------------------------


class Fatos(dict):
    """chave -> {"valor": texto pt-BR, "descricao": o que é}. Chaves só com [a-z0-9_].

    `periodo` guarda o mês de referência (AAAA-MM). `entidades` liga cada chave aos países e
    portos a que o número se refere, e `outros_nomes` guarda países e portos dos dados que não
    estão no pacote: a validação confere os nomes escritos por extenso contra eles.
    """

    periodo = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entidades: dict[str, set[str]] = {}
        self.outros_nomes: set[str] = set()
        self.ordem: dict[str, int] = {}  # posição (1, 2, 3) da origem ou do porto de cada share
        self.lado_brasil: dict[str, str] = {}  # produto -> "FOB" ou "CIF": o preço do Brasil comparável ao benchmark

    def add(self, chave: str, valor: str | None, descricao: str, entidades: tuple[str, ...] = ()) -> None:
        if valor is None:
            return
        if not re.fullmatch(r"[a-z0-9_]+", chave):
            raise ValueError(f"chave inválida no pacote de fatos: {chave!r}")
        self[chave] = {"valor": valor, "descricao": descricao}
        if entidades:
            self.entidades[chave] = set(entidades)

    def add_var(self, chave: str, variacao: float | None, descricao: str, pp: bool = False, entidades: tuple[str, ...] = ()) -> None:
        """Variação sem sinal, mais a direção em {chave}_sentido."""
        if variacao is None:
            return
        self.add(chave, fmt_pp(abs(variacao)) if pp else fmt_pct(abs(variacao)), f"{descricao} (sem sinal; a direção vai em {chave}_sentido, colado a ele)", entidades)
        self.add(f"{chave}_sentido", sentido(variacao), f"Direção de {chave}: alta, queda ou estabilidade", entidades)

    def valores(self) -> dict[str, str]:
        return {k: v["valor"] for k, v in self.items()}


def _var_pct(atual: float | None, anterior: float | None) -> float | None:
    if atual is None or anterior is None or anterior <= 0:
        return None
    return 100 * (atual / anterior - 1)


def _soma(serie: list, indices: list[int]) -> float:
    return sum(serie[i] or 0 for i in indices)


def carregar(dados_dir: Path = config.PROCESSED_DIR) -> tuple[dict, list[dict]]:
    """dashboard.json e as linhas de troca_mensal.csv (vazias se o arquivo não existir)."""
    painel_arq = Path(dados_dir) / "dashboard.json"
    if not painel_arq.exists():
        raise ErroBoletim(f"{painel_arq} não existe. Rode antes: python -m src.transform")
    painel = json.loads(painel_arq.read_text(encoding="utf-8"))
    troca_arq = Path(dados_dir) / "troca_mensal.csv"
    troca = []
    if troca_arq.exists():
        with troca_arq.open(encoding="utf-8", newline="") as f:
            troca = list(csv.DictReader(f))
    return painel, troca


def montar_fatos(painel: dict, troca: list[dict], mes: str | None = None) -> Fatos:
    """Pacote de fatos do mês (padrão: o último com dados) e do acumulado do ano."""
    mensal, anual = painel["mensal"], painel["anual"]
    periodos = mensal["periodos"]
    mes = mes or periodos[-1]
    if mes not in periodos:
        raise ErroBoletim(f"{mes} não está nos dados ({periodos[0]} a {periodos[-1]}).")
    ano, m = int(mes[:4]), int(mes[5:7])
    i = periodos.index(mes)
    anterior = f"{ano - 1}-{m:02d}"
    i_ant = periodos.index(anterior) if anterior in periodos else None
    ytd = [j for j, p in enumerate(periodos) if p[:4] == str(ano) and int(p[5:7]) <= m]
    ytd_ant = [j for j, p in enumerate(periodos) if p[:4] == str(ano - 1) and int(p[5:7]) <= m]
    tem_ytd_ant = len(ytd_ant) == m and len(ytd) == m
    # "janeiro a setembro de 2026"; nas descrições: "de {acum}" e "sobre {acum_ant}"
    acum = f"janeiro a {MESES[m - 1]} de {ano}" if m > 1 else f"janeiro de {ano}"
    acum_ant = f"janeiro a {MESES[m - 1]} de {ano - 1}" if m > 1 else f"janeiro de {ano - 1}"

    f = Fatos()
    f.periodo = mes
    f.add("mes_referencia", mes_extenso(mes), "Mês de referência do boletim (último mês com dados)")
    f.add("mes_referencia_curto", mes_curto(mes), "Mês de referência, forma curta")
    f.add("ano_referencia", str(ano), "Ano do mês de referência")
    f.add("ano_anterior", str(ano - 1), "Ano anterior ao de referência")
    if i_ant is not None:
        f.add("mes_ano_anterior", mes_extenso(anterior), "Mesmo mês do ano anterior (base das variações mensais)")
    f.add("periodo_ytd", acum, "Acumulado do ano até o mês de referência")
    if tem_ytd_ant:
        f.add("periodo_ytd_anterior", acum_ant, "Mesmo período do ano anterior (base das variações do acumulado)")

    # --- Volumes, preços e frete por grupo
    t_ytd_total = _soma(mensal["TOTAL"]["t"], ytd)
    for g, nome in GRUPOS.items():
        s, k = mensal[g], g.lower()
        t_mes, t_ytd = s["t"][i], _soma(s["t"], ytd)
        if t_mes:
            f.add(f"t_{k}_mes", fmt_t(t_mes), f"Toneladas importadas em {mes_extenso(mes)}, {nome}")
        if i_ant is not None:
            f.add_var(f"var_t_{k}_mes", _var_pct(t_mes, s["t"][i_ant]), f"Variação das toneladas de {mes_extenso(mes)} sobre {mes_extenso(anterior)}, {nome}")
        if t_ytd:
            f.add(f"t_{k}_ytd", fmt_t(t_ytd), f"Toneladas importadas de {acum}, {nome}")
        if tem_ytd_ant:
            f.add_var(f"var_t_{k}_ytd", _var_pct(t_ytd, _soma(s["t"], ytd_ant)), f"Variação das toneladas de {acum} sobre {acum_ant}, {nome}")
        if g != "TOTAL" and t_ytd_total:
            f.add(f"share_{k}_ytd", fmt_pct(100 * t_ytd / t_ytd_total), f"Participação dos {nome} nas toneladas totais de {acum}")

        cif = lambda idx: (s["cif_musd"][idx] or 0) * 1e6 / s["t"][idx] if s["t"][idx] else None  # noqa: E731
        if cif(i) is not None:
            f.add(f"preco_cif_{k}_mes", fmt_usd_t(cif(i)), f"Preço médio CIF (valor ÷ toneladas) em {mes_extenso(mes)}, {nome}")
            if i_ant is not None:
                f.add_var(f"var_preco_cif_{k}_mes", _var_pct(cif(i), cif(i_ant)), f"Variação do preço CIF de {mes_extenso(mes)} sobre {mes_extenso(anterior)}, {nome}")

    t = mensal["TOTAL"]
    if t["t"][i]:
        f.add("preco_fob_total_mes", fmt_usd_t(t["fob_musd"][i] * 1e6 / t["t"][i]), f"Preço médio FOB em {mes_extenso(mes)}, todos os grupos")
        f.add("frete_total_mes", fmt_usd_t(t["frete_musd"][i] * 1e6 / t["t"][i]), f"Frete médio por tonelada em {mes_extenso(mes)}, todos os grupos")
    frete_ytd = _soma(t["frete_musd"], ytd) * 1e6 / t_ytd_total if t_ytd_total else None
    if frete_ytd is not None:
        f.add("frete_medio_ytd", fmt_usd_t(frete_ytd), f"Frete médio por tonelada de {acum}, todos os grupos")
        cif_ytd = _soma(t["cif_musd"], ytd)
        if cif_ytd:
            f.add("frete_pct_cif_ytd", fmt_pct(100 * _soma(t["frete_musd"], ytd) / cif_ytd), f"Peso do frete no valor CIF de {acum}")
    if tem_ytd_ant and frete_ytd is not None:
        t_ant = _soma(t["t"], ytd_ant)
        if t_ant:
            frete_ant = _soma(t["frete_musd"], ytd_ant) * 1e6 / t_ant
            f.add("frete_medio_ytd_anterior", fmt_usd_t(frete_ant), f"Frete médio por tonelada de {acum_ant}")
            f.add_var("var_frete_medio_ytd", _var_pct(frete_ytd, frete_ant), f"Variação do frete médio de {acum} sobre {acum_ant}")

    # --- Origens, concentração e portos no acumulado do ano. Esses blocos do dashboard.json
    # cobrem todos os meses do ano nos dados: só valem se o ano termina no mês pedido.
    anos = anual["anos"]
    ano_ate_o_mes = ano in anos and anual["n_meses"][anos.index(ano)] == m
    if ano in anos and not ano_ate_o_mes:
        log.info("Origens, HHI, portos e rotas ficam fora do pacote: os dados de %d vão além de %s.", ano, mes_extenso(mes))
    if ano_ate_o_mes:
        ia = anos.index(ano)
        f.add("limiar_hhi", _num(LIMIAR_HHI), "Limiar de HHI acima do qual um mercado é considerado altamente concentrado (diretrizes dos EUA de 2023)")
        for g, nome in GRUPOS.items():
            k = g.lower()
            linhas = [r for r in (painel["origens"]["dados"].get(str(ano), {}).get(g) or []) if r[1] != "Outros"]
            for pos, r in enumerate(linhas[:3]):
                pais, s_pais = r[1], slug(r[1])
                ordem = ["maior", "segunda maior", "terceira maior"][pos]
                if pos == 0:
                    f.add(f"lider_{k}_ano", pais, f"Maior país de origem de {acum}, {nome}", (pais,))
                    f.add(f"share_lider_{k}_ano", fmt_pct(r[3]), f"Participação de {pais}, maior origem, nas toneladas de {acum}, {nome}", (pais,))
                f.add(f"share_{s_pais}_{k}_ano", fmt_pct(r[3]), f"Participação de {pais} ({ordem} origem) nas toneladas de {acum}, {nome}", (pais,))
                f.ordem[f"share_{s_pais}_{k}_ano"] = pos + 1
                if pos == 0:
                    f.ordem[f"share_lider_{k}_ano"] = 1
                f.add_var(f"var_share_{s_pais}_{k}_ano", r[5], f"Variação da participação de {pais} sobre {acum_ant}, {nome}", pp=True, entidades=(pais,))
                f.add_var(f"var_t_{s_pais}_{k}_ano", r[4], f"Variação das toneladas vindas de {pais} sobre {acum_ant}, {nome}", entidades=(pais,))
            a = anual[g]
            if a["hhi"][ia] is not None:
                f.add(f"hhi_{k}_ano", _num(a["hhi"][ia]), f"Concentração de fornecedores (HHI, 0 a 10.000) de {acum}, {nome}")
                lado = "acima do limiar" if a["hhi"][ia] > LIMIAR_HHI else "abaixo do limiar" if a["hhi"][ia] < LIMIAR_HHI else "no limiar"
                f.add(f"hhi_{k}_ano_posicao", lado, f"Posição de hhi_{k}_ano frente ao limiar de alta concentração (limiar_hhi), colada a hhi_{k}_ano, {nome}")
                if ia > 0 and a["hhi"][ia - 1] is not None:
                    f.add(f"hhi_{k}_ano_anterior", _num(a["hhi"][ia - 1]), f"HHI do ano {ano - 1} inteiro, {nome}")
                f.add(f"cr3_{k}_ano", fmt_pct(a["cr3_pct"][ia]), f"Participação somada das três maiores origens de {acum}, {nome}")
                f.add(f"n_origens_{k}_ano", _num(a["n_origens"][ia]), f"Número de países de origem de {acum}, {nome}")
        portos = [r for r in (painel["urfs"]["dados"].get(str(ano), {}).get("TOTAL") or []) if r[1] != "Outros"]
        for pos, r in enumerate(portos[:3]):
            nome_p, s_p = nome_urf(r[1]), slug(nome_urf(r[1]))
            ordem = ["maior", "segunda maior", "terceira maior"][pos]
            if pos == 0:
                f.add("porto_lider_ano", nome_p, f"URF (porto) que mais recebeu fertilizantes de {acum}", (nome_p,))
                f.add("share_porto_lider_ano", fmt_pct(r[3]), f"Participação de {nome_p} nas toneladas de {acum}", (nome_p,))
            f.add(f"share_porto_{s_p}_ano", fmt_pct(r[3]), f"Participação da URF {nome_p} ({ordem}) nas toneladas de {acum}", (nome_p,))
            f.ordem[f"share_porto_{s_p}_ano"] = pos + 1
            if pos == 0:
                f.ordem["share_porto_lider_ano"] = 1
            f.add_var(f"var_share_porto_{s_p}_ano", r[5], f"Variação da participação de {nome_p} sobre {acum_ant}", pp=True, entidades=(nome_p,))

    # --- Rotas país de origem -> URF no acumulado do ano
    rotas = (painel.get("rotas") or {}).get("dados", {}).get(str(ano), {}).get("TOTAL")
    if ano_ate_o_mes and rotas and rotas.get("rotas"):
        for pos, r in enumerate(rotas["rotas"][:3], start=1):
            ponta = (r[0], nome_urf(r[2]))
            f.add(f"rota_{pos}_ano", f"{r[0]} → {nome_urf(r[2])}", f"Rota (país de origem → URF) número {pos} em toneladas de {acum}", ponta)
            f.add(f"t_rota_{pos}_ano", fmt_t(r[3]), f"Toneladas da rota {pos} de {acum}", ponta)
            if r[5] is not None:
                f.add(f"frete_rota_{pos}_ano", fmt_usd_t(r[5]), f"Frete médio por tonelada da rota {pos} de {acum}", ponta)
        com_frete = [r for r in rotas["rotas"] if r[5] is not None]
        if com_frete:
            caro, barato = max(com_frete, key=lambda r: r[5]), min(com_frete, key=lambda r: r[5])
            so_maiores = "entre as rotas de maior volume listadas no dashboard, não entre todas as rotas relevantes (n_rotas_relevantes_ano)"
            pontas_caro, pontas_barato = (caro[0], nome_urf(caro[2])), (barato[0], nome_urf(barato[2]))
            f.add("rota_frete_max_ano", f"{caro[0]} → {nome_urf(caro[2])}", f"Rota de maior frete por tonelada de {acum}, {so_maiores}", pontas_caro)
            f.add("frete_rota_max_ano", fmt_usd_t(caro[5]), f"Frete por tonelada da rota rota_frete_max_ano, {so_maiores}", pontas_caro)
            f.add("rota_frete_min_ano", f"{barato[0]} → {nome_urf(barato[2])}", f"Rota de menor frete por tonelada de {acum}, {so_maiores}", pontas_barato)
            f.add("frete_rota_min_ano", fmt_usd_t(barato[5]), f"Frete por tonelada da rota rota_frete_min_ano, {so_maiores}", pontas_barato)
        if rotas.get("frete_relevantes") is not None:
            f.add("frete_rotas_relevantes_ano", fmt_usd_t(rotas["frete_relevantes"]), f"Frete médio das rotas com volume relevante de {acum}")
        f.add("n_rotas_relevantes_ano", _num(rotas["n_relevantes"]), f"Rotas com volume relevante (acima do corte) de {acum}")
        f.add("n_rotas_ano", _num(rotas["n_rotas"]), f"Rotas com carga de {acum}")
        if rotas.get("share_relevantes_pct") is not None:
            f.add("share_rotas_relevantes_ano", fmt_pct(rotas["share_relevantes_pct"]), "Participação das rotas relevantes nas toneladas")
        if rotas.get("corte_t") is not None:
            f.add("corte_rotas_ano", fmt_t(rotas["corte_t"]), "Corte de volume para uma rota ser relevante no período")

    # --- Relação de troca no porto (soja FOB x fertilizante CIF), do troca_mensal.csv
    _fatos_troca(f, troca, mes)
    _fatos_benchmark(f, painel.get("benchmark"), mes)

    # Países e portos dos dados que não estão no pacote: citar algum deles reprova.
    no_pacote = set().union(*f.entidades.values()) if f.entidades else set()
    todos = {r[1] for por_ano in painel.get("origens", {}).get("dados", {}).values() for linhas in por_ano.values() for r in (linhas or [])}
    todos |= {nome_urf(r[1]) for por_ano in painel.get("urfs", {}).get("dados", {}).values() for linhas in por_ano.values() for r in (linhas or [])}
    f.outros_nomes = {n for n in todos if n and n != "Outros" and n not in no_pacote}
    return f


def _fatos_troca(f: Fatos, troca: list[dict], mes: str) -> None:
    nota = "relação de troca no porto (soja exportada FOB x fertilizante importado CIF), não é o preço ao produtor"

    def numero(texto: str) -> float | None:
        return float(texto) if texto not in ("", None) else None

    soja_registrada = False
    for prod, nome in PRODUTOS_TROCA.items():
        serie = [r for r in troca if r["produto"] == prod and numero(r["t_soja_por_t"]) is not None and r["periodo"] <= mes]
        atual = next((r for r in serie if r["periodo"] == mes), None)
        if not serie or atual is None:
            continue
        razoes = [numero(r["t_soja_por_t"]) for r in serie]
        media = sum(razoes) / len(razoes)
        valor = numero(atual["t_soja_por_t"])
        desvio = 100 * (valor / media - 1)
        f.add(f"troca_{prod}_ultimo", fmt_razao(valor), f"Toneladas de soja que pagam uma tonelada de {nome} em {mes_extenso(mes)}; {nota}")
        f.add(f"sacas_{prod}_ultimo", f"{_num(numero(atual['sacas_soja_por_t']), 1)} sacas de 60 kg", f"O mesmo, em sacas de soja de 60 kg, {nome}")
        f.add(f"cif_{prod}_ultimo", fmt_usd_t(numero(atual["preco_cif_fert_usd_t"])), f"Preço CIF de {nome} em {mes_extenso(mes)}")
        f.add(f"troca_{prod}_media", fmt_razao(media), f"Média histórica da relação de troca de {nome} ({serie[0]['periodo']} a {mes}, média simples dos meses)")
        f.add(f"desvio_troca_{prod}", fmt_pct(abs(desvio)), f"Distância da relação de {nome} em {mes_extenso(mes)} para a média histórica (sem sinal; posição em desvio_troca_{prod}_posicao)")
        f.add(f"desvio_troca_{prod}_posicao", posicao(desvio), f"Posição da relação de {nome} frente à média histórica, colada a desvio_troca_{prod}")
        lado = (valor > media) - (valor < media)
        seguidos = 0
        for r in reversed(razoes):
            if lado != 0 and ((r > media) - (r < media)) == lado:
                seguidos += 1
            else:
                break
        if seguidos:
            f.add(f"meses_seguidos_troca_{prod}", f"{seguidos} {'mês' if seguidos == 1 else 'meses'}", f"Meses seguidos, até {mes_extenso(mes)}, com a relação de {nome} do mesmo lado da média histórica (o lado vai em meses_seguidos_troca_{prod}_posicao, colado a ele)")
            f.add(f"meses_seguidos_troca_{prod}_posicao", "acima da média histórica" if lado > 0 else "abaixo da média histórica", f"Lado da média histórica em que a relação de {nome} ficou nesses meses seguidos")
        maior = max(serie, key=lambda r: numero(r["t_soja_por_t"]))
        f.add(f"troca_{prod}_max", fmt_razao(numero(maior["t_soja_por_t"])), f"Maior relação de troca de {nome} na série")
        f.add(f"troca_{prod}_max_mes", mes_extenso(maior["periodo"]), f"Mês da maior relação de troca de {nome} na série")
        anterior = next((r for r in serie if r["periodo"] == f"{int(mes[:4]) - 1}{mes[4:]}"), None)
        if anterior:
            f.add(f"troca_{prod}_ano_anterior", fmt_razao(numero(anterior["t_soja_por_t"])), f"Relação de troca de {nome} no mesmo mês do ano anterior")
        if not soja_registrada:
            soja_registrada = True
            soja = numero(atual["preco_fob_soja_usd_t"])
            f.add("soja_fob_ultimo", fmt_usd_t(soja), f"Preço FOB da soja em grão exportada em {mes_extenso(mes)}")
            if anterior:
                f.add_var("var_soja_fob_ano", _var_pct(soja, numero(anterior["preco_fob_soja_usd_t"])), "Variação do preço FOB da soja sobre o mesmo mês do ano anterior")
            f.add("periodo_media_troca", f"{mes_extenso(serie[0]['periodo'])} a {mes_extenso(mes)}", "Período da média histórica da relação de troca")


def _fatos_benchmark(f: Fatos, bm: dict | None, mes: str) -> None:
    """Brasil x Banco Mundial (bloco "benchmark" do dashboard.json) e a PTAX do mês."""
    if not bm or mes not in bm["periodos"]:
        return
    i = bm["periodos"].index(mes)
    anterior = f"{int(mes[:4]) - 1}{mes[4:]}"
    ia = bm["periodos"].index(anterior) if anterior in bm["periodos"] else None
    ptax = bm["ptax"][i]
    if ptax:
        f.add("ptax_mes", f"R$ {_num(ptax, 4)} por US$", f"PTAX venda média de {mes_extenso(mes)} (BCB, SGS 3698)")
        if ia is not None and bm["ptax"][ia]:
            f.add_var("var_ptax_ano", _var_pct(ptax, bm["ptax"][ia]), f"Variação da PTAX média de {mes_extenso(mes)} sobre {mes_extenso(anterior)} (alta = dólar mais caro em reais)")
    for prod, d in bm["produtos"].items():
        if d["amostra_pequena"][i] or d["bm"][i] is None or d["spread_pct"][i] is None:
            continue  # sem importação, amostra pequena ou sem benchmark: nada a comparar
        base = [b for b in d["bases"] if b["desde"] <= mes][-1]
        nome = d["nome"] if prod != "ureia" else "ureia"
        lado_brasil = "FOB" if base["comparacao"] == "FOB×FOB" else "CIF"
        comparacao = base["comparacao"].replace("×", " contra ")
        f.lado_brasil[prod] = lado_brasil
        ref = f"{d['serie']}, Banco Mundial, {base['base']}" + ("; referência do MAP por aproximação, o Banco Mundial não publica MAP" if d["proxy"] else "")
        ressalva = "Referência de outro lugar e de outro momento de contrato: o spread mistura origem, frete, prazo e defasagem, não é ágio."
        f.add(f"benchmark_{prod}_mes", fmt_usd_t(d["bm"][i]), f"Benchmark de {nome} em {mes_extenso(mes)} ({ref})")
        f.add(f"base_benchmark_{prod}", base["base"], f"Base do benchmark de {nome} em {mes_extenso(mes)} (comparação {comparacao}; o preço do Brasil comparável é o {lado_brasil})")
        if d["fob"][i] is not None:
            f.add(f"fob_brasil_{prod}_mes", fmt_usd_t(d["fob"][i]), f"Preço FOB implícito de {nome} importado pelo Brasil em {mes_extenso(mes)}")
        if d["cif"][i] is not None:
            f.add(f"cif_brasil_{prod}_mes", fmt_usd_t(d["cif"][i]), f"Preço CIF implícito de {nome} importado pelo Brasil em {mes_extenso(mes)}")
        lado = posicao_benchmark(d["spread_pct"][i])
        sobre = f"preço {lado_brasil} de {nome} importado pelo Brasil frente ao benchmark ({ref}) em {mes_extenso(mes)}, comparação {comparacao}"
        f.add(f"spread_{prod}_mes", fmt_pct(abs(d["spread_pct"][i])), f"Distância, em % do benchmark, do {sobre} (sem sinal; a posição vai em spread_{prod}_mes_posicao, colado a ele). {ressalva}")
        f.add(f"spread_{prod}_mes_posicao", lado, f"Posição do preço de {nome} do Brasil frente ao benchmark em {mes_extenso(mes)}")
        if d["spread_usd_t"][i] is not None:
            f.add(f"spread_{prod}_usd_mes", fmt_usd_t(abs(d["spread_usd_t"][i])), f"A mesma distância em US$/t (sem sinal; a posição vai em spread_{prod}_usd_mes_posicao, colado a ele)")
            f.add(f"spread_{prod}_usd_mes_posicao", lado, f"Posição do preço de {nome} do Brasil frente ao benchmark em {mes_extenso(mes)}")
        if ia is not None and not d["amostra_pequena"][ia] and d["spread_pct"][ia] is not None:
            base_ant = [b for b in d["bases"] if b["desde"] <= anterior]
            if base_ant and base_ant[-1]["base"] == base["base"]:  # só na mesma base do benchmark
                # O spread do ano anterior com a sua própria posição, e não uma variação: "alta"
                # do spread com sinal seria lida como distância maior quando ela diminuiu.
                f.add(f"spread_{prod}_ano_anterior", fmt_pct(abs(d["spread_pct"][ia])), f"A mesma distância em {mes_extenso(anterior)}, na mesma base (sem sinal; a posição vai em spread_{prod}_ano_anterior_posicao, colado a ele)")
                f.add(f"spread_{prod}_ano_anterior_posicao", posicao_benchmark(d["spread_pct"][ia]), f"Posição do preço de {nome} do Brasil frente ao benchmark em {mes_extenso(anterior)}")
        if d["cif_brl_t"][i] is not None:
            f.add(f"cif_brl_{prod}_mes", fmt_brl_t(d["cif_brl_t"][i]), f"Preço CIF de {nome} em reais por tonelada em {mes_extenso(mes)} (CIF em US$ × PTAX média do mês)")
            if ia is not None and d["cif_brl_t"][ia] and not d["amostra_pequena"][ia]:
                f.add_var(f"var_cif_brl_{prod}", _var_pct(d["cif_brl_t"][i], d["cif_brl_t"][ia]), f"Variação do CIF em reais de {nome} sobre {mes_extenso(anterior)}")


# --- Prompt -------------------------------------------------------------------------------

SISTEMA = """Você é analista de inteligência de mercado e escreve o boletim mensal do Radar das importações brasileiras de fertilizantes, com dados públicos do Comex Stat/MDIC, do Banco Mundial (Pink Sheet) e do Banco Central (PTAX).

Você recebe um PACOTE DE FATOS em JSON: cada chave tem um valor já formatado e uma descrição. Escreva um boletim curto em português do Brasil a partir desses fatos.

Regras invioláveis. Um programa confere a resposta e a reprova se qualquer uma for descumprida:
1. Todo número entra SOMENTE como marcador {chave}, com a chave exatamente como está no pacote. O programa troca o marcador pelo valor. Isso vale para anos, meses, percentuais, toneladas, preços, contagens e sacas. Nunca escreva o nome de um mês: use {mes_referencia}, {mes_ano_anterior}, {periodo_ytd} e afins.
2. Nenhum dígito fora dos marcadores, nem números por extenso (dois, três, dez, mil, milhões, zero, meia, dobro, dobrou, triplicou, metade, um terço...), algarismos romanos ou posições além de "terceiro". Também não escreva contagens, recordes nem períodos que o pacote não traz: "terceiro mês seguido", "consecutivo", "pela primeira vez", "recorde", "inédito", "o maior da série", "nunca antes", semestre, trimestre, década, "comparação mensal", "mês anterior".
3. Os valores já trazem a unidade (%, t, US$/t, p.p.). Não escreva %, "por cento", US$, "toneladas" ou p.p. junto de um marcador, nem sinal (+, −, "menos") antes de um marcador, nem dois marcadores colados.
4. A direção entra SÓ pelos marcadores com sufixo _sentido (alta, queda ou estabilidade) e _posicao (acima ou abaixo de uma referência), sempre colados à sua variação: "{var_x_sentido} de {var_x}" ou "{desvio_x} {desvio_x_posicao}". Toda variação (var_) e toda distância (desvio_ e spread_) vai com o seu _sentido ou _posicao ao lado. Fora dos marcadores, NÃO escreva palavras de direção ou comparação: subiu, caiu, cresceu, recuou, avançou, superou, aumento, redução, alta, queda, acima, abaixo, aquém, maior que, menor que, mais caro, mais barato, melhorou, piorou, favorável, estável, elevado, forte, leve, significativo, manteve, segue, novamente, retomada e afins. Nunca negue nem inverta uma direção ("não houve {var_x_sentido}", "reverteu a {var_x_sentido}").
5. Use só os fatos do pacote. Não cite causas, contexto, eventos, empresas, regiões, previsões, recordes, picos nem continuidade ("manteve a liderança", "segue como principal") que não estejam nele. Ao escrever o nome de um país, porto, grupo (nitrogenados, fosfatados, potássicos, compostos, ou o total dos fertilizantes) ou produto (ureia, KCl, MAP), os números da mesma frase precisam ser dele: use as chaves dele. Ponha cada número logo depois do nome a que ele se refere ("Santos recebeu {a}, e Paranaguá, {b}"), nunca em lista no fim ("Santos e Paranaguá receberam {a} e {b}"). "Principal", "líder", "segunda maior" e "terceira maior" só para a posição que o pacote dá. Cada número no seu período: o do mês com {mes_referencia}, o do acumulado com {periodo_ytd}; as variações do mês são sobre o mesmo mês do ano anterior ({mes_ano_anterior}) e as do acumulado, sobre {periodo_ytd_anterior}. Não cite países ou portos que não estão no pacote. O spread compara o preço do Brasil com o benchmark do Banco Mundial, uma referência de outro lugar e de outro momento de contrato: não escreva ágio, prêmio, desconto, sobrepreço nem margem, nem para negar; o programa acrescenta as notas de método. O benchmark é o preço de referência do Banco Mundial, não o que o Brasil pagou: diga "benchmark" ou "Banco Mundial" na frase que o cita e, para o MAP, diga que a referência é o DAP. Cada spread vai com o preço do Brasil da sua base (o FOB ou o CIF que a descrição de base_benchmark_ indica), nunca com o outro. Não escreva "série". Câmbio, dólar e PTAX só na frase com {ptax_mes} ou {var_ptax_ano} (alta = dólar mais caro em reais; não fale do real). A relação de troca é US$ por US$: nunca a ponha em reais nem junto da PTAX.
6. Tom de analista: neutro e objetivo, sem recomendação de compra ou venda e sem adjetivos alarmistas (como alarmante, explosão, disparada, colapso, catastrófico, crítico, preocupante).
7. Todo título, lead ou parágrafo que citar a relação de troca (chaves troca_, sacas_, desvio_troca_ e meses_seguidos_troca_) precisa dizer nele mesmo que ela é medida "no porto". Não fale em produtor, agricultor ou fazenda: o programa acrescenta a ressalva de que ela não é o preço ao produtor (se quiser repeti-la, escreva exatamente "não é o preço ao produtor").
8. Texto simples, cada campo numa linha só: só letras, espaços e pontuação comum. Sem Markdown, HTML, links, emojis, setas, listas, asteriscos, colchetes ou "&".

Formato: responda SOMENTE com um objeto JSON com as chaves "titulo" (até 100 caracteres), "lead" (uma ou duas frases, até 350 caracteres) e "paragrafos" (lista de 4 a 6 parágrafos curtos, cada um com até 600 caracteres).

Exemplos de frases válidas: "As importações somaram {t_total_mes} em {mes_referencia}, {var_t_total_mes_sentido} de {var_t_total_mes} sobre {mes_ano_anterior}." e "No porto, uma tonelada de MAP equivalia a {troca_map_ultimo}, {desvio_troca_map} {desvio_troca_map_posicao}."
"""


def montar_prompt(fatos: Fatos, erros: list[str] | None = None, anterior: str | None = None) -> str:
    pacote = json.dumps(fatos, ensure_ascii=False, indent=1)
    texto = f"PACOTE DE FATOS:\n{pacote}\n\nEscreva o boletim de {fatos['mes_referencia']['valor']} seguindo as regras."
    if erros:
        lista = "\n".join(f"- {e}" for e in erros)
        texto += (
            "\n\nSua resposta anterior foi REPROVADA pela validação automática pelos motivos abaixo."
            f" Corrija todos e responda de novo com o JSON completo.\nMotivos:\n{lista}"
        )
        if anterior:
            texto += f"\n\nResposta anterior:\n{anterior[:6000]}"
    return texto


# --- Validação (igual para todos os provedores) --------------------------------------------

MARCADOR = re.compile(r"\{([^{}]*)\}")
CHAVE_VALIDA = re.compile(r"[a-z0-9_]+")
# Fora dos marcadores, só letras do português (Latin-1, depois de NFC) e esta pontuação. O resto
# (emoji, seta, sinal matemático, caractere invisível ou combinante, letra de outro alfabeto,
# "&", "@"...) reprova.
LETRA = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
PONTUACAO = set(" \u00a0.,;:!?()\"'“”‘’«»-–—/")
POR_EXTENSO = re.compile(
    r"\b(zero|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|quinze|"
    r"dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|"
    r"cem|cento|duzent\w*|trezent\w*|quatrocent\w*|quinhent\w*|seiscent\w*|setecent\w*|oitocent\w*|novecent\w*|"
    r"mil|milhar|milh[ãa]o|milh[õo]es|bilh[ãa]o|bilh[õo]es|trilh[ãa]o|trilh[õo]es|mi|bi|tri|porcento|dobro|metade|"
    r"meia|meio\s+(?:ponto|por\s*cento|milh\w*|bilh\w*)|e\s+meio|dezenas?|centenas?|milhares|ter[çc]os?|d[uú]zias?|"
    # multiplicativos e ordinais além do terceiro (o pacote só traz as três maiores posições)
    r"dobr(?:ou|aram|am|ando|ado|ada|ados|adas|ar|ará|aria)|duplic\w*|triplic\w*|quadruplic\w*|quintuplic\w*|\w*[uú]pl[oa]s?|"
    r"quart[oa]s?|quint[oa]s?|sext[oa]s?|s[eé]tim[oa]s?|oitav[oa]s?|non[oa]s?|d[eé]cim[oa]s?|\w*g[eé]sim[oa]s?|"
    r"cent[eé]sim\w*|mil[eé]sim\w*|"
    # inglês
    r"one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b",
    re.IGNORECASE,
)
HOMOGLIFOS = re.compile(r"\b[lIO0]{2,}\b")  # "lOO" no lugar de 100; sensível a maiúsculas
# Algarismos romanos com duas letras ou mais (XXI, IV...), em maiúsculas; MDIC não casa.
ROMANOS = re.compile(r"\b(?=[MDCLXVI]{2,}\b)M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})(?<=[MDCLXVI])\b")
# Meses e períodos só entram por marcador ({mes_referencia}, {periodo_ytd}...).
MESES_E_PERIODOS = re.compile(
    r"\b(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
    r"semestr\w*|trimestr\w*|bimestr\w*|quinzena\w*|d[eé]cadas?|dec[eê]nios?|s[eé]culos?|bi[eê]nios?|tri[eê]nios?|retrasad\w*)\b",
    re.IGNORECASE,
)
MES_ANTERIOR = re.compile(
    r"\bm[eê]s\s+(?:\w+\s+)?(?:anterior|passado|precedente|pr[eé]vio)\b|\bm[eê]s\s+que\s+passou\b|\b(?:compara[çc][ãa]o|varia[çc][ãa]o)\s+mensal\b|"
    r"\bna\s+margem\b|\b[uú]ltimos\s+meses\b",
    re.IGNORECASE,
)
# Contagens e recordes que o pacote não traz (a única contagem é meses_seguidos_troca_*).
CONTAGENS = re.compile(
    r"\b((?:primeir|segund|terceir)[oa]s?\s+(?:m[eê]s|meses|anos?|vez|vezes|trimestres?|semestres?|semanas?|altas?|quedas?|aumentos?|recuos?)|"
    r"(?:m[eê]s|meses|anos?|vezes|trimestres?|semestres?|semanas?|altas?|quedas?)\s+(?:seguid|consecutiv)\w*|"
    r"consecutiv\w*|em\s+sequ[eê]ncia|recordes?|in[eé]dit\w*|nunca\s+antes|j[aá]\s+(?:registrad|vist)\w*|"
    r"sem\s+precedentes?|n[ãa]o\s+tem\s+precedentes?)\b",
    re.IGNORECASE,
)
# "o maior da série", "máxima histórica": só com as chaves que trazem o máximo e a média da série.
HISTORICO = re.compile(r"\b(?:s[eé]rie|hist[oó]ria)\b|\bm[aá]xim[ao]s?\s+hist[oó]ric\w*|\bm[ií]nim[ao]s?\s+hist[oó]ric\w*", re.IGNORECASE)
CHAVES_HISTORICO = re.compile(r"^(troca_[a-z]+_(max|max_mes|media)|periodo_media_troca)$")
# Palavras de direção e de comparação: a direção entra só pelos marcadores _sentido e _posicao.
_ANTES_SUPERLATIVO = r"(?<!\bo )(?<!\ba )(?<!\bos )(?<!\bas )"
_DIRECAO = [
    # para cima
    r"alta(?!\s+concentra)", r"altas", r"aument\w*", r"cresc\w*", r"subi\w*", r"sob(?:e|em)", r"avan[çc]\w*", r"expans\w*",
    r"expandi\w*", r"elev(?:a|am|ou|aram|ando|ação|ações)", r"ganh\w*", r"acr[eé]scim\w*", r"valoriz\w*", r"encarec\w*",
    r"salt(?:a|am|o|os|ou|aram)", r"increment\w*", r"recupera\w*", r"positiv\w*", r"amplia\w*", r"ampliou",
    r"super(?:a|am|ou|aram|ando|ar)", r"ultrapass\w*", r"exced\w*", r"pul(?:a|am|ou|aram)", r"acelera\w*", r"multiplic\w*",
    r"engord\w*", r"escal(?:a|am|ou|aram|ada)",
    # para baixo
    r"queda", r"quedas", r"ca[ií](?:u|ram|r|a|am|em|ndo|do|da|dos|das)?", r"caem",
    r"recu(?:a|am|e|em|o|os|ou|aram|ar|ando|ada|ado|ava|avam)", r"redu(?:z|zem|ziu|ziram|zir|zindo|ção|ções|zida|zido|zidas|zidos)",
    r"diminu\w*", r"retra(?:i|em|ção|ções|iu|íram|ída|ído|indo)", r"perd\w*", r"baix(?:a(?!\s+concentra)|as|am|ou|aram)",
    r"decl[ií]n\w*", r"desvaloriz\w*", r"barate\w*", r"encolh\w*", r"negativ\w*", r"al[ií]vio", r"tomb(?:a|am|o|os|ou|aram)",
    r"ced(?:e|em|eu|eram)", r"contra[íi]\w*", r"contra[çc][ãa]o", r"enfraquec\w*", r"desc(?:e|em|eu|eram|ida|idas|endo)",
    r"cort(?:a|am|ou|aram)", r"afund\w*", r"mingu\w*", r"desacelera\w*", r"emagrec\w*",
    # posição e comparação
    r"acima", r"abaixo", r"aqu[eé]m", r"superior\w*", r"inferior\w*", r"pior(?:ou|aram|a|am)", r"melhor(?:ou|aram|a|am)",
    r"desfavor[aá]ve(?:l|is)", r"favor[aá]ve(?:l|is)",
    _ANTES_SUPERLATIVO + r"(?<!\bde )(?<!segunda )(?<!segundo )(?<!terceira )(?<!terceiro )(?:maior|menor|pior|melhor)(?:es)?",
    _ANTES_SUPERLATIVO + r"(?:mais|menos)\s+(?:car|barat|alt|baix|elevad|fort|frac|concentrad|competitiv)\w*",
    r"a\s+(?:mais|menos)\s+(?:que|do\s+que|sobre|frente|em\s+rela\w*)",
]
DIRECAO = re.compile(r"\b(?:" + "|".join(_DIRECAO) + r")\b", re.IGNORECASE)
UNIDADES = re.compile(r"%|US\$|R\$|\bp\.\s?p\.|\bpor\s*cento\b|\bpontos?\s+percentua\w*", re.IGNORECASE)
LINKS = re.compile(r"https?:|://|\bwww\.|\S@\S|\.(?:com|br|org|net|gov|io)\b|\w\.[^\W\d_]{2,}", re.IGNORECASE)
MARCACAO = re.compile(r"[*#`<>\[\]|_~{}&]|^\s*[-+]\s|:[a-z0-9+-]+:", re.IGNORECASE)
PROIBIDOS = re.compile(
    r"\b(recomend\w*|aconselh\w*|sugerimos|sugere-se|compre|comprem|venda agora|vender agora|oportunidade de compra|"
    r"vale\s+a\s+pena|antecipar\s+(?:as\s+)?compras|hora\s+de\s+(?:comprar|vender)|"
    r"momento\s+(?:é\s+)?(?:favor[aá]vel|ideal|oportuno|certo)\s+para|aproveit(?:ar|e|em)\b|prop[ií]ci\w*|conv[ée]m|"
    r"dev(?:e|em)\s+(?:comprar|vender|travar|antecipar|aguardar|adiar)|trav(?:ar|e|em)\s+(?:o\s+)?pre[çc]o|"
    r"alarmant\w*|catastr[oó]f\w*|desastr\w*|colaps\w*|explos\w*|explod\w*|dispar(ou|aram|ada|adas|ado|ados)|"
    r"despenc\w*|desab(ou|aram)|assustador\w*|dram[aá]tic\w*|dr[aá]stic\w*|brusc\w*|crise|p[aâ]nico|caos|ca[oó]tic\w*|"
    r"vertiginos\w*|preocupant\w*|cr[ií]tic[oa]s?|grav[ií]ssim\w*)\b",
    re.IGNORECASE,
)
AVISO_SEM_RECOMENDACAO = re.compile(
    r"\bn[ãa]o\s+(?:traz|faz|cont[eé]m|constitui|é)\s+(?:nenhuma\s+)?recomenda[çc](?:ão|ões)\s+de\s+compra\s+(?:ou|e)\s+venda\b",
    re.IGNORECASE,
)
GRUDADOS = re.compile(r"\}[.,]?\{")
COLADO_AO_MARCADOR = re.compile(r"[^\W_]\{|\}[^\W_]")
SEPARADOR_DE_NUMEROS = re.compile(r"^(?:\s+|[-‐‑−–/:'’])$")
# Marcadores que são só número (HHI, contagens, anos) não levam unidade inventada.
UNIDADE_DEPOIS = re.compile(
    r"^\s*(?:(?:de\s+)?(?:t|toneladas?|kg|quilos?|d[oó]lar\w*|usd|reais|sacas?)\b|(?P<curta>[^\W\d_]{1,3})\b|"
    r"(?:euros?|libras?|ienes?|yuans?|rublos?|navios?|cargas?|carregamentos?|embarques?|cont[eê]ineres?|anos?|meses|dias?|"
    r"semanas?|empresas?|importadores?|vezes|pa[ií]ses|origens|rotas|portos?)\b)",
    re.IGNORECASE,
)
# Palavras curtas que podem vir depois de um número puro ("{hhi_k_ano} no ano"); o resto ("Mt", "kt", "mil") reprova.
PALAVRAS_CURTAS = {"no", "na", "nos", "nas", "em", "e", "de", "do", "da", "dos", "das", "ao", "à", "o", "a", "os", "as", "ou", "que", "com", "por", "se", "foi", "é", "um", "uma"}
UNIDADE_ANTES = re.compile(r"(?:\busd|\bd[oó]lar\w*|\$|\beur|\beuros?|\blibras?|\bienes?|\byuans?)\s*$", re.IGNORECASE)
# Contagens só com o seu substantivo: {n_origens_k_ano} países, {n_rotas_ano} rotas.
SUBSTANTIVO_DA_CONTAGEM = {
    "n_origens_": re.compile(r"^\s+(?:pa[ií]ses|origens|fornecedores)\b", re.IGNORECASE),
    "n_rotas": re.compile(r"^\s+rotas\b", re.IGNORECASE),
}
AGIO = re.compile(
    r"\b(?:des)?[áa]gios?\b|\bsobre-?pre[çc]os?\b|\bsub-?pre[çc]os?\b|\bpr[êe]mios?\b|\bpremiums?\b|\bdescontos?\b|\bmark-?ups?\b|"
    r"\bsobre-?custos?\b|\bsobretaxas?\b|\bsobrevalor\w*|\boverprice\w*|\bmarge(?:m|ns)\b|\bspread\s+de\s+compra\b",
    re.IGNORECASE,
)
CITA_TROCA = re.compile(r"\brela[çc][ãa]o\s+de\s+troca\b|\bpoder\s+de\s+compra\b", re.IGNORECASE)
CHAVES_TROCA = re.compile(r"^(troca|sacas|desvio_troca|meses_seguidos_troca)_")
NO_PORTO = re.compile(r"\bno\s+porto\b", re.IGNORECASE)
PRODUTOR = re.compile(
    r"\b(produtor\w*|agricultor\w*|fazendas?|fazendeir\w*|lavouras?|sojicultor\w*|cooperad[oa]s?|porteira|pecuarist\w*|rura(?:l|is))\b",
    re.IGNORECASE,
)
RESSALVA = re.compile(
    r"\bn[ãa]o\s+(?:é|representa|equivale\s+a|corresponde\s+a|reflete)\s+(?:o\s+)?(?:pre[çc]o|custo)\s+(?:pago\s+)?(?:ao|pelo|do)\s+$",
    re.IGNORECASE,
)
FIM_DE_FRASE = re.compile(r"[.!?;]")
# Grupos e produtos escritos por extenso, e a parte da chave que diz de qual grupo ou produto é.
GRUPOS_TEXTO = {
    "n": re.compile(r"\bnitrogenad\w*|\bnitrog[eê]ni\w*", re.IGNORECASE),
    "p": re.compile(r"\bfosfatad\w*|\bfosfatos?\b(?!\s+(?:mono|di)am)|\bf[oó]sfor\w*", re.IGNORECASE),
    "k": re.compile(r"(?<!cloreto\sde\s)\bpot[aá]ss\w*", re.IGNORECASE),
    "npk": re.compile(r"\bcompost[oa]s?\b|\bNPK\b"),
}
GRUPO_DA_CHAVE = re.compile(r"(?:^|_)(n|p|k|npk|total)_(?:mes|ytd|ano)(?:_|$)")
PRODUTOS_TEXTO = {
    "ureia": re.compile(r"\bur[eé]ia\b", re.IGNORECASE),
    "kcl": re.compile(r"\bKCl\b|\bcloreto\s+de\s+pot[aá]ssio\b", re.IGNORECASE),
    "map": re.compile(r"\bMAP\b|\bmonoam[ôo]nico\b"),
    "dap": re.compile(r"\bDAP\b|\bdiam[ôo]nico\b"),
    "tsp": re.compile(r"\bTSP\b|\bsuperfosfato\s+triplo\b"),
}
PRODUTO_DA_CHAVE = re.compile(r"(?:^|_)(kcl|ureia|map|dap|tsp)(?:_|$)")
PASSADO = {"mes_ano_anterior", "periodo_ytd_anterior", "ano_anterior"}
# Valores sem sinal: a direção vem do _sentido ou da _posicao colado a eles.
MAGNITUDES = ("var_", "desvio_", "spread_")
MODELO_VALIDO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}")


def extrair_json(texto: str) -> dict:
    """O objeto JSON da resposta (o último com "titulo", "lead" ou "paragrafos"), tolerando cercas
    de código, texto em volta e raciocínio em <think>...</think>."""
    texto = re.sub(r"<(think|thinking|reasoning)>.*?</\1>", "", texto, flags=re.DOTALL | re.IGNORECASE).strip()
    cerca = re.match(r"^```(?:json)?\s*(.*?)\s*```$", texto, re.DOTALL)
    if cerca:
        texto = cerca.group(1)
    decodificador, achado, primeiro_erro = json.JSONDecoder(), None, None
    for chave in re.finditer(r"\{", texto):
        try:
            dados, _ = decodificador.raw_decode(texto, chave.start())
        except json.JSONDecodeError as erro:
            primeiro_erro = primeiro_erro or erro
            continue
        if isinstance(dados, dict) and {"titulo", "lead", "paragrafos"} & dados.keys():
            achado = dados
    if achado is not None:
        return achado
    if primeiro_erro is not None:
        raise primeiro_erro
    raise ValueError("a resposta não contém um objeto JSON")


def _uma_linha(texto: str) -> str:
    """Escapa quebras de linha: no log do Actions, uma linha que começa com '::' vira comando."""
    return texto.replace("\r", "\\r").replace("\n", "\\n")


def _trecho(texto: str, inicio: int, fim: int) -> str:
    return "…" + _uma_linha(texto[max(0, inicio - 25) : fim + 25]) + "…"


def _apagar_marcadores(texto: str) -> str:
    """Troca cada marcador por espaços do mesmo tamanho: as posições continuam valendo."""
    return MARCADOR.sub(lambda m: " " * len(m.group(0)), texto)


def _numerico(chave: str, fatos: Fatos) -> bool:
    return chave in fatos and any(c.isdigit() for c in fatos[chave]["valor"])


def _direcao_de(chave: str, fatos: Fatos) -> str | None:
    """Para uma variação (var_) ou distância (desvio_), a chave _sentido ou _posicao que a acompanha."""
    for sufixo in ("_sentido", "_posicao"):
        if f"{chave}{sufixo}" in fatos:
            return f"{chave}{sufixo}"
    return None


TRACOS = "-‐‑‒–−˗⁻"


def _sinal_antes(prefixo: str, variacao: bool = True) -> str | None:
    """O sinal antes de um marcador numérico ("-{x}", "- {x}", "+ {x}", "menos {x}"), ou None.
    O travessão com espaço ("— {x}") é pontuação, e o traço logo depois de outro marcador
    ("{a}-{b}") é faixa. Numa variação, qualquer outro traço conta como sinal; nos demais
    números, o traço com espaço só conta depois de uma preposição ("de - {x}")."""
    p = unicodedata.normalize("NFKC", prefixo)
    if not p:
        return None
    if p[-1] in TRACOS or unicodedata.category(p[-1]) == "Sm" or p[-1] in "+±":
        if len(p) >= 2 and p[-2] == "}" and p[-1] in TRACOS:
            return None
        return p[-1]
    sem_espaco = p.rstrip()
    if sem_espaco and sem_espaco != p:
        if sem_espaco[-1] in "+±" or unicodedata.category(sem_espaco[-1]) == "Sm":
            return sem_espaco[-1]
        if sem_espaco[-1] in TRACOS and (variacao or re.search(rf"\b(?:de|em|a|para|por|com|sobre|entre)\s*[{TRACOS}]$", sem_espaco, re.IGNORECASE)):
            return sem_espaco[-1]
    achado = re.search(r"\b(menos|mais)\s*$", p, re.IGNORECASE)
    return achado.group(1) if achado else None


def _erros_do_texto(campo: str, texto: str, fatos: Fatos) -> tuple[list[str], int]:
    erros = []
    marcadores = MARCADOR.findall(texto)
    desconhecidos = [m for m in marcadores if not CHAVE_VALIDA.fullmatch(m) or m not in fatos]
    if desconhecidos:
        lista = ", ".join(repr("{" + m + "}") for m in sorted(set(desconhecidos))[:10])
        erros.append(f"{campo}: marcadores que não existem no pacote: {lista}")
    resto = MARCADOR.sub(" ", texto)
    numerico = next((i for i, ch in enumerate(resto) if unicodedata.numeric(ch, None) is not None), None)
    if numerico is not None:  # dígitos de qualquer escrita, sobrescritos (²), frações (½), Ⅻ...
        erros.append(f"{campo}: dígito fora de marcador em {_trecho(resto, numerico, numerico + 1)!r}")
    estranho = next((ch for ch in resto if not (LETRA.fullmatch(ch) or ch in PONTUACAO or ch.isdigit())), None)
    if estranho is not None:
        nome = unicodedata.name(estranho, "sem nome")
        erros.append(
            f"{campo}: caractere fora do permitido (U+{ord(estranho):04X} {nome}); use só letras, espaços e pontuação"
            " comum, numa linha só, sem emoji, seta, símbolo, Markdown, '&' ou '@'"
        )
    if not re.search(r"[^\W\d_]", resto):
        erros.append(f"{campo}: só marcadores, sem texto")
    normal = unicodedata.normalize("NFKC", resto)
    for padrao, motivo in [
        (ROMANOS, "algarismo romano"),
        (POR_EXTENSO, "número por extenso"),
        (HOMOGLIFOS, "letras no lugar de algarismos"),
        (MESES_E_PERIODOS, "mês ou período por extenso (use {mes_referencia}, {mes_ano_anterior}, {periodo_ytd}...)"),
        (MES_ANTERIOR, "as variações do mês são sobre o mesmo mês do ano anterior ({mes_ano_anterior}), não sobre o mês anterior"),
        (CONTAGENS, "contagem, sequência ou recorde que o pacote não traz"),
        (UNIDADES, "unidade (%, por cento, US$ ou p.p.) fora de marcador: o valor já traz a unidade"),
        (LINKS, "link, endereço ou menção"),
        (MARCACAO, "caractere de marcação, '&' ou início de lista"),
        (PROIBIDOS, "termo proibido (recomendação ou tom alarmista)"),
        (AGIO, "o spread mistura origem, frete, prazo de contrato e defasagem: não escreva ágio, prêmio, desconto ou sobrepreço, nem para negar"),
    ]:
        alvo = AVISO_SEM_RECOMENDACAO.sub(" ", normal) if padrao is PROIBIDOS else normal
        achado = padrao.search(alvo)
        if achado:
            erros.append(f"{campo}: {motivo} em {_trecho(alvo, achado.start(), achado.end())!r}")
    direcao = DIRECAO.search(normal)
    if direcao:
        erros.append(
            f"{campo}: palavra de direção ou comparação '{direcao.group(0)}' fora dos marcadores, em"
            f" {_trecho(normal, direcao.start(), direcao.end())!r}. A direção entra só pelo marcador _sentido"
            " (\"{var_x_sentido} de {var_x}\") ou _posicao (\"{desvio_x} {desvio_x_posicao}\"); reescreva sem a palavra"
        )
    if GRUDADOS.search(texto):
        achado = GRUDADOS.search(texto)
        erros.append(f"{campo}: dois marcadores colados em {_trecho(texto, achado.start() - 20, achado.end() + 20)!r}; separe-os com texto")
    if colado := COLADO_AO_MARCADOR.search(texto):
        erros.append(f"{campo}: letra ou algarismo colado a um marcador em {_trecho(texto, colado.start(), colado.end())!r}; separe com espaço")
    marcas = [(m.start(), m.end(), m.group(1)) for m in MARCADOR.finditer(texto) if m.group(1) in fatos]
    for (_, fim1, k1), (ini2, _, k2) in zip(marcas, marcas[1:]):
        vao = texto[fim1:ini2]
        if _numerico(k1, fatos) and _numerico(k2, fatos) and SEPARADOR_DE_NUMEROS.match(vao) and not (k1.startswith("ano_") and k2.startswith("ano_")):
            erros.append(f"{campo}: dois números separados só por {vao!r} em {{{k1}}}{vao}{{{k2}}}; ligue-os com texto")
    for achado in MARCADOR.finditer(texto):
        chave = achado.group(1)
        if chave not in fatos:
            continue
        if _numerico(chave, fatos):
            sinal = _sinal_antes(texto[: achado.start()], chave.startswith(MAGNITUDES))
            if sinal:
                erros.append(f"{campo}: '{sinal}' antes de {{{chave}}}; o valor vai sem sinal e a direção vem do seu _sentido ou _posicao")
        if re.fullmatch(r"[\d.]+", fatos[chave]["valor"]):
            antes, depois = _apagar_marcadores(texto[: achado.start()]), _apagar_marcadores(texto[achado.end() :])
            contagem = next((rx for prefixo, rx in SUBSTANTIVO_DA_CONTAGEM.items() if chave.startswith(prefixo)), None)
            unidade = None if contagem else UNIDADE_DEPOIS.match(depois)
            if contagem and not contagem.match(depois):
                erros.append(f"{campo}: {{{chave}}} é uma contagem; escreva logo depois o que ela conta, como em \"{{{chave}}} {'rotas' if 'rotas' in chave else 'países'}\"")
            elif (unidade and not (unidade.group("curta") and unidade.group("curta").lower() in PALAVRAS_CURTAS)) or UNIDADE_ANTES.search(antes):
                erros.append(f"{campo}: unidade inventada junto de {{{chave}}}, que é só um número ({fatos[chave]['valor']})")
    cita_troca = any(CHAVES_TROCA.match(m) for m in marcadores) or CITA_TROCA.search(normal)
    if cita_troca and not NO_PORTO.search(normal):
        erros.append(f"{campo}: cita a relação de troca sem dizer, no mesmo campo, que ela é medida no porto")
    for achado in PRODUTOR.finditer(normal):
        if not RESSALVA.search(normal[: achado.start()]):
            erros.append(
                f"{campo}: '{achado.group(0)}' em {_trecho(normal, achado.start(), achado.end())!r}. Não fale em produtor:"
                " o programa acrescenta a ressalva; se quiser repeti-la, escreva exatamente 'não é o preço ao produtor'"
            )
            break
    erros += _ligacoes(campo, texto, fatos)
    erros += _frases(campo, texto, fatos)
    erros += _conferencias(campo, texto, fatos)
    erros += _conferencias_benchmark(campo, texto, fatos)
    return erros, len(marcadores)


def _ligacoes(campo: str, texto: str, fatos: Fatos) -> list[str]:
    """Cada _sentido ou _posicao colado à sua variação (e cada variação ou distância com o seu),
    sem outro marcador entre eles: "{var_x_sentido} de {var_x}" ou "{desvio_x} {desvio_x_posicao}"."""
    erros = []
    marcas = [(m.start(), m.end(), m.group(1)) for m in MARCADOR.finditer(texto) if m.group(1) in fatos]

    def colado(i: int, alvo: str) -> bool:
        for j in (i - 1, i + 1):
            if 0 <= j < len(marcas) and marcas[j][2] == alvo:
                vao = texto[min(marcas[i][1], marcas[j][1]) : max(marcas[i][0], marcas[j][0])]
                if len(vao) <= 30 and not FIM_DE_FRASE.search(vao):
                    return True
        return False

    for i, (_, _, chave) in enumerate(marcas):
        if chave.endswith(("_sentido", "_posicao")):
            base = chave.rsplit("_", 1)[0]
            if base in fatos and not colado(i, base):
                exemplo = f"{{{chave}}} de {{{base}}}" if chave.endswith("_sentido") else f"{{{base}}} {{{chave}}}"
                erros.append(f"{campo}: {{{chave}}} precisa vir colado a {{{base}}}, sem outro marcador entre eles, como em \"{exemplo}\"")
        elif chave.startswith(MAGNITUDES) and _direcao_de(chave, fatos) and not colado(i, _direcao_de(chave, fatos)):
            erros.append(f"{campo}: {{{chave}}} vai sem sinal; ponha {{{_direcao_de(chave, fatos)}}} colado a ele para dar a direção")
    return erros


def _frases(campo: str, texto: str, fatos: Fatos) -> list[str]:
    """Nomes de países, portos, grupos e produtos, e o período, ligados aos números da frase."""
    erros = []
    limpo = _apagar_marcadores(texto)
    inicio = 0
    for fim in [m.end() for m in FIM_DE_FRASE.finditer(limpo)] + [len(limpo)]:
        frase = unicodedata.normalize("NFC", limpo[inicio:fim])
        chaves = [m.group(1) for m in MARCADOR.finditer(texto) if inicio <= m.start() < fim and m.group(1) in fatos]
        inicio = fim
        numeros = [k for k in chaves if _numerico(k, fatos)]
        trecho = frase.strip()[:80]
        # países e portos
        citados = set().union(*(fatos.entidades.get(k, set()) for k in chaves)) if chaves else set()
        for nome in sorted(fatos.outros_nomes):
            if re.search(rf"(?<!\w){re.escape(nome)}(?!\w)", frase):
                erros.append(f"{campo}: '{nome}' não está no pacote de fatos; cite só países e portos do pacote")
        if numeros:
            no_pacote = set().union(*fatos.entidades.values()) if fatos.entidades else set()
            for nome in sorted(no_pacote - citados):
                if re.search(rf"(?<!\w){re.escape(nome)}(?!\w)", frase):
                    erros.append(f"{campo}: a frase cita '{nome}', mas nenhum número dela é de {nome} em {trecho!r}; use as chaves de {nome}")
        # grupos
        grupos_chaves = {g.group(1) for k in chaves if (g := GRUPO_DA_CHAVE.search(k))}
        grupos_texto = {g for g, rx in GRUPOS_TEXTO.items() if rx.search(frase)}
        # na ida, o produto conta pelo seu grupo: "os nitrogenados ficaram {spread_kcl_mes}" reprova
        grupos_dos_numeros = grupos_chaves | {GRUPO_DO_PRODUTO[p.group(1)] for k in chaves if (p := PRODUTO_DA_CHAVE.search(k))}
        if numeros and grupos_dos_numeros - {"total"}:
            for g in sorted(grupos_texto - grupos_dos_numeros):
                erros.append(f"{campo}: a frase fala dos {_NOME_GRUPO[g]}, mas nenhum número dela é dos {_NOME_GRUPO[g]} em {trecho!r}")
            if grupos_texto:
                for g in sorted(grupos_chaves - grupos_texto - {"total"}):
                    erros.append(f"{campo}: {_NOME_GRUPO[g]} ganham números de outro grupo em {trecho!r}; cada grupo com as suas chaves")
        # produtos
        produtos_chaves = {g.group(1) for k in chaves if (g := PRODUTO_DA_CHAVE.search(k))}
        if PROXY_DO_MAP & set(chaves):
            produtos_chaves.add("dap")  # o benchmark do MAP é o do DAP: a frase precisa dizer isso
        for prod, rx in PRODUTOS_TEXTO.items():
            if numeros and rx.search(frase) and prod not in produtos_chaves:
                erros.append(f"{campo}: a frase fala de {prod.upper() if prod != 'ureia' else 'ureia'}, mas nenhum número dela é desse produto em {trecho!r}")
        # período: valor do período atual numa frase sobre o passado
        if PASSADO & set(chaves):
            atuais = [k for k in numeros if re.search(r"_(mes|ytd)$", k) and not k.startswith("var_")]
            comparacao = [k for k in chaves if k.startswith("var_") or (k.endswith("_anterior") and k not in PASSADO)]
            if atuais and not comparacao:
                erros.append(f"{campo}: {{{atuais[0]}}} é do período atual, mas a frase o põe no período anterior em {trecho!r}")
        # recorde e máximo da série só com as chaves que os trazem
        if HISTORICO.search(frase) and not any(CHAVES_HISTORICO.match(k) for k in chaves):
            erros.append(f"{campo}: 'série' ou 'história' só com {{troca_*_max}}, {{troca_*_media}} ou {{periodo_media_troca}}, que trazem esses valores")
    return erros


_NOME_GRUPO = {"n": "nitrogenados", "p": "fosfatados", "k": "potássicos", "npk": "compostos", "total": "todos os grupos"}
GRUPO_DO_PRODUTO = {"kcl": "k", "ureia": "n", "map": "p", "dap": "p", "tsp": "p"}
# O MAP é comparado com o DAP do Banco Mundial: essas chaves vão junto da palavra DAP.
PROXY_DO_MAP = {"benchmark_map_mes", "base_benchmark_map"}


# --- Conferências de ligação e de conteúdo -------------------------------------------------
# Cada número ligado ao grupo, produto, país, porto, posição, período e métrica que ele mede, e
# nada que o pacote não traz: continuidade, recorde, causa, previsão, região, produto de fora,
# juízo de intensidade ou de concentração, negação de uma direção, nome próprio desconhecido.

CORTE_ORACAO = re.compile(r"[,;:—–()]")
# Menções de grupo (e do total) e de produto, para ligar cada número à mais próxima.
MENCOES_GRUPO = {
    "n": re.compile(r"\bnitrogenad\w*", re.IGNORECASE),
    "p": re.compile(r"\bfosfatad\w*", re.IGNORECASE),
    "k": re.compile(r"(?<!cloreto\sde\s)\bpot[aá]ss\w*", re.IGNORECASE),
    "npk": re.compile(r"\bcompost[oa]s?\b|\bNPK\b"),
    "total": re.compile(
        r"\btodos\s+os\s+grupos\b|\btota(?:l|is)\b|\bconjunto\b|\bfertilizantes\b|\badubos\b"
        r"|\bimporta[çc][õo]es\s+brasileiras\b|\bo\s+Brasil\s+importou\b",
        re.IGNORECASE,
    ),
}
MENCOES_PRODUTO = {
    "ureia": re.compile(r"\bur[eé]ia\b", re.IGNORECASE),
    "kcl": re.compile(r"\bKCl\b|\bMOP\b|\bcloreto\b", re.IGNORECASE),
    "map": re.compile(r"\bMAP\b|\bmonoam[ôo]nico\b"),
    "dap": re.compile(r"\bDAP\b|\bdiam[ôo]nico\b"),
    "tsp": re.compile(r"\bTSP\b|\bsuperfosfato\s+triplo\b", re.IGNORECASE),
}
PRODUTOS_FORA = re.compile(r"\b(?:superfosfato\s+simples|SSP|sulfat\w*|nitrat\w*|am[ôo]nia\b)", re.IGNORECASE)
CHAVE_DE_PRODUTO = re.compile(
    r"^(?:troca|sacas|cif|desvio_troca|meses_seguidos_troca|benchmark|base_benchmark|fob_brasil|cif_brasil|spread|var_spread|cif_brl|var_cif_brl)"
    r"_(kcl|ureia|map|dap|tsp)(?:_|$)"
)
CHAVE_DO_TOTAL = re.compile(
    r"^(?:var_)?(?:frete_(?!rota_)|n_rotas|share_rotas|corte_rotas|frete_rotas|t_rota_|frete_rota_|share_porto|var_share_porto|preco_fob)"
)
# Países e portos escritos de outro jeito, e regiões que o pacote não traz.
APELIDOS = [
    (re.compile(r"\bbielorr[uú]ss\w*|\bbelarus\w*", re.IGNORECASE), "Belarus"),
    (re.compile(r"\bholand\w*|\bpa[ií]ses\s+baixos\b|\bneerland\w*", re.IGNORECASE), "Países Baixos (Holanda)"),
    (re.compile(r"\bqatar\w*|\bcatar\w*", re.IGNORECASE), "Catar"),
    (re.compile(r"\bkuwait\w*|\bkuweit\w*|\bcoveit\w*", re.IGNORECASE), "Coveite (Kuweit)"),
    (re.compile(r"\bEUA\b|\bnorte-american\w*|\bamerican[oa]s?\b|\bestadunidens\w*"), "Estados Unidos"),
    (re.compile(r"\bruss[oa]s?\b", re.IGNORECASE), "Rússia"),
    (re.compile(r"\bchin[eê]s\w*|\bchinesa\w*", re.IGNORECASE), "China"),
    (re.compile(r"\bcanadens\w*", re.IGNORECASE), "Canadá"),
    (re.compile(r"\bmarroquin\w*", re.IGNORECASE), "Marrocos"),
    (re.compile(r"\beg[ií]pci\w*", re.IGNORECASE), "Egito"),
    (re.compile(r"\bnigerian\w*", re.IGNORECASE), "Nigéria"),
    (re.compile(r"\bturcomen\w*", re.IGNORECASE), "Turcomenistão"),
    (re.compile(r"\bitaqui\b", re.IGNORECASE), "IRF São Luís"),
]
REGIOES = re.compile(
    r"\b(?:Oriente\s+M[eé]dio|Golfo|Mar\s+Negro|Mar\s+Vermelho|B[aá]ltico|Europa|europe[iu]\w*|[AÁ]sia|asi[aá]tic\w*|"
    r"[AÁ]frica|african\w*|Am[eé]rica\s+do\s+Norte|Am[eé]rica\s+Latina|Suez|Leste\s+Europeu|Ocidente|ocidenta\w*)\b",
    re.IGNORECASE,
)
# Maiúsculas fora do início de frase: só nomes do pacote e estas siglas e palavras.
MAIUSCULAS_PERMITIDAS = {
    "Brasil", "Comex", "Stat", "MDIC", "HHI", "CIF", "FOB", "NPK", "KCl", "MAP", "URF", "URFs", "IRF", "ALF", "Porto",
    "N", "P", "K", "Herfindahl", "Hirschman", "Receita", "Federal",
    # fase 6: as fontes do benchmark e do câmbio e as siglas das bases
    "Banco", "Mundial", "Pink", "Sheet", "PTAX", "BCB", "Central", "CFR", "DAP", "TSP",
}
# Período: palavras que marcam o mês, o acumulado e o passado.
PALAVRA_MES = re.compile(r"\b(?:no|do)\s+m[eê]s\b(?!\s+(?:anterior|passado))", re.IGNORECASE)
PALAVRA_YTD = re.compile(r"\bacumulad\w*|\b(?:no|do)\s+ano\b(?!\s+(?:anterior|passado|inteiro))", re.IGNORECASE)
MESMO_PERIODO = re.compile(r"\bmesmo\s+per[ií]odo\b|\bigual\s+per[ií]odo\b", re.IGNORECASE)
PASSADO_ESCRITO = re.compile(r"\b(?:ano|per[ií]odo|exerc[ií]cio|acumulado)\s+(?:\w+\s+)?(?:passado|anterior)\b", re.IGNORECASE)
COMO_BASE = re.compile(r"\b(?:sobre|frente|ante|contra|rela[çc][ãa]o|compara\w*|versus)\b[^.;!?]{0,40}$", re.IGNORECASE)
CONTINUIDADE = re.compile(
    r"\b(?:mant(?:eve|êm|em|inha|inham|iveram|ém|endo|ida|ido|idas|idos)|segue|seguem|seguiu|seguiram|continu\w*|permanec\w*|"
    r"novamente|de\s+novo|mais\s+uma\s+vez|outra\s+vez|volt(?:a|am|ou|aram)\s+a|retom\w*|revert\w*)\b",
    re.IGNORECASE,
)
RECORDE = re.compile(
    r"\b(?:nunca|pico|picos|topo|[aá]pice|piso|em\s+anos|h[aá]\s+anos|desde|j[aá]\s+(?:observad|alcan[çc]ad|atingid|verificad|medid)\w*)\b",
    re.IGNORECASE,
)
SUPERLATIVO = re.compile(
    r"\b[oa]s?\s+(?:maior|menor|mais\s+alt\w*|mais\s+baix\w*)(?:es)?\s+(?:n[ií]ve(?:l|is)|patamar\w*|valor\w*|volume\w*|pre[çc]o\w*|frete\w*|rela[çc][ãa]o|quantidade)\b"
    r"|\bs[eé]rie\b|\bhist[oó]ria\b|\bm[aá]xim[ao]s?\b|\bm[ií]nim[ao]s?\b",
    re.IGNORECASE,
)
RANKING = re.compile(
    r"\b(?:principal|principais|l[ií]der\w*|lidera\w*|(?<!segunda\s)(?<!segundo\s)(?<!terceira\s)(?<!terceiro\s)maior\s+(?:origem|fornecedor\w*)|"
    r"que\s+mais\s+(?:recebeu|vendeu|forneceu|enviou))\b",
    re.IGNORECASE,
)
SEGUNDA = re.compile(r"\bsegund[oa]\s+maior\b|\bsegund[oa]\s+(?:origem|fornecedor\w*|lugar|posi[çc][ãa]o)\b|\bvice\b", re.IGNORECASE)
TERCEIRA = re.compile(r"\bterceir[oa]\s+maior\b|\bterceir[oa]\s+(?:origem|fornecedor\w*|lugar|posi[çc][ãa]o)\b", re.IGNORECASE)
QUANTIFICADOR = re.compile(r"\b(?:[uú]nic[oa]s?|maioria|minoria|a\s+maior\s+parte|maior\s+parte\s+d)\b", re.IGNORECASE)
JUIZO = re.compile(
    r"\b(?:est[aá]ve(?:l|is)|estabilidade|estabiliz\w*|inalterad\w*|igua(?:l|is)(?!\s+per[ií]odo)|semelhante\w*|similar\w*|praticamente|"
    r"em\s+linha|alinhad\w*|pr[oó]xim[oa]s?\s+(?:a|ao|à|aos|às|do|da|dos|das|de)\b|[oa]s?\s+mesm[oa]s?\s+que|a\s+(?:mais|menos)\b(?!\s+recente)|"
    r"aquec\w*|esfri\w*|arrefec\w*|elevad[oa]s?|alt[oa]s?(?!\s+concentra)|(?<!pa[ií]ses\s)baix[oa]s?(?!\s+concentra)|car[oa]s?|barat[oa]s?|"
    r"fortes?|fortemente|frac[oa]s?|firmes?|diversific\w*|mergulh\w*|pression\w*|aprofund\w*|agrav\w*|abrand\w*|intensific\w*|"
    r"significativ\w*|expressiv\w*|leve|ligeir\w*|modest\w*|acentuad\w*|tant[oa]s?|"
    r"oneros\w*|custos[oa]s?|em\s+conta|acess[ií]ve\w*|salgad\w*|do\s+que|poder\s+de\s+compra|respectivamente|"
    r"\w*cresc\w*|decr[eé]scim\w*|(?:a|re)baix(?:a|as|am|ou|aram|ando|ad[oa]s?)|alto|infl(?:ou|aram|a|am|ando|ad[oa]s?)|"
    r"fortalec\w*|retroced\w*|retrocess\w*|deterior\w*|zer(?:ou|aram|ad[oa]s?|ando|ar)|"
    r"\w+(?:ou|aram|iu|iram)\s+(?:mais|menos)|"
    r"(?:custa|custam|custava|compra|compram|paga|pagava|vale|valia|rende)\s+(?:mais|menos))\b",
    re.IGNORECASE,
)
INTENSIDADE_DO_SENTIDO = re.compile(
    r"\{[a-z0-9_]+_sentido\}\s+(?:relevante|significativ|expressiv|fortes?\b|leve|ligeir|modest|acentuad|marginal|t[ií]mid)", re.IGNORECASE
)
NEGACAO = re.compile(
    r"\b(?:n[ãa]o|nem|sem|nunca|jamais|nenhum\w*|nada|revert\w*|invert\w*|anul\w*|desf(?:ez|izeram|az|azem|eit[oa]s?)|contr[áa]ri\w*|"
    r"opost\w*|longe\s+de|em\s+vez|ao\s+inv[ée]s|interromp\w*|compens\w*|neutraliz\w*|elimin\w*|apag(?:ou|aram|a|am|ad[oa]s?))\b",
    re.IGNORECASE,
)
RESSALVA_INTEIRA = re.compile(
    r"\bn[ãa]o\s+(?:é|representa|equivale\s+a|corresponde\s+a|reflete)\s+(?:o\s+)?(?:pre[çc]o|custo)\s+(?:pago\s+)?(?:ao|pelo|do)\s+produtor\b",
    re.IGNORECASE,
)
CAUSA = re.compile(
    r"\b(?:reflexo\w*|reflet\w*|devid[oa]s?\s+a|por\s+causa|em\s+raz[ãa]o|gra[çc]as\s+a|puxad\w*|impulsionad\w*|"
    r"em\s+meio\s+a|diante\s+d\w*|motivad\w*|explicad\w*|tend[eê]nci\w*|tende\w*|pr[oó]ximos\s+meses|expectativ\w*|proje[çc]\w*|"
    r"perspectiv\w*|dev(?:e|em|er[áa]|er[ãa]o)\s+\w+(?:ar|er|ir)|ser[áa]|ser[ãa]o|haver[áa]|\w{2,}(?:ar|er|ir)á|\w{2,}(?:ar|er|ir)ão|"
    r"san[çc]\w*|guerra\w*|conflit\w*|estoques?|petr[óo]leo|g[áa]s\s+natural|plantio\w*|"
    r"demanda\w*|oferta\w*|clim[aá]\w*|chuvas?|greves?|tarif\w*|embarg\w*)\b",
    re.IGNORECASE,
)
PRODUTOR_AMPLO = re.compile(
    r"\b(?:quem\s+(?:planta|plantam|produz|produzem|cultiva\w*)|plantador\w*|custo\s+de\s+produ[çc][ãa]o|campo|agro|agroneg[oó]ci\w*|"
    r"agropecu\w*|safras?|safrinha|cooperativ\w*|revend\w*|distribuidor\w*|interior)\b",
    re.IGNORECASE,
)
CONCENTRACAO = re.compile(
    r"\balta\s+concentra\w*|\baltamente\s+concentrad\w*|\bconcentrad[oa]s?\b|\bdesconcentr\w*|\bbaixa\s+concentra\w*|\bpouco\s+concentrad\w*",
    re.IGNORECASE,
)
METRICAS = {
    "volume": re.compile(r"\bimporta[çc][õo]es\b|\bcompras\b|\bvolume\b|\btoneladas\b|\bdesembarques\b", re.IGNORECASE),
    "preco": re.compile(r"\bpre[çc]o\w*|\bCIF\b|\bFOB\b|\bcusto\b|\bvalor\b", re.IGNORECASE),
    "share": re.compile(r"\bparticipa[çc][ãa]o\b|\bfatia\b|\bpeso\b", re.IGNORECASE),
    "frete": re.compile(r"\bfrete\b", re.IGNORECASE),
}
ANAFORA_DE_GRUPO = re.compile(r"\b(?:o|do|no|ao|nesse|neste|desse|deste|naquele)\s+grupo\b", re.IGNORECASE)
CHAVES_FRETE_EXTREMO = {"rota_frete_max_ano", "frete_rota_max_ano", "rota_frete_min_ano", "frete_rota_min_ano"}


def _grupo_da_chave(k: str) -> str | None:
    if CHAVE_DE_PRODUTO.match(k):
        return None
    g = GRUPO_DA_CHAVE.search(k)
    if g:
        return g.group(1)
    return "total" if CHAVE_DO_TOTAL.match(k) else None


def _produto_da_chave(k: str) -> str | None:
    m = CHAVE_DE_PRODUTO.match(k)
    return m.group(1) if m else None


def _metrica(k: str) -> str | None:
    base = k[4:] if k.startswith("var_") else k
    if base.startswith("t_"):
        return "volume"
    if base.startswith(("preco_", "cif_", "soja_fob")):
        return "preco"
    if base.startswith("share_"):
        return "share"
    if base.startswith("frete_"):
        return "frete"
    return None


def _classe_periodo(k: str) -> str | None:
    """De que período é a chave: âncora atual (a_mes, a_ytd) ou passada, variação mensal ou do
    acumulado, valor do período anterior, valor da série histórica ou valor atual."""
    if k in ("mes_referencia", "mes_referencia_curto"):
        return "a_mes"
    if k in ("periodo_ytd", "ano_referencia"):
        return "a_ytd"
    if k in PASSADO:
        return "a_passado"
    if k in ("var_soja_fob_ano", "var_ptax_ano") or k.startswith(("var_spread_", "var_cif_brl_")) or (k.startswith("var_") and k.endswith("_mes")):
        return "var_mes"  # todas sobre o mesmo mês do ano anterior
    if k.startswith("var_") and k.endswith(("_ytd", "_ano")):
        return "var_ytd"
    if k.startswith("hhi_") and k.endswith("_ano_anterior"):
        return "prev_full"
    if re.fullmatch(r"(?:troca|spread)_\w+_ano_anterior", k):
        return "prev_mes"
    if k.endswith("_ytd_anterior"):
        return "prev_ytd"
    if re.fullmatch(r"troca_\w+_(?:media|max|max_mes)|periodo_media_troca|meses_seguidos_\w+", k):
        return "hist"
    if k.endswith(("_mes", "_ultimo")) or k.startswith("desvio_troca_"):
        return "mes"
    if k.endswith(("_ytd", "_ano")):
        return "ytd"
    return None


def _dobrar(texto: str) -> str:
    """Sem acento e em minúsculas, com o mesmo tamanho: 'Paranagua' e 'CHINA' casam com os nomes."""
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return re.sub(r"\s", " ", texto).casefold()


def _mencoes_de_entidade(frase: str, ini: int, texto: str, fatos: Fatos, fim: int) -> list[tuple[int, int, frozenset, str]]:
    """(início, fim, entidades, origem) dos países e portos citados na frase: por extenso (sem
    acento e sem caixa), por apelido ou por um marcador de nome ({lider_k_ano}, {rota_1_ano})."""
    achados = []
    nomes = (set().union(*fatos.entidades.values()) if fatos.entidades else set()) | fatos.outros_nomes
    dobrada = _dobrar(frase) if len(_dobrar(frase)) == len(frase) else None  # "ß" muda o tamanho: aí, sem dobrar
    for nome in nomes:
        alvo = dobrada if dobrada is not None else frase
        padrao = re.escape(_dobrar(nome) if dobrada is not None else nome)
        for m in re.finditer(rf"(?<!\w){padrao}(?!\w)", alvo):
            achados.append((ini + m.start(), ini + m.end(), frozenset({nome}), "texto"))
    for rx, nome in APELIDOS:
        for m in rx.finditer(frase):
            achados.append((ini + m.start(), ini + m.end(), frozenset({nome}), "apelido"))
    for m in MARCADOR.finditer(texto):
        k = m.group(1)
        if ini <= m.start() < fim and k in fatos.entidades and not _numerico(k, fatos):
            achados.append((m.start(), m.end(), frozenset(fatos.entidades[k]), "marcador:" + k))
    achados.sort(key=lambda a: (a[0], -(a[1] - a[0])))
    saida, fim_anterior = [], -1
    for a in achados:  # menção contida em outra conta uma vez só
        if a[0] >= fim_anterior:
            saida.append(a)
            fim_anterior = a[1]
    return saida


def _mais_proxima(mencoes, ini_m: int, fim_m: int, limpo: str, preferir_antes: bool = True, fora_da_oracao: bool = False):
    """A menção anterior mais próxima; sem anterior, a posterior mais próxima na mesma oração."""
    antes = [m for m in mencoes if m[1] <= ini_m]
    if antes and preferir_antes:
        return max(antes, key=lambda m: m[1])
    depois = [m for m in mencoes if m[0] >= fim_m and not CORTE_ORACAO.search(limpo[fim_m : m[0]])]
    if depois:
        return min(depois, key=lambda m: m[0])
    if antes:
        return max(antes, key=lambda m: m[1])
    if fora_da_oracao:
        depois = [m for m in mencoes if m[0] >= fim_m]
        return min(depois, key=lambda m: m[0]) if depois else None
    return None


def _conferencias(campo: str, texto: str, fatos: Fatos) -> list[str]:
    erros = []
    limpo = _apagar_marcadores(texto)
    normal = unicodedata.normalize("NFKC", MARCADOR.sub(" ", texto))
    chaves_campo = [m.group(1) for m in MARCADOR.finditer(texto)]
    for padrao, motivo in [
        (CONTINUIDADE, "continuidade ou repetição que o pacote não traz (manteve, segue, novamente, retomada)"),
        (RECORDE, "recorde, pico ou comparação com anos passados que o pacote não traz"),
        (QUANTIFICADOR, "'única', 'maioria' ou 'maior parte' não vêm do pacote"),
        (JUIZO, "palavra de direção, comparação ou juízo fora dos marcadores"),
        (CAUSA, "causa, previsão ou contexto que o pacote não traz"),
        (PRODUTOR_AMPLO, "os preços e a relação de troca são no porto, não o custo de quem planta"),
        (REGIOES, "região ou lugar fora do pacote"),
        (PRODUTOS_FORA, "produto fora do pacote"),
    ]:
        achado = padrao.search(normal)
        if achado and not (padrao is CONTINUIDADE and any(k.startswith("meses_seguidos_troca_") for k in chaves_campo)):
            erros.append(f"{campo}: {motivo}: {_trecho(normal, achado.start(), achado.end())!r}")
    if INTENSIDADE_DO_SENTIDO.search(texto):
        erros.append(f"{campo}: juízo de intensidade colado ao _sentido; a direção vai sem adjetivo")
    for m in CONCENTRACAO.finditer(texto):
        antes = texto[: m.start()]
        if re.search(r"(?:\{hhi_[a-z]+_ano_posicao\}|\blimiar)\s+de\s+$", antes):
            continue
        frase_ini = max([x.end() for x in FIM_DE_FRASE.finditer(limpo[: m.start()])] or [0])
        proximo = FIM_DE_FRASE.search(limpo, m.end())
        trecho = texto[frase_ini : proximo.end() if proximo else len(texto)]
        if "{limiar_hhi}" in trecho and not re.search(r"\{hhi_[a-z]+_ano\}", trecho):
            continue  # definição do limiar, sem o HHI de nenhum grupo
        erros.append(f"{campo}: '{m.group(0)}' só como \"{{hhi_x_ano_posicao}} de alta concentração\" ou na definição do limiar")
        break
    if "soja_fob_ultimo" in chaves_campo and any(k.startswith("cif_") for k in chaves_campo) and not NO_PORTO.search(normal):
        erros.append(f"{campo}: soja FOB e fertilizante CIF juntos são a relação de troca; diga, no mesmo campo, que ela é medida no porto")
    permitidas = set(MAIUSCULAS_PERMITIDAS)
    for nome in (set().union(*fatos.entidades.values()) if fatos.entidades else set()) | fatos.outros_nomes:
        permitidas |= set(re.findall(r"[^\W\d_]+", nome))
    for k in chaves_campo:
        if k in fatos and not _numerico(k, fatos):
            permitidas |= set(re.findall(r"[^\W\d_]+", fatos[k]["valor"]))
    for m in re.finditer(r"(?<![^\W\d_])[A-ZÀ-Ý][^\W\d_]*", normal):
        antes = normal[: m.start()].rstrip()
        if antes and antes[-1] not in ".!?;:—–(\"“" and m.group(0) not in permitidas:
            erros.append(f"{campo}: nome próprio fora do pacote: {_trecho(normal, m.start(), m.end())!r}")
            break

    mencoes_do_campo: list[tuple[int, int, str]] = []  # grupos e produtos das frases anteriores, para "o grupo", "ali"
    inicio = 0
    for fim in [m.end() for m in FIM_DE_FRASE.finditer(limpo)] + [len(limpo)]:
        frase = limpo[inicio:fim]
        marcas = [(m.start(), m.end(), m.group(1)) for m in MARCADOR.finditer(texto) if inicio <= m.start() < fim and m.group(1) in fatos]
        chaves = [k for _, _, k in marcas]
        trecho = frase.strip()[:70]
        # Negação de uma direção ("não houve {x_sentido}", "reverteu a {x_sentido}").
        if any(k.endswith(("_sentido", "_posicao")) for k in chaves):
            alvo = AVISO_SEM_RECOMENDACAO.sub(" ", RESSALVA_INTEIRA.sub(" ", unicodedata.normalize("NFKC", frase)))
            if negacao := NEGACAO.search(alvo):
                erros.append(f"{campo}: '{negacao.group(0)}' numa frase com _sentido ou _posicao inverte a direção dada pelo programa: {trecho!r}")
        # Grupo ou produto de cada número: a menção mais próxima precisa ser a dele.
        mencoes = [(inicio + m.start(), inicio + m.end(), "g:" + g) for g, rx in MENCOES_GRUPO.items() for m in rx.finditer(frase)]
        mencoes += [(inicio + m.start(), inicio + m.end(), "p:" + p) for p, rx in MENCOES_PRODUTO.items() for m in rx.finditer(frase)]
        for m in ANAFORA_DE_GRUPO.finditer(frase):  # "o grupo", "nesse grupo": o último grupo citado
            anteriores = [x for x in mencoes_do_campo + mencoes if x[2].startswith("g:") and x[1] <= inicio + m.start()]
            if anteriores:
                mencoes.append((inicio + m.start(), inicio + m.end(), max(anteriores, key=lambda x: x[1])[2]))
        mencoes.sort()
        for s, e, k in marcas:
            if k.endswith(("_sentido", "_posicao")):
                continue
            g, p = _grupo_da_chave(k), _produto_da_chave(k)
            if (g is None and p is None) or (g is not None and not _numerico(k, fatos)):
                continue
            alvo = "g:" + g if g else "p:" + p
            prox = _mais_proxima(mencoes, s, e, limpo, fora_da_oracao=(g != "total"))
            if prox is not None and k in PROXY_DO_MAP and prox[2] == "p:dap":
                continue
            if prox is None:
                if g == "total":
                    continue
                prox = mencoes_do_campo[-1] if mencoes_do_campo else None
                if prox is None:
                    erros.append(f"{campo}: {{{k}}} é de {alvo[2:]}, mas a frase não diz de qual grupo ou produto: {trecho!r}")
                    continue
            if prox[2] != alvo:
                erros.append(f"{campo}: {{{k}}} é de {alvo[2:]}, mas está ligado a '{limpo[prox[0]:prox[1]].strip() or prox[2][2:]}' em {trecho!r}")
        mencoes_do_campo += mencoes
        # País ou porto de cada número, também pela menção mais próxima.
        entidades = _mencoes_de_entidade(frase, inicio, texto, fatos, fim)
        for s, e, k in marcas:
            if not _numerico(k, fatos) or k not in fatos.entidades:
                continue
            prox = _mais_proxima([m for m in entidades if m[0] != s], s, e, limpo)
            if prox is None:
                continue
            dele = fatos.entidades[k]
            exato = prox[3].startswith("marcador:rota") or k.startswith(("t_rota", "frete_rota"))
            if not (prox[2] == frozenset(dele) if exato else prox[2] & dele):
                erros.append(f"{campo}: {{{k}}} é de {'/'.join(sorted(dele))}, mas está ligado a {'/'.join(sorted(prox[2]))} em {trecho!r}")
        # País, porto ou produto escrito sem nenhuma chave dele na frase.
        citados = set().union(*(fatos.entidades.get(k, set()) for k in chaves)) if chaves else set()
        for m in entidades:
            if m[3] in ("texto", "apelido") and not (m[2] & citados):
                erros.append(f"{campo}: a frase cita {'/'.join(sorted(m[2]))} sem nenhuma chave de {'/'.join(sorted(m[2]))}: {trecho!r}")
                break
        for p, rx in MENCOES_PRODUTO.items():
            if rx.search(frase) and not any(_produto_da_chave(k) == p for k in chaves) and not (p == "dap" and PROXY_DO_MAP & set(chaves)):
                erros.append(f"{campo}: a frase cita {p} sem nenhuma chave de {p}: {trecho!r}")
        # Posição (principal, segunda, terceira) conferida com a ordem do pacote.
        for padrao, pos in [(RANKING, 1), (SEGUNDA, 2), (TERCEIRA, 3)]:
            for m in padrao.finditer(frase):
                prox = _mais_proxima(entidades, inicio + m.start(), inicio + m.end(), limpo)
                if prox is None:
                    continue
                if prox[3] == "marcador:porto_lider_ano" or prox[3].startswith("marcador:lider_"):
                    certo = pos == 1
                elif rota := re.fullmatch(r"marcador:rota_(\d+)_ano", prox[3]):
                    certo = pos == int(rota.group(1))
                else:
                    certo = any(fatos.ordem.get(k) == pos and fatos.entidades.get(k, set()) & prox[2] for k in chaves)
                if not certo:
                    erros.append(f"{campo}: '{m.group(0)}' não confere com a posição de {'/'.join(sorted(prox[2]))} no pacote: {trecho!r}")
        # Série, máximo, mínimo e "o maior valor" só ligados às chaves de máximo e média da série.
        for m in SUPERLATIVO.finditer(frase):
            pos_m = inicio + m.start()
            antes = [(pos_m - e, k) for s, e, k in marcas if _numerico(k, fatos) and e <= pos_m]
            depois = [(s - pos_m, k) for s, e, k in marcas if _numerico(k, fatos) and s > pos_m]
            perto = antes or depois
            if not perto or not CHAVES_HISTORICO.match(min(perto)[1]):
                erros.append(f"{campo}: '{m.group(0)}' só ligado a {{troca_x_max}}, {{troca_x_max_mes}} ou {{troca_x_media}}: {trecho!r}")
                break
        # Métrica de cada variação: o sujeito entre a vírgula anterior e o _sentido.
        for s, e, k in marcas:
            if not k.startswith("var_") or k.endswith("_sentido") or (met := _metrica(k)) is None:
                continue
            ini_var = min([s] + [s2 for s2, _, k2 in marcas if k2 == k + "_sentido"])
            corte = max([inicio] + [e2 for _, e2, _ in marcas if e2 <= ini_var] + [inicio + c.end() for c in CORTE_ORACAO.finditer(limpo[inicio:ini_var])])
            outras = [n for n, rx in METRICAS.items() if n != met and rx.search(limpo[corte:ini_var])]
            if outras:
                erros.append(f"{campo}: {{{k}}} é de {met}, mas o sujeito fala de {outras[0]}: {trecho!r}")
        for k in chaves:
            if k.startswith("cr3_") and not re.search(r"\b(?:maiores|principais)\s+(?:origens|fornecedores|pa[ií]ses)\b", frase, re.IGNORECASE):
                erros.append(f"{campo}: {{{k}}} é a soma das maiores origens; diga isso na frase: {trecho!r}")
        # Frete máximo e mínimo: só entre as maiores rotas listadas no pacote.
        extremos = CHAVES_FRETE_EXTREMO & set(chaves)
        if extremos and ({"n_rotas_relevantes_ano", "n_rotas_ano"} & set(chaves) or re.search(r"\brelevantes\b|\btodas\b|\bcom\s+carga\b", frase, re.IGNORECASE)):
            erros.append(f"{campo}: o maior e o menor frete valem só entre as maiores rotas listadas, não entre todas: {trecho!r}")
        if extremos and not re.search(r"\bmaiores\b|\blistadas\b|\bde\s+maior\s+volume\b|\bprincipais\b", frase, re.IGNORECASE):
            erros.append(f"{campo}: o maior e o menor frete valem só entre as maiores rotas; diga 'entre as maiores rotas': {trecho!r}")
        if re.search(r"\b(?:maior|menor)\s+frete\b", frase, re.IGNORECASE) and not extremos:
            erros.append(f"{campo}: 'maior frete' e 'menor frete' só com {{rota_frete_max_ano}} ou {{rota_frete_min_ano}}: {trecho!r}")
        erros += _periodo_da_frase(campo, frase, inicio, limpo, marcas, fatos, trecho)
        inicio = fim
    return erros


CITA_BENCHMARK = re.compile(r"\bbenchmark\w*|\bBanco\s+Mundial\b|\bPink\s+Sheet\b|\bspreads?\b|\brefer[êe]ncia\s+internacional\b", re.IGNORECASE)
NOMEIA_BENCHMARK = re.compile(r"\bbenchmark\w*|\bBanco\s+Mundial\b|\bPink\s+Sheet\b|\brefer[êe]ncia\b", re.IGNORECASE)
CITA_CAMBIO = re.compile(r"\bPTAX\b|\bBanco\s+Central\b|\bBCB\b|\bc[âa]mbio\w*|\bd[óo]lar\w*", re.IGNORECASE)
REAL_MOEDA = re.compile(r"\breal\b|\breais\b", re.IGNORECASE)
CAMBIO_NA_TROCA = re.compile(r"\breais\b|\bPTAX\b|\bc[âa]mbio\w*|\bd[óo]lar\w*", re.IGNORECASE)
# Valor que já traz a unidade (US$/t, R$/t, %, t) não leva outra depois.
UNIDADE_REPETIDA = re.compile(r"^\s*(?:reais|d[óo]lar\w*|por\s+tonelada|toneladas?|por\s*cento|pontos?\s+percentua\w*)\b", re.IGNORECASE)
CHAVES_DO_CAMBIO = {"ptax_mes", "var_ptax_ano"}


def _conferencias_benchmark(campo: str, texto: str, fatos: Fatos) -> list[str]:
    """Benchmark, spread, PTAX e CIF em reais: cada número com a sua fonte, base e moeda."""
    erros = []
    for m in MARCADOR.finditer(texto):
        k = m.group(1)
        if _numerico(k, fatos) and re.search(r"[^\d.,\s]", fatos[k]["valor"]) and UNIDADE_REPETIDA.match(_apagar_marcadores(texto[m.end() :])):
            erros.append(f"{campo}: {{{k}}} já traz a unidade ({fatos[k]['valor']}); não repita a unidade depois dele")
    limpo = _apagar_marcadores(texto)
    inicio = 0
    for fim in [m.end() for m in FIM_DE_FRASE.finditer(limpo)] + [len(limpo)]:
        frase = unicodedata.normalize("NFKC", limpo[inicio:fim])
        chaves = [m.group(1) for m in MARCADOR.finditer(texto) if inicio <= m.start() < fim and m.group(1) in fatos]
        trecho = frase.strip()[:70]
        inicio = fim
        da_pink = [k for k in chaves if k.startswith(CHAVES_PINK)]
        for prod, lado in fatos.lado_brasil.items():
            if not any(k.startswith(f"spread_{prod}_") for k in chaves):
                continue
            outro = "FOB" if lado == "CIF" else "CIF"
            if f"{'fob' if outro == 'FOB' else 'cif'}_brasil_{prod}_mes" in chaves or (
                re.search(rf"\b{outro}\b", frase) and not any(k.startswith("cif_brl_") for k in chaves)
            ):
                erros.append(f"{campo}: o spread de {prod} compara o preço {lado} do Brasil com o benchmark; não o ponha junto do preço {outro}: {trecho!r}")
        nomeia = NOMEIA_BENCHMARK.search(frase) or any(k.startswith("spread_") and k.endswith("_posicao") for k in chaves)  # "acima do benchmark"
        if any(k.startswith(("benchmark_", "base_benchmark_")) for k in chaves) and not nomeia:
            erros.append(f"{campo}: o benchmark é o preço de referência do Banco Mundial, não o que o Brasil pagou; diga 'benchmark' ou 'Banco Mundial' na frase: {trecho!r}")
        if PROXY_DO_MAP & set(chaves) and not re.search(r"\bDAP\b", frase):
            erros.append(f"{campo}: o Banco Mundial não publica MAP; o benchmark do MAP é o do DAP, e a frase precisa dizer isso: {trecho!r}")
        if CITA_BENCHMARK.search(frase) and not da_pink:
            erros.append(f"{campo}: benchmark, Banco Mundial e spread só com as chaves deles na mesma frase: {trecho!r}")
        if CITA_CAMBIO.search(frase) and not CHAVES_DO_CAMBIO & set(chaves):
            erros.append(f"{campo}: câmbio, dólar e PTAX só com {{ptax_mes}} ou {{var_ptax_ano}} na mesma frase: {trecho!r}")
        if "var_ptax_ano" in chaves and REAL_MOEDA.search(frase):
            erros.append(f"{campo}: {{var_ptax_ano}} é a variação do dólar em reais (alta = dólar mais caro); não fale do real nessa frase: {trecho!r}")
        if (CITA_TROCA.search(frase) or any(CHAVES_TROCA.match(k) for k in chaves)) and (
            any(k.startswith(CHAVES_PTAX) for k in chaves) or CAMBIO_NA_TROCA.search(frase)
        ):
            erros.append(f"{campo}: a relação de troca é US$ por US$ e não muda com o câmbio; não a ponha em reais nem junto da PTAX: {trecho!r}")
    return erros


def _periodo_da_frase(campo, frase, inicio, limpo, marcas, fatos, trecho) -> list[str]:
    """Cada número ligado ao seu período (mês, acumulado, ano anterior inteiro, série) e cada
    variação à sua base ({mes_ano_anterior} ou {periodo_ytd_anterior})."""
    erros = []
    chaves = [k for _, _, k in marcas]
    ancoras = []
    for s, e, k in marcas:
        c = _classe_periodo(k)
        if c in ("a_mes", "a_ytd"):
            ancoras.append((s, e, c[2:]))
        elif c == "a_passado":
            tipo = {"mes_ano_anterior": "p_mes", "periodo_ytd_anterior": "p_ytd"}.get(k, "p_any")
            if k == "ano_anterior":
                antes, depois = limpo[max(inicio, s - 25) : s], limpo[e : e + 10]
                if re.search(r"(?:mesmo|igual)\s+per[ií]odo\s+de\s+$", antes, re.IGNORECASE):
                    tipo = "p_ytd"
                elif re.search(r"mesmo\s+m[eê]s\s+de\s+$", antes, re.IGNORECASE):
                    tipo = "p_mes"
                elif re.search(r"(?:todo\s+o\s+ano|ano\s+inteiro)\s+de\s+$", antes, re.IGNORECASE) or re.match(r"\s+inteiro", depois):
                    tipo = "p_full"
            ancoras.append((s, e, tipo))
    for padrao, c in [(PALAVRA_MES, "mes"), (PALAVRA_YTD, "ytd")]:
        ancoras += [(inicio + m.start(), inicio + m.end(), c) for m in padrao.finditer(frase)]
    for m in re.finditer(r"\bano\s+anterior\s+inteiro\b|\btodo\s+o\s+ano\s+anterior\b", frase, re.IGNORECASE):
        ancoras.append((inicio + m.start(), inicio + m.end(), "p_full"))
    escritos = [(inicio + m.start(), m.group(0)) for m in PASSADO_ESCRITO.finditer(frase)]
    tem_atual = any(a[2] in ("mes", "ytd") for a in ancoras)
    tem_passado = any(a[2].startswith("p_") for a in ancoras)
    compara = any(k.startswith("var_") or (k.endswith("_anterior") and k not in PASSADO) for k in chaves)
    tem_valor_anterior = any((_classe_periodo(k) or "").startswith("prev_") for k in chaves)
    for s, e, k in marcas:
        c = _classe_periodo(k)
        if c is None or c.startswith("a_") or not _numerico(k, fatos):
            continue
        atuais = [a for a in ancoras if a[2] in ("mes", "ytd")]
        if c in ("mes", "ytd", "var_mes", "var_ytd") and atuais:
            depois_na_oracao = any(a[0] >= e and not CORTE_ORACAO.search(limpo[e : a[0]]) for a in atuais)
            prox = _mais_proxima(atuais, s, e, limpo, preferir_antes=not depois_na_oracao)
            quer = "mes" if c in ("mes", "var_mes") else "ytd"
            if prox and prox[2] != quer and not any(a[2] == quer for a in atuais if a is not prox and abs(a[0] - s) < 3):
                erros.append(f"{campo}: {{{k}}} é do {'mês' if quer == 'mes' else 'acumulado'}, mas está ligado a outro período em {trecho!r}")
        passados = [a for a in ancoras if a[2].startswith("p_")]
        if c in ("var_mes", "var_ytd") and passados:
            depois = [a for a in passados if a[0] >= e]
            base = min(depois, key=lambda a: a[0]) if depois else max(passados, key=lambda a: a[1])
            quer = "p_mes" if c == "var_mes" else "p_ytd"
            if base[2] not in (quer, "p_any"):
                erros.append(f"{campo}: {{{k}}} é sobre {'o mesmo mês' if quer == 'p_mes' else 'o mesmo período'} do ano anterior, não sobre a base escrita: {trecho!r}")
        if c in ("mes", "ytd", "hist") and (passados or escritos):
            como_base = all(COMO_BASE.search(limpo[inicio:pos]) for pos in [a[0] for a in passados] + [p for p, _ in escritos])
            if not (compara and (como_base or tem_valor_anterior)):
                erros.append(f"{campo}: {{{k}}} é do período atual (ou da série), mas a frase o põe no passado: {trecho!r}")
        if c == "prev_full" and MESMO_PERIODO.search(frase):
            erros.append(f"{campo}: {{{k}}} é do ano anterior inteiro, não do mesmo período: {trecho!r}")
        if tem_atual and tem_passado and tem_valor_anterior and c in ("mes", "ytd", "prev_mes", "prev_ytd", "prev_full"):
            seguintes = [a for a in ancoras if a[0] >= e and not CORTE_ORACAO.search(limpo[e : a[0]])]
            prox = min(seguintes, key=lambda a: a[0]) if seguintes else max([a for a in ancoras if a[1] <= s], key=lambda a: a[1], default=None)
            if prox is not None and c.startswith("prev_") != prox[2].startswith("p_"):
                erros.append(f"{campo}: {{{k}}} é do {'período anterior' if c.startswith('prev_') else 'período atual'}, mas está ligado ao outro período em {trecho!r}")
    return erros


def substituir(texto: str, fatos: Fatos) -> str:
    return re.sub(r"p\.p\.\.", "p.p.", MARCADOR.sub(lambda m: fatos[m.group(1)]["valor"], texto))


def validar(resposta: str, fatos: Fatos) -> tuple[dict | None, list[str]]:
    """(conteúdo com os marcadores já trocados, lista de erros). Reprovado = lista não vazia."""
    try:
        dados = extrair_json(resposta)
    except (ValueError, json.JSONDecodeError) as erro:
        return None, [f"JSON inválido: {erro}"]
    erros = []
    titulo, lead, paragrafos = dados.get("titulo"), dados.get("lead"), dados.get("paragrafos")
    if not isinstance(titulo, str) or not titulo.strip():
        erros.append("falta 'titulo' (texto)")
    if not isinstance(lead, str) or not lead.strip():
        erros.append("falta 'lead' (texto)")
    if not isinstance(paragrafos, list) or not all(isinstance(p, str) and p.strip() for p in paragrafos):
        erros.append("'paragrafos' deve ser uma lista de textos não vazios")
    elif not MIN_PARAGRAFOS <= len(paragrafos) <= MAX_PARAGRAFOS:
        erros.append(f"são {len(paragrafos)} parágrafos; o boletim precisa de {MIN_PARAGRAFOS} a {MAX_PARAGRAFOS}")
    if erros:
        return None, erros

    # Acentos compostos (NFC): "ã" decomposto vira um caractere só, e o que sobrar de combinante reprova.
    titulo, lead = unicodedata.normalize("NFC", titulo), unicodedata.normalize("NFC", lead)
    paragrafos = [unicodedata.normalize("NFC", p) for p in paragrafos]
    campos = [("titulo", titulo), ("lead", lead), *((f"paragrafo {n}", p) for n, p in enumerate(paragrafos, 1))]
    total_marcadores = 0
    for campo, texto in campos:
        e, n = _erros_do_texto(campo, texto, fatos)
        erros += e
        total_marcadores += n
    if erros:
        return None, erros
    if total_marcadores < MIN_MARCADORES:
        erros.append(f"o boletim cita só {total_marcadores} números (marcadores); são necessários pelo menos {MIN_MARCADORES}")

    final = {
        "titulo": substituir(titulo.strip(), fatos),
        "lead": substituir(lead.strip(), fatos),
        "paragrafos": [substituir(p.strip(), fatos) for p in paragrafos],
    }
    limites = [("titulo", final["titulo"], MAX_TITULO), ("lead", final["lead"], MAX_LEAD)]
    limites += [(f"paragrafo {n}", p, MAX_PARAGRAFO) for n, p in enumerate(final["paragrafos"], 1)]
    for campo, texto, maximo in limites:
        if len(texto) > maximo:
            erros.append(f"{campo} tem {len(texto)} caracteres depois de trocar os marcadores; o máximo é {maximo}")
    total = sum(len(t) for _, t, _ in limites)
    if total > MAX_TOTAL:
        erros.append(f"o boletim tem {total} caracteres; o máximo é {MAX_TOTAL}")
    if erros:
        return None, erros
    final["marcadores"] = {m: fatos[m]["valor"] for _, texto in campos for m in MARCADOR.findall(texto)}
    citadas = list(final["marcadores"])
    notas = []
    cita_troca = any(CITA_TROCA.search(MARCADOR.sub(" ", texto)) for _, texto in campos)
    if cita_troca or any(CHAVES_TROCA.match(m) for m in citadas) or ("soja_fob_ultimo" in citadas and any(m.startswith("cif_") and not m.startswith("cif_brl_") for m in citadas)):
        notas.append(NOTA_TROCA)
    if any(m.startswith(CHAVES_PINK) for m in citadas):
        notas.append(NOTA_BENCHMARK)
        if any(PRODUTO_DA_CHAVE.search(m) and PRODUTO_DA_CHAVE.search(m).group(1) == "map" for m in citadas if m.startswith(CHAVES_PINK)):
            notas.append(NOTA_MAP)
    if any(m.startswith(("cif_brl_", "var_cif_brl_")) for m in citadas):
        notas.append(NOTA_PTAX)
    if notas:
        final["nota"] = " ".join(notas)
    final["fontes"] = fontes_citadas(citadas)
    return final, []


def fontes_citadas(chaves) -> list[str]:
    """Comex Stat sempre; Banco Mundial e BCB quando o texto cita números deles."""
    fontes = [FONTE]
    if any(k.startswith(CHAVES_PINK) for k in chaves):
        fontes.append(FONTE_PINK)
    if any(k.startswith(CHAVES_PTAX) for k in chaves):
        fontes.append(FONTE_PTAX)
    return fontes


def rodape(fontes: list[str]) -> str:
    nomes = {FONTE: "do Comex Stat/MDIC", FONTE_PINK: "do Banco Mundial (Pink Sheet)", FONTE_PTAX: "do BCB (PTAX)"}
    partes = [nomes[f] for f in fontes]
    origem = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]
    return f"Texto gerado por IA a partir de dados {origem}; números inseridos e verificados automaticamente."


# --- Geração ------------------------------------------------------------------------------


class ProvedorMock:
    """Resposta fixa de exemplo, sem nenhuma API: para testes, CI e demonstração."""

    nome = "mock"
    modelo = "resposta-fixa"

    def gerar(self, sistema: str, usuario: str, *, esquema: dict | None = None) -> llm.Resposta:
        tem_troca = '"troca_map_ultimo"' in usuario
        origem = (
            "{lider_total_ano} foi a principal origem no ano, com {share_lider_total_ano} das toneladas. Entre as"
            " unidades de desembaraço, {porto_lider_ano} recebeu {share_porto_lider_ano} do volume."
            if '"lider_total_ano"' in usuario
            else "O preço médio FOB de todos os grupos foi de {preco_fob_total_mes} em {mes_referencia_curto}, e o frete"
            " médio do mês ficou em {frete_total_mes}."
        )
        ultimo = (
            "Na relação de troca medida no porto, que compara a soja exportada (FOB) com o fertilizante importado (CIF) e não é o"
            " preço ao produtor, uma tonelada de MAP equivalia a {troca_map_ultimo} em {mes_referencia_curto},"
            " {desvio_troca_map} {desvio_troca_map_posicao} de {troca_map_media}."
            if tem_troca
            else "A concentração de fornecedores de potássicos, medida pelo HHI, ficou em {hhi_k_ano} no ano,"
            " {hhi_k_ano_posicao} de alta concentração, de {limiar_hhi}."
            if '"hhi_k_ano"' in usuario
            else "Os nitrogenados somaram {t_n_mes} em {mes_referencia_curto}, e os potássicos, {t_k_mes}."
        )
        benchmark = None
        if '"spread_ureia_mes"' in usuario:
            benchmark = "Na comparação com o benchmark do Banco Mundial, o preço da ureia importada ficou {spread_ureia_mes} {spread_ureia_mes_posicao}"
            benchmark += ", e o CIF em reais chegou a {cif_brl_ureia_mes}." if '"cif_brl_ureia_mes"' in usuario else "."
        resposta = {
            "titulo": "Importações de fertilizantes em {mes_referencia}",
            "lead": (
                "O Brasil importou {t_total_mes} de fertilizantes em {mes_referencia}, uma {var_t_total_mes_sentido}"
                " de {var_t_total_mes} sobre {mes_ano_anterior}."
                if '"var_t_total_mes"' in usuario
                else "O Brasil importou {t_total_mes} de fertilizantes em {mes_referencia}."
            ),
            "paragrafos": [
                (
                    "No acumulado de {periodo_ytd}, as compras somaram {t_total_ytd}, com {var_t_total_ytd_sentido} de"
                    " {var_t_total_ytd} sobre o mesmo período de {ano_anterior}."
                    if '"var_t_total_ytd"' in usuario
                    else "No acumulado de {periodo_ytd}, as compras somaram {t_total_ytd}."
                )
                + " Os potássicos responderam por {share_k_ytd} do volume e os nitrogenados por {share_n_ytd}.",
                "O preço médio CIF de todos os grupos ficou em {preco_cif_total_mes} no mês. No ano, o frete médio foi de"
                " {frete_medio_ytd}, o equivalente a {frete_pct_cif_ytd} do valor CIF.",
                origem,
                ultimo,
                *([benchmark] if benchmark else []),
            ],
        }
        return llm.Resposta(json.dumps(resposta, ensure_ascii=False), self.modelo)


def _nome_valido(nome) -> bool:
    return isinstance(nome, str) and bool(MODELO_VALIDO.fullmatch(nome)) and not re.search(r"://|www\.|@\w+\.\w", nome)


def _nome_do_modelo(devolvido, configurado) -> str:
    """O nome do modelo vai para o .md e o card sem passar pela validação do texto. Publica o
    configurado; o devolvido pela API só entra se for uma versão dele ("gpt-4o" →
    "gpt-4o-2024-08-06"), para um gateway não pôr texto ou endereço no lugar do nome."""
    if not _nome_valido(configurado):
        return devolvido if _nome_valido(devolvido) else "não informado"
    if _nome_valido(devolvido) and devolvido.lower().startswith(configurado.lower()):
        return devolvido
    return configurado


def gerar(fatos: Fatos, provedor) -> dict:
    """Pede o texto, valida e, se reprovar, tenta mais uma vez com os erros. Falha sem publicar."""
    erros: list[str] = []
    texto = None
    for tentativa in range(1, TENTATIVAS + 1):
        resposta = provedor.gerar(SISTEMA, montar_prompt(fatos, erros, texto), esquema=ESQUEMA_RESPOSTA)
        texto = resposta.texto
        conteudo, erros = validar(texto, fatos)
        if not erros:
            conteudo["modelo"] = _nome_do_modelo(resposta.modelo, getattr(provedor, "modelo", None))
            return conteudo
        log.warning("Tentativa %d/%d reprovada na validação:\n  %s", tentativa, TENTATIVAS, "\n  ".join(_uma_linha(e) for e in erros))
    raise ErroBoletim(f"resposta reprovada na validação após {TENTATIVAS} tentativas; nada foi publicado.")


# --- Saída -------------------------------------------------------------------------------


def _gravar(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(texto, encoding="utf-8")
    os.replace(temporario, caminho)


def markdown(conteudo: dict, meta: dict) -> str:
    linhas = [f"# {conteudo['titulo']}", ""]
    if meta["mock"]:
        linhas += ["> **Exemplo do modo --mock:** texto fixo de demonstração, sem IA.", ""]
    linhas += [
        f"*Boletim de {meta['mes_referencia']} · importações brasileiras de fertilizantes · Fonte: {meta['fonte']}*",
        "",
        f"**{conteudo['lead']}**",
        "",
    ]
    for paragrafo in conteudo["paragrafos"]:
        linhas += [re.sub(r"^(\d+)([.)])(?=\s|$)", r"\1\\\2", paragrafo), ""]
    if conteudo.get("nota"):
        linhas += [f"*{conteudo['nota']}*", ""]
    linhas += ["<details>", "<summary>Números citados (inseridos pelo programa a partir do pacote de fatos)</summary>", ""]
    linhas += ["| Marcador | Valor |", "| --- | --- |"]
    linhas += [f"| `{chave}` | {valor} |" for chave, valor in conteudo["marcadores"].items()]
    linhas += ["", "</details>", "", "---", "", f"*{meta['rodape']}*", ""]
    linhas += [f"<sub>Provedor: {meta['provedor']} · modelo: `{meta['modelo']}` · gerado em {meta['gerado_em']} · [dashboard]({meta['dashboard']})</sub>", ""]
    return "\n".join(linhas)


def _enderecos(env=None) -> tuple[str, str]:
    """(repositório, dashboard): os deste repositório no GitHub Actions (num fork, os do fork), senão
    os do original."""
    env = os.environ if env is None else env
    repo = env.get("GITHUB_REPOSITORY") or ""
    if env.get("GITHUB_SERVER_URL", "https://github.com") == "https://github.com" and re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo):
        dono, nome = repo.split("/")
        return f"https://github.com/{repo}", f"https://{dono.lower()}.github.io/{nome}/"
    return REPOSITORIO, DASHBOARD


def _mes_do_latest(latest: Path) -> str | None:
    try:
        mes = json.loads(latest.read_text(encoding="utf-8")).get("mes")
    except (OSError, ValueError, AttributeError):
        return None
    return mes if isinstance(mes, str) and re.fullmatch(r"\d{4}-\d{2}", mes) else None


def escrever(conteudo: dict, fatos: Fatos, saida: Path, provedor: str, mock: bool, agora: datetime | None = None) -> tuple[Path, Path | None]:
    """Grava AAAA-MM.md e, se o mês não for anterior ao do latest.json atual, o latest.json."""
    agora = agora or datetime.now(timezone.utc)
    mes = fatos.periodo
    arquivo = Path(saida) / f"{mes}.md"
    _, dashboard = _enderecos()
    meta = {
        "mes": mes,
        "mes_referencia": fatos["mes_referencia"]["valor"],
        "fonte": "; ".join(conteudo.get("fontes") or [FONTE]),
        "rodape": rodape(conteudo.get("fontes") or [FONTE]),
        "provedor": provedor,
        "modelo": conteudo["modelo"],
        "mock": mock,
        "gerado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arquivo": f"boletins/{arquivo.name}",
        "dashboard": dashboard,
    }
    ultimo = {**meta, "titulo": conteudo["titulo"], "lead": conteudo["lead"], "paragrafos": conteudo["paragrafos"], "marcadores": conteudo["marcadores"]}
    if conteudo.get("nota"):
        ultimo["nota"] = conteudo["nota"]
    _gravar(arquivo, markdown(conteudo, meta))
    latest = Path(saida) / "latest.json"
    atual = _mes_do_latest(latest)
    if atual is not None and atual > mes:  # preencher um mês antigo não tira o mais recente do card
        log.info("latest.json continua em %s, mais recente que %s.", atual, mes)
        return arquivo, None
    _gravar(latest, json.dumps(ultimo, ensure_ascii=False, indent=1) + "\n")
    return arquivo, latest


# --- CLI ---------------------------------------------------------------------------------


def _mes(texto: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", texto):
        raise argparse.ArgumentTypeError(f"mês inválido: {texto!r} (use AAAA-MM)")
    return texto


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.boletim", description="Fase 5: boletim mensal com IA. Fonte: Comex Stat/MDIC.")
    p.add_argument("--mock", action="store_true", help="resposta fixa de exemplo, sem chamar nenhuma API (grava em boletins/mock/)")
    p.add_argument("--mes", type=_mes, metavar="AAAA-MM", help="mês do boletim (padrão: o último com dados)")
    p.add_argument("--dados", type=Path, default=config.PROCESSED_DIR, help="dados processados (padrão: data/processed)")
    p.add_argument("--saida", type=Path, help="pasta de saída (padrão: boletins/, ou boletins/mock/ com --mock)")
    p.add_argument("--forcar", action="store_true", help="refaz o boletim do mês se ele já existir")
    p.add_argument("--fatos", action="store_true", help="só imprime o pacote de fatos (JSON) e sai, sem chamar a IA")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    saida = args.saida or (config.BOLETINS_DIR / "mock" if args.mock else config.BOLETINS_DIR)
    try:
        painel, troca = carregar(args.dados)
        fatos = montar_fatos(painel, troca, args.mes)
        if args.fatos:
            print(json.dumps(fatos, ensure_ascii=False, indent=1))
            return 0
        mes = fatos.periodo
        if (Path(saida) / f"{mes}.md").exists() and not args.forcar:
            log.info("%s já existe; nada a fazer (use --forcar para refazer).", Path(saida) / f"{mes}.md")
            return 0
        provedor = ProvedorMock() if args.mock else llm.provedor_do_ambiente()
        log.info("Boletim de %s: provedor %s, modelo %s, %d fatos no pacote.", mes, provedor.nome, provedor.modelo, len(fatos))
        conteudo = gerar(fatos, provedor)
        arquivo, latest = escrever(conteudo, fatos, saida, provedor.nome, args.mock)
    except (ErroBoletim, llm.ErroLLM) as erro:
        log.error("%s", erro)
        return 1
    log.info("Gravado %s%s (%d números citados).", arquivo, f" e {latest}" if latest else "", len(conteudo["marcadores"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
