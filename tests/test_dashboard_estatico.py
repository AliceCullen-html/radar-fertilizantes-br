"""Testes das peças estáticas do dashboard (sem rede e sem navegador): fontes self-hosted e
referências externas da página. O mapa de pontos é testado em test_mapa.py."""

import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DASH = RAIZ / "dashboard"


def test_fontes_self_hosted_com_licenca():
    fontes = DASH / "assets" / "fontes"
    css = (DASH / "assets" / "radar.css").read_text(encoding="utf-8")
    for arquivo in re.findall(r'url\("(fontes/[^"]+\.woff2)"\)', css):
        assert (DASH / "assets" / arquivo).exists(), arquivo
    assert len(re.findall(r"@font-face", css)) == 5
    for licenca in ("OFL-InterTight.txt", "OFL-JetBrainsMono.txt"):
        assert "SIL Open Font License" in (fontes / licenca).read_text(encoding="utf-8")


FUNCAO_LISTA = "https://recqdysmwfltkrhrhdjx.supabase.co/functions/v1/lista-espera"
TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=radarTurnstilePronto"


def test_terceiros_so_jsdelivr_turnstile_e_a_funcao_da_lista_de_espera():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    # carregamentos do HTML (src/href de script e link) e @import/url no CSS: só o próprio site e o jsDelivr
    carregados = re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https?://[^"]+)"', html)
    assert carregados and all(u.startswith("https://cdn.jsdelivr.net/npm/") for u in carregados), carregados
    for arquivo in (DASH / "assets").glob("*.css"):
        assert not re.search(r"(?:url\(|@import)\s*[\"']?https?://", arquivo.read_text(encoding="utf-8")), arquivo
    # nos scripts, os únicos endereços externos: o Turnstile (só no lista-espera.js) e o namespace do SVG
    for arquivo in (DASH / "assets").glob("*.js"):
        texto = arquivo.read_text(encoding="utf-8")
        assert not re.search(r"fetch\(\s*[\"'`]https?://", texto), arquivo
        enderecos = set(re.findall(r"https?://[^\s\"'`)]+", texto))
        permitidos = {"http://www.w3.org/2000/svg"} | ({TURNSTILE} if arquivo.name == "lista-espera.js" else set())
        assert enderecos <= permitidos, (arquivo.name, enderecos - permitidos)
    # o formulário da lista de espera só fala com a Edge Function do projeto, por HTTPS
    assert re.findall(r'data-funcao="([^"]+)"', html) == [FUNCAO_LISTA]


def test_lista_de_espera_textos_turnstile_isca_e_um_botao_so():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    secao = re.search(r'<section class="cartao grade lista-espera".*?</section>', html, re.S).group(0)
    assert ">Receba alertas do seu mercado</h2>" in secao
    assert ("Alerta no dia em que o dado sai, já interpretado para o seu recorte: o produto, a origem e o porto que "
            "importam para você. Entre na lista para ser avisado quando abrir.") in secao
    assert "Pronto! Você está na lista." in secao
    # aviso LGPD curto: para que servem os dados, nada de venda ou compartilhamento, exclusão por e-mail
    lgpd = re.search(r'<p class="le-lgpd" id="le-lgpd">(.*?)</p>', secao, re.S).group(1)
    for trecho in ("só para avisar sobre o produto", "Não são vendidos nem compartilhados", "contato@graaos.cloud",
                   "Supabase", "Cloudflare", "#lista-de-espera-e-privacidade"):
        assert trecho in lgpd, trecho
    # site key pública do Turnstile como constante no HTML; o segredo nunca aparece no site
    assert 'data-sitekey="0x4AAAAAAFYdiHT5aqpj-sA2"' in secao
    # campos espelhando a tabela; isca fora do teclado e dos leitores de tela
    for nome in ("nome", "email", "empresa", "cargo", "segmento", "interesses", "consentimento_lgpd", "website"):
        assert f'name="{nome}"' in secao, nome
    assert re.search(r'<div class="le-isca" aria-hidden="true" inert>', secao)
    assert re.search(r'<input id="le-website"[^>]+tabindex="-1"', secao)
    valores = re.findall(r'<option value="([a-z]+)"', secao)
    assert valores == ["revenda", "cooperativa", "trading", "industria", "logistica", "consultoria", "produtor", "outro"]
    assert re.findall(r'name="interesses" value="([a-z]+)"', secao) == ["alertas", "watchlist", "api", "relatorios", "mcp"]
    # sem JavaScript nada sai: POST (nunca os dados na URL), botão desligado até o script assumir e um aviso
    assert re.search(r'<form class="le-form" id="le-form" method="post" novalidate', secao)
    assert re.search(r'<button class="le-botao" id="le-enviar" type="submit" disabled>', secao)
    assert "<noscript>" in secao
    assert "botao.disabled = false;" in (DASH / "assets" / "lista-espera.js").read_text(encoding="utf-8")
    # um único botão de ação na seção e nada de doação no site
    assert len(re.findall(r"<button\b", secao)) == 1
    assert not re.search(r"\b(?:doa[çc][ãa]o|doar|sponsors?|patroc[ií]nio|apoie|pix)\b", html, re.I)


class _Secoes(HTMLParser):
    """Ids das <section> filhas diretas do <main>, na ordem da página."""

    def __init__(self):
        super().__init__()
        self.no_main, self.profundidade, self.ids = False, 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.no_main = True
        elif tag == "section" and self.no_main:
            if self.profundidade == 0:
                self.ids.append(dict(attrs).get("id"))
            self.profundidade += 1

    def handle_endtag(self, tag):
        if tag == "main":
            self.no_main = False
        elif tag == "section" and self.no_main:
            self.profundidade -= 1


def test_lista_de_espera_no_fim_da_pagina_com_o_item_receber_alertas_no_menu():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    # os dados vêm primeiro: a lista fica depois da Troca e antes do Método (e do rodapé), fora dos blocos numerados
    secoes = _Secoes()
    secoes.feed(html)
    dados = ["boletim", "volumes", "origens", "fluxos", "portos", "precos", "troca"]
    assert secoes.ids == [*dados, "lista-espera", "metodo"]
    # menu: as seções na ordem da página e, no fim, o convite, que rola até a lista (sem mexer no hash dos filtros)
    menu = re.search(r'<nav class="nav-pilula" id="nav-secoes".*?</nav>', html, re.S).group(0)
    assert re.findall(r'<a href="#(\w+)" data-secao="\1">', menu) == [*dados, "metodo"]
    assert menu.rstrip().endswith('<a class="nav-alertas" href="#lista-espera" data-secao="lista-espera">Receber alertas</a>\n  </nav>')
    # o título é o alvo do foco do clique (t-<seção>, como nos blocos) e dá nome à seção
    assert '<section class="cartao grade lista-espera" aria-labelledby="t-lista-espera" id="lista-espera">' in html
    assert '<h2 class="le-titulo" id="t-lista-espera" tabindex="-1">Receba alertas do seu mercado</h2>' in html
    # o convite fica fora do indicador do menu (que segue a ordem das seções) e tem contorno próprio
    assert 'nav.querySelectorAll("a[data-secao]:not(.nav-alertas)")' in (DASH / "assets" / "hero.js").read_text(encoding="utf-8")
    css = (DASH / "assets" / "radar.css").read_text(encoding="utf-8")
    assert re.search(r"\.nav-pilula a\.nav-alertas \{[^}]*box-shadow: inset 0 0 0 1px", css)


def test_turnstile_tem_aviso_no_lugar_ate_carregar():
    # o script só carrega quando alguém usa o formulário; até lá, um aviso ocupa o lugar do widget (nada de caixa
    # vazia), e ele sai da caixa logo antes de o Turnstile desenhar o widget
    html = (DASH / "index.html").read_text(encoding="utf-8")
    caixa = re.search(r'<div class="le-turnstile" id="le-turnstile" data-sitekey="[^"]+">(.*?)</div>', html, re.S).group(1)
    assert 'id="le-turnstile-espera"' in caixa
    assert "<span>A verificação aparece aqui quando você começar a preencher.</span>" in caixa
    js = (DASH / "assets" / "lista-espera.js").read_text(encoding="utf-8")
    renderizar = re.search(r"function renderizar\(\) \{(.*?)\n  \}", js, re.S).group(1)
    assert renderizar.index("caixaTurnstile.replaceChildren()") < renderizar.index("window.turnstile.render(")
    assert 'aviso("Carregando a verificação…")' in js and 'aviso("A verificação não carregou.")' in js
    assert re.search(r"\.le-turnstile-espera \{[^}]*min-height: 65px", (DASH / "assets" / "radar.css").read_text(encoding="utf-8"))


def _regexes(texto):
    """Os `const NOME = /.../flags;` de um arquivo JS ou TS."""
    literal = r"/(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\n\[])+/[a-z]*"
    return dict(re.findall(rf"const (\w+) = ({literal});", texto))


def _textos(bloco):
    return dict(re.findall(r'(\w+): "([^"]*)"', bloco))


def test_formulario_espelha_as_regras_da_funcao():
    js = (DASH / "assets" / "lista-espera.js").read_text(encoding="utf-8")
    ts = (RAIZ / "supabase" / "functions" / "lista-espera" / "inscricao.ts").read_text(encoding="utf-8")
    regras_js, regras_ts = _regexes(js), _regexes(ts)
    for nome in ("INVISIVEIS", "MARCAS_EMPILHADAS", "ESPACOS", "MARCACAO", "FORMULA", "EMAIL", "ASCII_VISIVEL",
                 "DUAS_LETRAS", "LETRA_OU_NUMERO"):
        assert nome in regras_js and regras_js[nome] == regras_ts.get(nome), nome
    # as mesmas mensagens (o formulário usa um subconjunto das da função)
    mensagens_js = _textos(re.search(r"const MENSAGENS = \{(.*?)\};", js, re.S).group(1))
    mensagens_ts = _textos(re.search(r"export const MENSAGENS = \{(.*?)\} as const;", ts, re.S).group(1))
    assert mensagens_js and all(mensagens_ts.get(chave) == texto for chave, texto in mensagens_js.items()), mensagens_js
    # as mesmas listas de segmento e interesse, e os mesmos caracteres na origem (utm)
    for nome in ("SEGMENTOS", "INTERESSES"):
        lista_js = re.findall(r'"([a-z]+)"', re.search(rf"const {nome} = \[(.*?)\]", js, re.S).group(1))
        lista_ts = re.findall(r'"([a-z]+)"', re.search(rf"const {nome} = \[(.*?)\]", ts, re.S).group(1))
        assert lista_js == lista_ts, nome
    utm_ts = re.search(r"const VALOR_UTM = /\^\[([^\]]+)\]", ts).group(1)
    utm_js = re.search(r"const limpar = .*?replace\(/\[\^([^\]]+)\]/gu", js).group(1)
    assert utm_js == utm_ts


def test_sem_caracteres_invisiveis_literais_no_codigo():
    # as regras de texto usam escapes (\u200b), nunca o caractere invisível em si, que some na revisão
    arquivos = [DASH / "index.html", *sorted((DASH / "assets").glob("*.js")), *sorted((DASH / "assets").glob("*.css")),
                *sorted((RAIZ / "supabase").rglob("*.ts"))]
    for arquivo in arquivos:
        texto = arquivo.read_text(encoding="utf-8")
        ruins = {f"U+{ord(c):04X}" for c in texto if unicodedata.category(c) in {"Cc", "Cf", "Co", "Cs", "Cn"} and c not in "\n\r\t"}
        assert not ruins, (arquivo.name, sorted(ruins))


def test_site_sem_segredos():
    # o site é público: nada de chave secreta do Supabase, JWT, service_role ou segredo do Turnstile
    proibidos = re.compile(r"sb_secret_|service_role|eyJhbGciOi|SUPABASE_SERVICE_ROLE_KEY|TURNSTILE_SECRET|IP_HASH_SALT", re.I)
    for arquivo in [DASH / "index.html", *(DASH / "assets").glob("*.js"), *(DASH / "assets").glob("*.css")]:
        assert not proibidos.search(arquivo.read_text(encoding="utf-8")), arquivo


def test_pagina_cita_as_fontes_e_a_base_cartografica():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    assert "Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX" in html
    assert "Base cartográfica: Natural Earth" in html
    assert "no porto" in html and "não é o preço ao produtor" in html.lower()


def test_anime_js_com_versao_fixa_e_sri():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<script src="https://cdn\.jsdelivr\.net/npm/animejs@([^/"]+)/[^"]+"([^>]*)>', html)
    assert tag and re.fullmatch(r"4\.\d+\.\d+", tag.group(1)), "anime.js v4 com versão fixa"
    assert 'integrity="sha384-' in tag.group(2) and 'crossorigin="anonymous"' in tag.group(2)
