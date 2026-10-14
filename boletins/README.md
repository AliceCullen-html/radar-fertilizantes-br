# Boletins mensais

Um boletim por mês de dados, gerado pelo workflow [`atualizacao.yml`](../.github/workflows/atualizacao.yml)
com [`src/boletim.py`](../src/boletim.py):

- `AAAA-MM.md`: o boletim do mês, com a tabela dos números citados;
- `latest.json`: o boletim mais recente, lido pelo card do [dashboard](https://alicecullen-html.github.io/radar-fertilizantes-br/).

O texto é escrito por IA, mas os números não: a IA só cita marcadores de um pacote de fatos
calculado pelo programa, que os troca pelos valores e reprova qualquer número escrito fora deles.
Detalhes no [README](../README.md#como-a-ia-é-usada-e-por-que-ela-não-inventa-números).

Texto gerado por IA a partir de dados do Comex Stat/MDIC; números inseridos e verificados automaticamente.
Quando um boletim cita números do benchmark internacional, o rodapé e as fontes dele incluem o
Banco Mundial (Pink Sheet), com uma nota fixa: o benchmark é uma referência de outro lugar e de
outro momento de contrato, e o spread não é ágio (e, para o MAP, a referência é o DAP). Quando
cita a PTAX ou o CIF em reais, incluem o BCB (PTAX); com o CIF em reais, entra a nota de que ele
usa a PTAX média do mês. As licenças das fontes (CC BY 4.0 e ODbL) estão no
[README](../README.md#licenças-das-fontes).

Fonte: Comex Stat/MDIC; Banco Mundial (Pink Sheet); BCB/PTAX.
