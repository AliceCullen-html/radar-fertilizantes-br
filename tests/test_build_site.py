"""Testes do build_site.sh (o site do deploy na Cloudflare e no GitHub Pages). Sem rede."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "build_site.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="precisa de bash")


def _rodar(raiz: Path, saida: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(raiz / "build_site.sh"), str(saida)], capture_output=True, text=True, timeout=60)


def test_monta_o_site_com_os_dados_na_raiz(tmp_path):
    saida = tmp_path / "site"
    r = _rodar(RAIZ, saida)
    assert r.returncode == 0, r.stderr
    # o index.html busca "dashboard.json" e "boletim.json" ao lado dele
    html = (saida / "index.html").read_text(encoding="utf-8")
    assert '"dashboard.json"' in html and '"boletim.json"' in html
    original = (RAIZ / "data" / "processed" / "dashboard.json").read_bytes()
    assert (saida / "dashboard.json").read_bytes() == original
    assert "mapa" in json.loads(original)
    json.loads((saida / "boletim.json").read_text(encoding="utf-8"))
    for arquivo in ("assets/mapa-pontos.json", "assets/fluxos.js", "assets/hero.js", "assets/mapa-hero.js", "assets/radar.css"):
        assert (saida / arquivo).stat().st_size > 0, arquivo
    boletim = RAIZ / "boletins" / "latest.json"
    if boletim.exists():
        assert (saida / "boletim.json").read_bytes() == boletim.read_bytes()


def test_wrangler_publica_a_pasta_do_build():
    texto = (RAIZ / "wrangler.jsonc").read_text(encoding="utf-8")
    sem_comentarios = "\n".join(linha for linha in texto.splitlines() if not linha.lstrip().startswith("//"))
    config = json.loads(sem_comentarios)
    assert config["assets"]["directory"] == "./site"
    assert not ({"account_id", "vars"} & set(config)), "nada de conta ou variáveis no repositório público"


def _repo_falso(tmp_path: Path, dashboard: str | None, index: bool = True) -> Path:
    raiz = tmp_path / "repo"
    (raiz / "dashboard" / "assets").mkdir(parents=True)
    shutil.copy(SCRIPT, raiz / "build_site.sh")
    if index:
        (raiz / "dashboard" / "index.html").write_text(
            '<meta name="atualizado" content=""><script>fetch("dashboard.json"); fetch("boletim.json")</script>', encoding="utf-8")
    (raiz / "dashboard" / "assets" / "mapa-pontos.json").write_text('{"grade": [], "paises": []}', encoding="utf-8")
    for nome in ("fluxos.js", "hero.js", "mapa-hero.js", "radar.css"):
        (raiz / "dashboard" / "assets" / nome).write_text("/* */", encoding="utf-8")
    if dashboard is not None:
        (raiz / "data" / "processed").mkdir(parents=True)
        (raiz / "data" / "processed" / "dashboard.json").write_text(dashboard, encoding="utf-8")
    return raiz


COMPLETO = json.dumps({k: {} for k in ("meta", "mensal", "anual", "origens", "urfs", "rotas", "mapa")})


def test_repo_falso_completo_monta_sem_erro(tmp_path):
    raiz = _repo_falso(tmp_path, COMPLETO)
    r = _rodar(raiz, raiz / "site")
    assert r.returncode == 0, r.stderr
    assert "sem histórico do git" in r.stderr  # fora de um repositório git, só avisa
    assert json.loads((raiz / "site" / "boletim.json").read_text()) == {}


@pytest.mark.parametrize(
    ("dashboard", "index", "mensagem"),
    [
        (None, True, "data/processed/dashboard.json não existe"),
        ("{ isto não é JSON", True, "dashboard.json inválido"),
        (json.dumps({"meta": {}, "mensal": {}}), True, "sem as chaves"),
        (COMPLETO, False, "dashboard/index.html não existe"),
    ],
)
def test_build_falha_sem_dados_validos(tmp_path, dashboard, index, mensagem):
    raiz = _repo_falso(tmp_path, dashboard, index=index)
    r = _rodar(raiz, raiz / "site")
    assert r.returncode == 1
    assert mensagem in r.stderr
