"""Licenças do repositório: código sob MIT, dados tratados de data/ sob CC BY 4.0, e as linhas do
README e do rodapé do site que dizem isso."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CREDITO = "Graaos (github.com/mvsantos-dev/graaos)"


def test_codigo_mit_e_dados_tratados_cc_by():
    codigo = (RAIZ / "LICENSE").read_text(encoding="utf-8")
    assert codigo.startswith("MIT License\n") and "Copyright (c) 2026 Marcus Santos" in codigo
    dados = (RAIZ / "data" / "LICENSE").read_text(encoding="utf-8")
    assert "CC BY 4.0" in dados and f"Atribuição obrigatória: {CREDITO}" in dados
    assert "https://creativecommons.org/licenses/by/4.0/" in dados
    # os dados de origem continuam com as licenças das fontes, descritas no README
    for fonte in ("Comex Stat/MDIC", "Banco Mundial (Pink Sheet)", "BCB/PTAX: ODbL"):
        assert fonte in dados, fonte
    assert "https://github.com/mvsantos-dev/graaos#licenças-das-fontes" in dados


def test_readme_e_rodape_do_site_citam_as_licencas():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    topo = readme.split("\n## ", 1)[0]
    assert "[![Licença: MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-blue)](LICENSE)" in topo
    assert "\n### Licenças das fontes\n" in readme
    assert "Código: [MIT](LICENSE) · Dados tratados: [CC BY 4.0](data/LICENSE)" in readme
    assert "\n## O que é aberto e o que será pago\n" in readme
    assert "Nada do que hoje é aberto vai migrar para a parte paga." in readme
    html = (RAIZ / "dashboard" / "index.html").read_text(encoding="utf-8")
    rodape = re.search(r'<footer class="rodape">(.*?)</footer>', html, re.S).group(1)
    assert 'Código aberto (MIT) · dados CC BY 4.0: <a href="https://github.com/mvsantos-dev/graaos">' in rodape
