"""Fase 5: boletim mensal escrito por IA, com números inseridos e verificados pelo programa.

Fluxo:

1. Monta um "pacote de fatos" só com dados já calculados (data/processed/dashboard.json e
   troca_mensal.csv): chaves nomeadas, como troca_map_ultimo ou share_russia_k_ano, com
   valores já formatados em pt-BR e uma descrição de cada uma.
2. Pede à IA um JSON com título, lead e 4 a 6 parágrafos que citam números SÓ por
   marcadores {chave}. Número literal é proibido.
3. Valida a resposta, com a mesma regra para todos os provedores: todo marcador existe no
   pacote; nenhum dígito, número por extenso, nome de mês, contagem, unidade ou sinal fora dos
   marcadores; toda palavra de direção (subiu, queda...) na mesma frase de uma variação do
   pacote e coerente com ela; a relação de troca sempre com a ressalva "no porto"; texto simples
   numa linha só (sem Markdown, HTML ou caractere invisível); sem os termos de uma lista fixa de
   recomendação e tom alarmista; e tamanho máximo. Se reprovar, tenta mais uma vez com os erros
   no prompt; se reprovar de novo, falha sem publicar nada.
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
from pathlib import Path

from . import config, llm

log = logging.getLogger(__name__)

RODAPE = "Texto gerado por IA a partir de dados do Comex Stat/MDIC; números inseridos e verificados automaticamente."
FONTE = "Comex Stat/MDIC"
REPOSITORIO = "https://github.com/AliceCullen-html/radar-fertilizantes-br"
DASHBOARD = "https://alicecullen-html.github.io/radar-fertilizantes-br/"

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


def _num(valor: float, casas: int = 0) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "\0").replace(".", ",").replace("\0", ".")


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

    `periodo` guarda o mês de referência (AAAA-MM). `direcao` liga cada chave que tem direção
    (a variação e o valor a que ela se refere) à chave _sentido ou _posicao que a confirma.
    """

    periodo = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.direcao: dict[str, str] = {}

    def add(self, chave: str, valor: str | None, descricao: str) -> None:
        if valor is None:
            return
        if not re.fullmatch(r"[a-z0-9_]+", chave):
            raise ValueError(f"chave inválida no pacote de fatos: {chave!r}")
        self[chave] = {"valor": valor, "descricao": descricao}

    def add_var(self, chave: str, variacao: float | None, descricao: str, pp: bool = False, nivel: tuple[str, ...] = ()) -> None:
        """Variação sem sinal, mais a direção em {chave}_sentido. `nivel`: as chaves do valor que
        variou (ex.: t_k_mes para var_t_k_mes), conferidas contra a mesma direção."""
        if variacao is None:
            return
        self.add(chave, fmt_pp(abs(variacao)) if pp else fmt_pct(abs(variacao)), f"{descricao} (sem sinal; direção em {chave}_sentido)")
        self.add(f"{chave}_sentido", sentido(variacao), f"Direção de {chave}: alta, queda ou estabilidade")
        for k in (chave, *nivel):
            self.direcao[k] = f"{chave}_sentido"

    def add_posicao(self, chave: str, valor: str, descricao: str, de: tuple[str, ...]) -> None:
        """Posição (acima/abaixo de uma referência) das chaves `de`, conferida como direção."""
        self.add(chave, valor, descricao)
        for k in de:
            self.direcao[k] = chave

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
            f.add_var(f"var_t_{k}_mes", _var_pct(t_mes, s["t"][i_ant]), f"Variação das toneladas de {mes_extenso(mes)} sobre {mes_extenso(anterior)}, {nome}", nivel=(f"t_{k}_mes",))
        if t_ytd:
            f.add(f"t_{k}_ytd", fmt_t(t_ytd), f"Toneladas importadas de {acum}, {nome}")
        if tem_ytd_ant:
            f.add_var(f"var_t_{k}_ytd", _var_pct(t_ytd, _soma(s["t"], ytd_ant)), f"Variação das toneladas de {acum} sobre {acum_ant}, {nome}", nivel=(f"t_{k}_ytd",))
        if g != "TOTAL" and t_ytd_total:
            f.add(f"share_{k}_ytd", fmt_pct(100 * t_ytd / t_ytd_total), f"Participação dos {nome} nas toneladas totais de {acum}")

        cif = lambda idx: (s["cif_musd"][idx] or 0) * 1e6 / s["t"][idx] if s["t"][idx] else None  # noqa: E731
        if cif(i) is not None:
            f.add(f"preco_cif_{k}_mes", fmt_usd_t(cif(i)), f"Preço médio CIF (valor ÷ toneladas) em {mes_extenso(mes)}, {nome}")
            if i_ant is not None:
                f.add_var(f"var_preco_cif_{k}_mes", _var_pct(cif(i), cif(i_ant)), f"Variação do preço CIF de {mes_extenso(mes)} sobre {mes_extenso(anterior)}, {nome}", nivel=(f"preco_cif_{k}_mes",))

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
            f.add_var("var_frete_medio_ytd", _var_pct(frete_ytd, frete_ant), f"Variação do frete médio de {acum} sobre {acum_ant}", nivel=("frete_medio_ytd",))

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
                    f.add(f"lider_{k}_ano", pais, f"Maior país de origem de {acum}, {nome}")
                    f.add(f"share_lider_{k}_ano", fmt_pct(r[3]), f"Participação de {pais}, maior origem, nas toneladas de {acum}, {nome}")
                f.add(f"share_{s_pais}_{k}_ano", fmt_pct(r[3]), f"Participação de {pais} ({ordem} origem) nas toneladas de {acum}, {nome}")
                niveis = (f"share_{s_pais}_{k}_ano", f"share_lider_{k}_ano") if pos == 0 else (f"share_{s_pais}_{k}_ano",)
                f.add_var(f"var_share_{s_pais}_{k}_ano", r[5], f"Variação da participação de {pais} sobre {acum_ant}, {nome}", pp=True, nivel=niveis)
                f.add_var(f"var_t_{s_pais}_{k}_ano", r[4], f"Variação das toneladas vindas de {pais} sobre {acum_ant}, {nome}")
            a = anual[g]
            if a["hhi"][ia] is not None:
                f.add(f"hhi_{k}_ano", _num(a["hhi"][ia]), f"Concentração de fornecedores (HHI, 0 a 10.000) de {acum}, {nome}")
                lado = "acima do limiar" if a["hhi"][ia] > LIMIAR_HHI else "abaixo do limiar" if a["hhi"][ia] < LIMIAR_HHI else "no limiar"
                f.add_posicao(f"hhi_{k}_ano_posicao", lado, f"Posição do HHI de {acum} frente ao limiar de alta concentração (limiar_hhi), {nome}", de=(f"hhi_{k}_ano",))
                if ia > 0 and a["hhi"][ia - 1] is not None:
                    f.add(f"hhi_{k}_ano_anterior", _num(a["hhi"][ia - 1]), f"HHI do ano {ano - 1} inteiro, {nome}")
                f.add(f"cr3_{k}_ano", fmt_pct(a["cr3_pct"][ia]), f"Participação somada das três maiores origens de {acum}, {nome}")
                f.add(f"n_origens_{k}_ano", _num(a["n_origens"][ia]), f"Número de países de origem de {acum}, {nome}")
        portos = [r for r in (painel["urfs"]["dados"].get(str(ano), {}).get("TOTAL") or []) if r[1] != "Outros"]
        for pos, r in enumerate(portos[:3]):
            nome_p, s_p = nome_urf(r[1]), slug(nome_urf(r[1]))
            ordem = ["maior", "segunda maior", "terceira maior"][pos]
            if pos == 0:
                f.add("porto_lider_ano", nome_p, f"URF (porto) que mais recebeu fertilizantes de {acum}")
                f.add("share_porto_lider_ano", fmt_pct(r[3]), f"Participação de {nome_p} nas toneladas de {acum}")
            f.add(f"share_porto_{s_p}_ano", fmt_pct(r[3]), f"Participação da URF {nome_p} ({ordem}) nas toneladas de {acum}")
            niveis = (f"share_porto_{s_p}_ano", "share_porto_lider_ano") if pos == 0 else (f"share_porto_{s_p}_ano",)
            f.add_var(f"var_share_porto_{s_p}_ano", r[5], f"Variação da participação de {nome_p} sobre {acum_ant}", pp=True, nivel=niveis)

    # --- Rotas país de origem -> URF no acumulado do ano
    rotas = (painel.get("rotas") or {}).get("dados", {}).get(str(ano), {}).get("TOTAL")
    if ano_ate_o_mes and rotas and rotas.get("rotas"):
        for pos, r in enumerate(rotas["rotas"][:3], start=1):
            f.add(f"rota_{pos}_ano", f"{r[0]} → {nome_urf(r[2])}", f"Rota (país de origem → URF) número {pos} em toneladas de {acum}")
            f.add(f"t_rota_{pos}_ano", fmt_t(r[3]), f"Toneladas da rota {pos} de {acum}")
            if r[5] is not None:
                f.add(f"frete_rota_{pos}_ano", fmt_usd_t(r[5]), f"Frete médio por tonelada da rota {pos} de {acum}")
        com_frete = [r for r in rotas["rotas"] if r[5] is not None]
        if com_frete:
            caro, barato = max(com_frete, key=lambda r: r[5]), min(com_frete, key=lambda r: r[5])
            so_maiores = "entre as rotas de maior volume listadas no dashboard, não entre todas as rotas relevantes (n_rotas_relevantes_ano)"
            f.add("rota_frete_max_ano", f"{caro[0]} → {nome_urf(caro[2])}", f"Rota de maior frete por tonelada de {acum}, {so_maiores}")
            f.add("frete_rota_max_ano", fmt_usd_t(caro[5]), f"Frete por tonelada da rota rota_frete_max_ano, {so_maiores}")
            f.add("rota_frete_min_ano", f"{barato[0]} → {nome_urf(barato[2])}", f"Rota de menor frete por tonelada de {acum}, {so_maiores}")
            f.add("frete_rota_min_ano", fmt_usd_t(barato[5]), f"Frete por tonelada da rota rota_frete_min_ano, {so_maiores}")
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
        f.add_posicao(f"desvio_troca_{prod}_posicao", posicao(desvio), f"Posição da relação de {nome} frente à média histórica", de=(f"desvio_troca_{prod}", f"troca_{prod}_media"))
        lado = (valor > media) - (valor < media)
        seguidos = 0
        for r in reversed(razoes):
            if lado != 0 and ((r > media) - (r < media)) == lado:
                seguidos += 1
            else:
                break
        if seguidos:
            f.add(f"meses_seguidos_troca_{prod}", f"{seguidos} {'mês' if seguidos == 1 else 'meses'}", f"Meses seguidos, até {mes_extenso(mes)}, com a relação de {nome} do mesmo lado ({'acima' if lado > 0 else 'abaixo'}) da média histórica")
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
                f.add_var("var_soja_fob_ano", _var_pct(soja, numero(anterior["preco_fob_soja_usd_t"])), "Variação do preço FOB da soja sobre o mesmo mês do ano anterior", nivel=("soja_fob_ultimo",))
            f.add("periodo_media_troca", f"{mes_extenso(serie[0]['periodo'])} a {mes_extenso(mes)}", "Período da média histórica da relação de troca")


# --- Prompt -------------------------------------------------------------------------------

SISTEMA = """Você é analista de inteligência de mercado e escreve o boletim mensal do Radar das importações brasileiras de fertilizantes, com dados públicos do Comex Stat/MDIC.

Você recebe um PACOTE DE FATOS em JSON: cada chave tem um valor já formatado e uma descrição. Escreva um boletim curto em português do Brasil a partir desses fatos.

Regras invioláveis. Um programa confere a resposta e a reprova se qualquer uma for descumprida:
1. Todo número entra SOMENTE como marcador {chave}, com a chave exatamente como está no pacote. O programa troca o marcador pelo valor. Isso vale para anos, meses, percentuais, toneladas, preços, contagens e sacas. Nunca escreva o nome de um mês: use {mes_referencia}, {mes_ano_anterior}, {periodo_ytd} e afins.
2. Nenhum dígito fora dos marcadores, nem números por extenso (dois, três, dez, mil, milhões, zero, meia, dobro, dobrou, triplicou, metade, um terço...), algarismos romanos ou posições além de "terceiro". Também não escreva contagens nem períodos que o pacote não traz: "terceiro mês seguido", "consecutivo", "pela primeira vez", "recorde", "inédito", semestre, trimestre, década, século.
3. Os valores já trazem a unidade (%, t, US$/t, p.p.). Não escreva %, "por cento", US$ ou p.p. fora dos marcadores, nem sinal (+ ou −) antes de um marcador.
4. Variações vêm sem sinal. A direção está na chave com sufixo _sentido (alta, queda ou estabilidade) ou _posicao. Toda palavra de direção (alta, queda, subiu, caiu, cresceu, recuou, avançou, maior que, mais barato e afins) precisa estar na mesma frase de um marcador de variação, do seu _sentido ou do valor que variou, e ser coerente com ele: o programa reprova a palavra que contradiz a direção e a palavra de direção sem esse marcador na frase. As variações mensais são sobre o mesmo mês do ano anterior ({mes_ano_anterior}), não sobre o mês anterior.
5. Use só os fatos do pacote. Não cite causas, eventos, empresas ou previsões que não estejam nele. Pode comparar os fatos entre si.
6. Tom de analista: neutro e objetivo, sem recomendação de compra ou venda e sem adjetivos alarmistas (como alarmante, explosão, disparada, colapso, catastrófico, crítico, preocupante).
7. Todo título, lead ou parágrafo que citar a relação de troca (chaves troca_, sacas_, desvio_troca_ e meses_seguidos_troca_) precisa dizer nele mesmo que ela é medida "no porto" (soja FOB x fertilizante CIF) e que não é o preço ao produtor.
8. Texto simples, cada campo numa linha só: sem quebra de linha, sem Markdown, sem HTML, sem listas, sem asteriscos, sem colchetes.

Formato: responda SOMENTE com um objeto JSON com as chaves "titulo" (até 100 caracteres), "lead" (uma ou duas frases, até 350 caracteres) e "paragrafos" (lista de 4 a 6 parágrafos curtos, cada um com até 600 caracteres).

Exemplo de frase válida: "As importações somaram {t_total_mes} em {mes_referencia}, {var_t_total_mes_sentido} de {var_t_total_mes} sobre {mes_ano_anterior}."
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
POR_EXTENSO = re.compile(
    r"\b(zero|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|quinze|"
    r"dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|"
    r"cem|cento|duzent\w*|trezent\w*|quatrocent\w*|quinhent\w*|seiscent\w*|setecent\w*|oitocent\w*|novecent\w*|"
    r"mil|milh[ãa]o|milh[õo]es|bilh[ãa]o|bilh[õo]es|trilh[ãa]o|trilh[õo]es|mi|bi|tri|dobro|triplo|qu[aá]druplo|metade|"
    r"meia|meio\s+(?:ponto|por\s*cento|milh\w*|bilh\w*)|e\s+meio|dezenas?|centenas?|milhares|ter[çc]os?|d[uú]zias?|"
    # multiplicativos e ordinais além do terceiro (o pacote só traz as três maiores posições)
    r"dobr(?:ou|aram|am|ando|ado|ada|ados|adas|ar|ará|aria)|duplic\w*|triplic\w*|quadruplic\w*|quintuplic\w*|"
    r"quart[oa]s?|quint[oa]s?|sext[oa]s?|s[eé]tim[oa]s?|oitav[oa]s?|non[oa]s?|d[eé]cim[oa]s?)\b",
    re.IGNORECASE,
)
# Algarismos romanos com duas letras ou mais (XXI, IV...), em maiúsculas; MDIC não casa.
ROMANOS = re.compile(r"\b(?=[MDCLXVI]{2,}\b)M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})(?<=[MDCLXVI])\b")
# Meses e períodos só entram por marcador ({mes_referencia}, {periodo_ytd}...).
MESES_E_PERIODOS = re.compile(
    r"\b(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
    r"semestr\w*|trimestr\w*|bimestr\w*|quinzena\w*|d[eé]cadas?|dec[eê]nios?|s[eé]culos?)\b",
    re.IGNORECASE,
)
MES_ANTERIOR = re.compile(r"\bm[eê]s\s+(anterior|passado)\b", re.IGNORECASE)
# Contagens e recordes que o pacote não traz (a única contagem é meses_seguidos_troca_*).
CONTAGENS = re.compile(
    r"\b((?:primeir|segund|terceir)[oa]s?\s+(?:m[eê]s|meses|anos?|vez|vezes|trimestres?|semestres?|semanas?|altas?|quedas?|aumentos?|recuos?)|"
    r"(?:m[eê]s|meses|anos?|vezes|trimestres?|semestres?|semanas?|altas?|quedas?)\s+(?:seguid|consecutiv)\w*|"
    r"consecutiv\w*|em\s+sequ[eê]ncia|recordes?|in[eé]dit\w*)\b",
    re.IGNORECASE,
)
# Palavras de direção. As de mudança (subiu, queda...) precisam de um marcador de variação na
# mesma frase; as de posição (acima, maior que...) são conferidas quando ficam junto de um.
_MUDANCA_ALTA = (
    r"alta|altas|aument\w*|cresc\w*|subi\w*|sob(?:e|em)|avan[çc]\w*|expans\w*|expandi\w*|elev(?:ou|aram|ação|ações)|"
    r"ganh\w*|acr[eé]scimos?|valoriz\w*|encarec\w*|salt(?:o|os|ou|aram)|increment\w*|recupera\w*|positiv\w*|"
    r"amplia\w*|ampliou|(?<!\bo )(?<!\ba )(?<!\bos )(?<!\bas )mais\s+car[oa]s?"
)
_POSICAO_ALTA = r"acima|superior\w*|maior(?:es)?\s+(?:do\s+)?que|(?<!\bo )(?<!\ba )(?<!\bos )(?<!\bas )mais\s+alt[oa]s?"
_MUDANCA_QUEDA = (
    r"queda|quedas|ca[ií](?:u|ram|r|a|am|em|ndo|do|da|dos|das)?|recu(?:o|os|ou|aram|ar|ando|ada|ado)|"
    r"redu(?:ziu|ziram|zir|ção|ções|zida|zido|zidas|zidos)|diminu\w*|retra(?:ção|ções|iu|íram|ída|ído)|perd\w*|"
    r"baix(?:a|as|ou|aram)|decl[ií]n\w*|desvaloriz\w*|barate\w*|encolh\w*|negativ\w*|al[ií]vio|tomb(?:o|os|ou|aram)|"
    r"ced(?:eu|eram)|contra[íi]\w*|contra[çc][ãa]o|enfraquec\w*|(?<!\bo )(?<!\ba )(?<!\bos )(?<!\bas )mais\s+barat[oa]s?"
)
_POSICAO_QUEDA = r"abaixo|inferior\w*|menor(?:es)?\s+(?:do\s+)?que|(?<!\bo )(?<!\ba )(?<!\bos )(?<!\bas )mais\s+baix[oa]s?"
PALAVRAS_ALTA = re.compile(rf"\b(?:{_MUDANCA_ALTA}|{_POSICAO_ALTA})\b", re.IGNORECASE)
PALAVRAS_QUEDA = re.compile(rf"\b(?:{_MUDANCA_QUEDA}|{_POSICAO_QUEDA})\b", re.IGNORECASE)
PALAVRAS_MUDANCA = re.compile(rf"\b(?:{_MUDANCA_ALTA}|{_MUDANCA_QUEDA})\b", re.IGNORECASE)
# "{var_x} maior que..." e "{var_x} a menos": comparativo logo depois do marcador.
LOGO_DEPOIS_ALTA = re.compile(r"^\s*(?:a\s+)?(maior|mais|superior|acima)\b", re.IGNORECASE)
LOGO_DEPOIS_QUEDA = re.compile(r"^\s*(?:a\s+)?(menor|menos|inferior|abaixo)\b", re.IGNORECASE)
FIM_DE_ORACAO = re.compile(r"[.;:!?]")
# Sinal colado antes de um marcador com direção: "+{var_x}" publicaria o sinal que a IA escolheu.
SINAL_ANTES = re.compile(r"[+\-±‐-―−﹣＋－]\s*$")
UNIDADES = re.compile(r"%|US\$|R\$|\bp\.\s?p\.|\bpor\s*cento\b|\bpontos?\s+percentua\w*", re.IGNORECASE)
MARCACAO = re.compile(r"[*#`<>\[\]|_~{}]")
PROIBIDOS = re.compile(
    r"\b(recomend\w*|aconselh\w*|sugerimos|compre|comprem|venda agora|vender agora|oportunidade de compra|"
    r"vale\s+a\s+pena|antecipar\s+(?:as\s+)?compras|hora\s+de\s+(?:comprar|vender)|"
    r"momento\s+(?:é\s+)?(?:favor[aá]vel|ideal|oportuno|certo)\s+para|aproveit(?:ar|e|em)\b|"
    r"alarmant\w*|catastr[oó]f\w*|desastr\w*|colaps\w*|explos\w*|explod\w*|dispar(ou|aram|ada|adas|ado|ados)|"
    r"despenc\w*|desab(ou|aram)|assustador\w*|dram[aá]tic\w*|p[aâ]nico|caos|ca[oó]tic\w*|vertiginos\w*|"
    r"preocupant\w*|cr[ií]tic[oa]s?|grav[ií]ssim\w*)\b",
    re.IGNORECASE,
)
# Quebra de linha, tabulação, controles bidi, largura zero, hífen suave, uso privado...
INVISIVEIS = {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}
CHAVES_TROCA = re.compile(r"^(troca|sacas|desvio_troca|meses_seguidos_troca)_")
NO_PORTO = re.compile(r"\bno\s+porto\b", re.IGNORECASE)
PRODUTOR = re.compile(r"\b(produtor\w*|agricultor\w*|fazendas?|lavouras?)\b", re.IGNORECASE)
NEGACAO = re.compile(r"\bn[ãa]o\b", re.IGNORECASE)
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


def _trecho(texto: str, inicio: int, fim: int) -> str:
    return "…" + texto[max(0, inicio - 25) : fim + 25].replace("\n", " ") + "…"


def _apagar_marcadores(texto: str) -> str:
    """Troca cada marcador por espaços do mesmo tamanho: as posições continuam valendo."""
    return MARCADOR.sub(lambda m: " " * len(m.group(0)), texto)


def _erros_do_texto(campo: str, texto: str, fatos: Fatos) -> tuple[list[str], int]:
    erros = []
    invisivel = next((ch for ch in texto if unicodedata.category(ch) in INVISIVEIS), None)
    if invisivel is not None:
        erros.append(f"{campo}: quebra de linha, tabulação ou caractere invisível (U+{ord(invisivel):04X}); cada campo é uma linha só de texto simples")
    marcadores = MARCADOR.findall(texto)
    desconhecidos = [m for m in marcadores if not CHAVE_VALIDA.fullmatch(m) or m not in fatos]
    if desconhecidos:
        lista = ", ".join("{" + m + "}" for m in sorted(set(desconhecidos))[:10])
        erros.append(f"{campo}: marcadores que não existem no pacote: {lista}")
    resto = MARCADOR.sub(" ", texto)
    fora_do_latim = next((ch for ch in resto if unicodedata.category(ch).startswith("L") and not unicodedata.name(ch, "").startswith("LATIN")), None)
    if fora_do_latim is not None:
        erros.append(f"{campo}: letra fora do alfabeto latino ({fora_do_latim!r})")
    if not re.search(r"[^\W\d_]", resto):
        erros.append(f"{campo}: só marcadores, sem texto")
    numerico = next((i for i, ch in enumerate(resto) if unicodedata.numeric(ch, None) is not None), None)
    if numerico is not None:  # dígitos de qualquer escrita, sobrescritos (²), frações (½), Ⅻ...
        erros.append(f"{campo}: dígito fora de marcador em {_trecho(resto, numerico, numerico + 1)!r}")
    normal = unicodedata.normalize("NFKC", resto)  # letras de largura total, ligaduras...
    for padrao, motivo in [
        (ROMANOS, "algarismo romano"),
        (POR_EXTENSO, "número por extenso"),
        (MESES_E_PERIODOS, "mês ou período por extenso (use {mes_referencia}, {mes_ano_anterior}, {periodo_ytd}...)"),
        (MES_ANTERIOR, "as variações mensais são sobre o mesmo mês do ano anterior ({mes_ano_anterior}), não sobre o mês anterior"),
        (CONTAGENS, "contagem, sequência ou recorde que o pacote não traz"),
        (UNIDADES, "unidade (%, por cento, US$ ou p.p.) fora de marcador"),
        (MARCACAO, "caractere de marcação ou chave malformada"),
        (PROIBIDOS, "termo proibido (recomendação ou tom alarmista)"),
    ]:
        achado = padrao.search(normal)
        if achado:
            erros.append(f"{campo}: {motivo} em {_trecho(normal, achado.start(), achado.end())!r}")
    for achado in MARCADOR.finditer(texto):
        if _direcao_de(achado.group(1), fatos) and SINAL_ANTES.search(texto[: achado.start()]):
            erros.append(f"{campo}: sinal antes de {{{achado.group(1)}}}; as variações vão sem sinal e a direção vem do _sentido")
    if any(CHAVES_TROCA.match(m) for m in marcadores):
        if not NO_PORTO.search(resto):
            erros.append(f"{campo}: cita a relação de troca sem dizer que ela é medida no porto (e não é o preço ao produtor)")
        for achado in PRODUTOR.finditer(resto):
            if not NEGACAO.search(resto[max(0, achado.start() - 40) : achado.start()]):
                erros.append(f"{campo}: apresenta a relação de troca como preço ao produtor em {_trecho(resto, achado.start(), achado.end())!r}")
    erros += _direcoes_contraditorias(campo, texto, fatos)
    return erros, len(marcadores)


def _direcao_de(chave: str, fatos: Fatos) -> str | None:
    """A chave _sentido ou _posicao que dá a direção de `chave` (ela mesma, se já for uma)."""
    if chave.endswith(("_sentido", "_posicao")) and chave in fatos:
        return chave
    referencia = fatos.direcao.get(chave) if isinstance(fatos, Fatos) else None
    return referencia if referencia in fatos else None


def _esperado(referencia: str, fatos: Fatos) -> str:
    valor = fatos[referencia]["valor"]
    if referencia.endswith("_sentido"):
        return {"alta": "alta", "queda": "queda"}.get(valor, "nenhuma")
    return "alta" if valor.startswith("acima") else "queda" if valor.startswith("abaixo") else "nenhuma"


def _direcoes_contraditorias(campo: str, texto: str, fatos: Fatos) -> list[str]:
    """Confere as palavras de direção contra o _sentido (ou a _posicao) dos marcadores.

    - Para cada marcador com direção (variação, o seu _sentido, o valor que variou ou uma
      posição frente a uma referência), vale a palavra de direção mais próxima antes dele, na
      mesma oração; sem ela, um comparativo logo depois ("{var_x} maior") ou a palavra nos
      primeiros caracteres depois. Palavra contrária à direção reprova.
    - Toda palavra de mudança (subiu, caiu, alta, queda...) precisa, na mesma oração, de um
      marcador cuja direção seja um _sentido: sem ele não há como conferir e reprova."""
    erros = []
    limpo = _apagar_marcadores(texto)  # palavras dentro de marcadores não contam
    for achado in MARCADOR.finditer(texto):
        chave = achado.group(1)
        referencia = _direcao_de(chave, fatos)
        if referencia is None:
            continue
        esperado = _esperado(referencia, fatos)
        antes = limpo[: achado.start()]
        corte = max((m.end() for m in FIM_DE_ORACAO.finditer(antes)), default=0)
        janela = antes[max(corte, len(antes) - 60) :]
        palavras = [(m.start(), "alta", m.group(0)) for m in PALAVRAS_ALTA.finditer(janela)]
        palavras += [(m.start(), "queda", m.group(0)) for m in PALAVRAS_QUEDA.finditer(janela)]
        if not palavras:
            depois = limpo[achado.end() :]
            depois = depois[: next((m.start() for m in FIM_DE_ORACAO.finditer(depois)), len(depois))]
            logo = LOGO_DEPOIS_ALTA.match(depois) or LOGO_DEPOIS_QUEDA.match(depois)
            if logo:
                palavras = [(0, "alta" if LOGO_DEPOIS_ALTA.match(depois) else "queda", logo.group(1))]
            else:
                perto = depois[:25]
                palavras = [(0, "alta", m.group(0)) for m in PALAVRAS_ALTA.finditer(perto)][:1]
                palavras += [(0, "queda", m.group(0)) for m in PALAVRAS_QUEDA.finditer(perto)][:1]
        if not palavras:
            continue
        _, direcao, palavra = max(palavras)
        if direcao != esperado:
            erros.append(f"{campo}: '{palavra}' contradiz {{{chave}}}, cuja direção é '{fatos[referencia]['valor']}'")

    inicio = 0
    for fim in [m.start() for m in FIM_DE_ORACAO.finditer(limpo)] + [len(limpo)]:
        oracao = limpo[inicio:fim]
        mudanca = PALAVRAS_MUDANCA.search(oracao)
        if mudanca:
            ancoras = [m.group(1) for m in MARCADOR.finditer(texto) if inicio <= m.start() < fim]
            if not any((_direcao_de(k, fatos) or "").endswith("_sentido") for k in ancoras):
                erros.append(
                    f"{campo}: '{mudanca.group(0)}' indica direção, mas a frase não cita uma variação do pacote que a"
                    " confirme; cite o marcador da variação (ou o seu _sentido) na mesma frase, ou tire a palavra"
                )
        inicio = fim + 1
    return erros


def substituir(texto: str, fatos: Fatos) -> str:
    return MARCADOR.sub(lambda m: fatos[m.group(1)]["valor"], texto)


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
    return final, []


# --- Geração ------------------------------------------------------------------------------


class ProvedorMock:
    """Resposta fixa de exemplo, sem nenhuma API: para testes, CI e demonstração."""

    nome = "mock"
    modelo = "resposta-fixa"

    def gerar(self, sistema: str, usuario: str, *, esquema: dict | None = None) -> llm.Resposta:
        tem_troca = '"troca_map_ultimo"' in usuario
        origem = (
            "{lider_total_ano} segue como principal origem no ano, com {share_lider_total_ano} das toneladas. Entre as"
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
            else "A concentração de fornecedores de potássicos, medida pelo HHI, ficou em {hhi_k_ano} no ano; acima de"
            " {limiar_hhi}, um mercado é considerado altamente concentrado."
            if '"hhi_k_ano"' in usuario
            else "Os nitrogenados somaram {t_n_mes} em {mes_referencia_curto}, e os potássicos, {t_k_mes}."
        )
        resposta = {
            "titulo": "Importações de fertilizantes em {mes_referencia}",
            "lead": (
                "O Brasil importou {t_total_mes} de fertilizantes em {mes_referencia}, uma {var_t_total_mes_sentido}"
                " de {var_t_total_mes} sobre {mes_ano_anterior}."
            ),
            "paragrafos": [
                "No acumulado de {periodo_ytd}, as compras somaram {t_total_ytd}, com {var_t_total_ytd_sentido} de"
                " {var_t_total_ytd} sobre o mesmo período de {ano_anterior}. Os potássicos responderam por {share_k_ytd}"
                " do volume e os nitrogenados por {share_n_ytd}.",
                "O preço médio CIF de todos os grupos ficou em {preco_cif_total_mes} no mês. No ano, o frete médio foi de"
                " {frete_medio_ytd}, o equivalente a {frete_pct_cif_ytd} do valor CIF.",
                origem,
                ultimo,
            ],
        }
        return llm.Resposta(json.dumps(resposta, ensure_ascii=False), self.modelo)


def _nome_do_modelo(*candidatos) -> str:
    """O nome do modelo vai para o .md e o card sem passar pela validação do texto: só entra se
    tiver cara de nome de modelo (o devolvido pela API, senão o configurado)."""
    return next((c for c in candidatos if isinstance(c, str) and MODELO_VALIDO.fullmatch(c)), "não informado")


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
        log.warning("Tentativa %d/%d reprovada na validação:\n  %s", tentativa, TENTATIVAS, "\n  ".join(erros))
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
        f"*Boletim de {meta['mes_referencia']} · importações brasileiras de fertilizantes · Fonte: {FONTE}*",
        "",
        f"**{conteudo['lead']}**",
        "",
    ]
    for paragrafo in conteudo["paragrafos"]:
        linhas += [paragrafo, ""]
    linhas += ["<details>", "<summary>Números citados (inseridos pelo programa a partir do pacote de fatos)</summary>", ""]
    linhas += ["| Marcador | Valor |", "| --- | --- |"]
    linhas += [f"| `{chave}` | {valor} |" for chave, valor in conteudo["marcadores"].items()]
    linhas += ["", "</details>", "", "---", "", f"*{RODAPE}*", ""]
    linhas += [f"<sub>Provedor: {meta['provedor']} · modelo: {meta['modelo']} · gerado em {meta['gerado_em']} · [dashboard]({meta['dashboard']})</sub>", ""]
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
        "fonte": FONTE,
        "rodape": RODAPE,
        "provedor": provedor,
        "modelo": conteudo["modelo"],
        "mock": mock,
        "gerado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arquivo": f"boletins/{arquivo.name}",
        "dashboard": dashboard,
    }
    ultimo = {**meta, "titulo": conteudo["titulo"], "lead": conteudo["lead"], "paragrafos": conteudo["paragrafos"], "marcadores": conteudo["marcadores"]}
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
