# Eleições 2026 · 2º turno em Belém (PA), por seção, local de votação e bairro

Mapa interativo do resultado do 2º turno da eleição presidencial de 2026 em Belém, com dados oficiais do
Tribunal Superior Eleitoral (TSE). Cada ponto é colorido pelo candidato mais votado:
🔴 **vermelho = Lula (PT)** · 🔵 **azul = Flávio Bolsonaro (PL)** · ⚪ cinza = empate.

🔗 **Mapa online:** `https://rfsilveira.github.io/eleicoes-2026-belem/` 
## Resultado (Belém, votos válidos)

| | Lula (PT) | Flávio Bolsonaro (PL) |
|---|---|---|
| Votos | 468.891 (55,01%) | 383.457 (44,99%) |
| Locais de votação vencidos (375) | 317 | 58 |
| Seções vencidas (2.989; 23 empates) | 2.373 | 593 |
| Bairros vencidos (60 com local de votação) | 54 | 6 |

A soma das seções confere com o total oficial do município (diferença zero para os dois candidatos).

## O que o mapa mostra

- **Locais de votação (escolas):** um círculo por local, com tamanho proporcional aos votos válidos.
- **Seções eleitorais:** um ponto por seção. A posição dentro da escola é apenas ilustrativa.
- **Limites dos bairros** e **Bairros: vencedor**, pintado pela soma dos locais de votação de cada bairro.
- Fundos de mapa: Esri (claro, ruas e satélite) e, quando servido por `http://`, OpenStreetMap e CARTO.

## Como reproduzir

```bash
git clone https://github.com/rfsilveira/eleicoes-2026-belem.git
cd eleicoes-2026-belem
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Baixe os dois arquivos do TSE para `data/raw/secoes/` (a pasta já existe; os CSV não são versionados):

1. **Boletim de urna do 2º turno, Pará:** [Portal de Dados Abertos do TSE, Resultados 2026, Boletim de Urna](https://dadosabertos.tse.jus.br/dataset/resultados-2026-boletim-de-urna)
   (arquivo `bweb_2t_PA_*.zip`, que traz o CSV do estado inteiro).
2. **Eleitorado por local de votação 2026:** [Portal de Dados Abertos do TSE, Eleitorado 2026](https://dadosabertos.tse.jus.br/dataset/eleitorado-2026)
   (arquivo `eleitorado_local_votacao_2026.zip`, todos os estados).

O script aceita os arquivos originais do TSE (separador `;`) ou recortes só de Belém, e filtra o município sozinho:

```bash
python mapa_secoes_belem.py \
  --bu data/raw/secoes/bweb_2t_PA_BEL_29102026.csv \
  --locais data/raw/secoes/eleitorado_local_votacao_2026_PA_belem.csv \
  --total-lula 468891 --total-flavio 383457      # opcional: confere com o total oficial
```

Abra `saida/mapa_belem_2t.html` no navegador. Com `--servir` o mapa abre em `http://localhost:8000`.
Saem também CSVs e GeoJSONs de seções, locais e bairros (abrem no QGIS) e `locais_bairro_conferencia.csv`.
Para publicar a versão atual, copie `saida/mapa_belem_2t.html` para `docs/index.html`.

## Notas de método

- **Chave de ligação:** município + zona + seção. O número do local de votação **não** serve de chave: é numerado por
  zona, e o boletim de urna traz o local *original* da seção, não o temporário em que a votação ocorreu
  (160 seções em Belém). A coordenada usada é a do local efetivamente usado no pleito.
- **Só há pontos.** O TSE não publica polígono de seção, apenas a coordenada do local de votação.
- **Resultado por bairro = soma das escolas dentro do polígono**, não dos eleitores que moram ali.
- **Coordenadas ruins no cadastro do TSE** (7 locais) são trocadas pelo centro do bairro e aparecem com contorno tracejado.
- **Ilhas ficam fora do resultado por bairro** (9 locais, 4.870 votos válidos, 0,6%): a base de bairros não as inclui.
  Elas aparecem no mapa de pontos.
- Seções agregadas não têm votos próprios (entram na seção principal). Percentuais sobre votos válidos.

## Créditos e fontes

- **Dados eleitorais:** Tribunal Superior Eleitoral, [Portal de Dados Abertos](https://dadosabertos.tse.jus.br/)
  (boletim de urna e eleitorado por local de votação, 2026; licença Creative Commons Atribuição).
- **Limites dos bairros:** [samuel-c-santos/geovisor-belem](https://github.com/samuel-c-santos/geovisor-belem),
  de Samuel Santos (README declara licença MIT), a partir de IBGE e CODEM/Prefeitura de Belém. Detalhes em
  [`data/geo/LEIAME.md`](data/geo/LEIAME.md).
- **Referência de método e conferência:** [LADRI (ASCES-UNITA), *Eleições 2026*](https://github.com/ladriasces/Elei-es-2026---LADRI),
  análise municipal da eleição presidencial de 2026, usada como ponto de partida para a organização da análise e de onde
  vieram os totais municipais de Belém usados na conferência. **Este repositório não contém código nem arquivos do
  LADRI**; o LADRI trabalha no nível de município e a análise por seção e bairro é feita aqui.
- **Mapa:** [Leaflet](https://leafletjs.com/) (BSD-2-Clause). Fundos: Esri, © OpenStreetMap contributors, © CARTO.
- **Desenvolvimento:** código escrito com o auxílio do **Claude**, assistente de IA da [Anthropic](https://www.anthropic.com/)
  (via Claude Code). A definição da análise, a escolha dos recortes e a revisão dos resultados são do autor.

## Autor

[rfsilveira](https://github.com/rfsilveira)
