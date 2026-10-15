"""Mapa-múndi em pontos (dot matrix) para o dashboard, gerado a partir do Natural Earth.

Script de desenvolvimento: roda à mão, uma vez, e o resultado fica versionado em
dashboard/assets/mapa-pontos.json. O deploy não roda nada daqui e o navegador não faz
nenhum cálculo de geografia: só desenha os pontos já prontos.

    python scripts/gerar_mapa_pontos.py                  # baixa o Natural Earth 1:110m
    python scripts/gerar_mapa_pontos.py --geojson arq    # ou usa um arquivo local

Base cartográfica: Natural Earth, Admin 0 – Countries, 1:110m (domínio público,
https://www.naturalearthdata.com/about/terms-of-use/). Fica de fora a Antártida.

Projeção equirretangular: cada ponto é o centro de uma célula de PASSO graus. Um ponto entra
se o centro da célula cai dentro de algum país (regra par-ímpar, que respeita os buracos dos
polígonos). Cada linha da grade vira uma lista plana de trincas [coluna inicial, quantidade,
índice do país], e cada país guarda o ponto de rótulo do Natural Earth (LABEL_X, LABEL_Y),
que o dashboard usa como ponta dos arcos de rota.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "dashboard" / "assets" / "mapa-pontos.json"
VERSAO = "v5.1.2"
URL = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{VERSAO}/geojson/ne_110m_admin_0_countries.geojson"

PASSO = 1.5  # graus por célula
OESTE, NORTE, SUL = -180.0, 84.0, -57.0  # sem a Antártida


def _codigo(props: dict) -> str:
    """ISO 3166-1 alfa-2 do país; ISO_A2_EH corrige os -99 de França e Noruega no Natural Earth."""
    for campo in ("ISO_A2_EH", "ISO_A2"):
        valor = str(props.get(campo) or "")
        if len(valor) == 2 and valor.isalpha():
            return valor.upper()
    return "-" + str(props.get("ADM0_A3") or props.get("NAME") or "?")


def _aneis(geometria: dict) -> list[list[np.ndarray]]:
    """Polígonos da geometria, cada um como lista de anéis (o primeiro é o exterior)."""
    if geometria["type"] == "Polygon":
        poligonos = [geometria["coordinates"]]
    elif geometria["type"] == "MultiPolygon":
        poligonos = geometria["coordinates"]
    else:
        return []
    return [[np.asarray(anel, dtype=float)[:, :2] for anel in poligono] for poligono in poligonos]


def _dentro(lon: np.ndarray, lat: np.ndarray, aneis: list[np.ndarray]) -> np.ndarray:
    """Regra par-ímpar (ray casting) de vários pontos contra os anéis de um polígono."""
    dentro = np.zeros(lon.shape, dtype=bool)
    for anel in aneis:
        x1, y1 = anel[:, 0], anel[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        for a, b, c, d in zip(x1, y1, x2, y2):
            if b == d:
                continue
            cruza = (b > lat) != (d > lat)
            x = a + (lat - b) * (c - a) / (d - b)
            dentro ^= cruza & (lon < x)
    return dentro


def rasterizar(geojson: dict, passo: float = PASSO, oeste: float = OESTE, norte: float = NORTE, sul: float = SUL) -> dict:
    """Grade de pontos com o país de cada um. Pura: não acessa a rede nem o disco."""
    colunas = int(round(360 / passo))
    linhas = int(round((norte - sul) / passo))
    lon_c = oeste + (np.arange(colunas) + 0.5) * passo
    lat_c = norte - (np.arange(linhas) + 0.5) * passo
    grade = np.full((linhas, colunas), -1, dtype=int)
    paises: list[str] = []
    rotulos: dict[str, list[float]] = {}
    nomes: dict[str, str] = {}
    for feicao in geojson["features"]:
        props = feicao.get("properties") or {}
        codigo = _codigo(props)
        if codigo == "AQ" or props.get("CONTINENT") == "Antarctica":
            continue
        indice_atual = None
        for aneis in _aneis(feicao["geometry"]):
            todos = np.concatenate(aneis)
            lon0, lon1 = todos[:, 0].min(), todos[:, 0].max()
            lat0, lat1 = todos[:, 1].min(), todos[:, 1].max()
            cols = np.nonzero((lon_c >= lon0) & (lon_c <= lon1))[0]
            lins = np.nonzero((lat_c >= lat0) & (lat_c <= lat1))[0]
            if not len(cols) or not len(lins):
                continue
            mlon, mlat = np.meshgrid(lon_c[cols], lat_c[lins])
            dentro = _dentro(mlon, mlat, aneis)
            if not dentro.any():
                continue
            if indice_atual is None:
                if codigo not in paises:
                    paises.append(codigo)
                indice_atual = paises.index(codigo)
            sub = grade[np.ix_(lins, cols)]
            sub[dentro] = indice_atual
            grade[np.ix_(lins, cols)] = sub
        if indice_atual is not None:
            x, y = props.get("LABEL_X"), props.get("LABEL_Y")
            if x is not None and y is not None:
                rotulos[codigo] = [round(float(x), 2), round(float(y), 2)]
            nomes[codigo] = str(props.get("NAME_PT") or props.get("NAME") or codigo)
    rle = []
    for linha in grade:
        trincas: list[int] = []
        c = 0
        while c < colunas:
            p = linha[c]
            if p < 0:
                c += 1
                continue
            inicio = c
            while c < colunas and linha[c] == p:
                c += 1
            trincas += [inicio, c - inicio, int(p)]
        rle.append(trincas)
    return {
        "projecao": "equirretangular",
        "passo": passo,
        "oeste": oeste,
        "norte": norte,
        "colunas": colunas,
        "linhas": linhas,
        "pontos": int((grade >= 0).sum()),
        "paises": paises,
        "nomes": nomes,
        "rotulos": rotulos,
        "grade": rle,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--geojson", type=Path, help="arquivo local do Natural Earth (padrão: baixa a versão fixa)")
    parser.add_argument("--saida", type=Path, default=SAIDA)
    parser.add_argument("--passo", type=float, default=PASSO)
    args = parser.parse_args(argv)
    if args.geojson:
        bruto = args.geojson.read_bytes()
        origem = str(args.geojson.name)
    else:
        import requests

        resp = requests.get(URL, timeout=120)
        resp.raise_for_status()
        bruto, origem = resp.content, URL
    mapa = rasterizar(json.loads(bruto), passo=args.passo)
    saida = {
        "fonte": f"Natural Earth, Admin 0 – Countries, 1:110m ({VERSAO}); domínio público",
        "origem": origem,
        "sha256": hashlib.sha256(bruto).hexdigest(),
        **mapa,
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{args.saida}: {mapa['pontos']} pontos, {len(mapa['paises'])} países, {args.saida.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
