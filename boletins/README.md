# Boletins mensais

Um boletim por mês de dados, gerado pelo workflow [`atualizacao.yml`](../.github/workflows/atualizacao.yml)
com [`src/boletim.py`](../src/boletim.py):

- `AAAA-MM.md`: o boletim do mês, com a tabela dos números citados;
- `latest.json`: o boletim mais recente, lido pelo card do [dashboard](https://alicecullen-html.github.io/radar-fertilizantes-br/).

O texto é escrito por IA, mas os números não: a IA só cita marcadores de um pacote de fatos
calculado pelo programa, que os troca pelos valores e reprova qualquer número escrito fora deles.
Detalhes no [README](../README.md#como-a-ia-é-usada-e-por-que-ela-não-inventa-números).

Texto gerado por IA a partir de dados do Comex Stat/MDIC; números inseridos e verificados automaticamente.

Fonte: Comex Stat/MDIC.
