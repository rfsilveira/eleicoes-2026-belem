"""
mapa_secoes_belem.py — Mapa do 2º turno (Presidente) por seção eleitoral e local de votação em Belém (PA).

Cada ponto é colorido pelo candidato que venceu:  Lula = vermelho  |  Flávio Bolsonaro = azul.

Entradas (dados abertos do TSE; veja o README.md do projeto):
  --bu       Boletim de urna do 2º turno (bweb_2t_PA_*.csv, já filtrado em Belém ou o do estado inteiro)
  --locais   Eleitorado por local de votação 2026 (eleitorado_local_votacao_2026*.csv, Belém ou Brasil inteiro)

Saídas (pasta --saida, por padrão saida/):
  mapa_belem_2t.html            mapa interativo (abre em qualquer navegador; usa Leaflet via internet)
  secoes_belem_2t.csv           uma linha por seção apurada, com votos, vencedor e local de votação
  locais_belem_2t.csv           uma linha por local de votação (escola), somando as suas seções
  secoes_belem_2t.geojson       pontos das seções (abre no QGIS)
  locais_belem_2t.geojson       pontos dos locais de votação (abre no QGIS)
  sem_coordenada.csv            locais cuja coordenada está ausente/errada no cadastro do TSE (se houver)
  bairros_belem_2t.csv          votos e vencedor por bairro (soma dos locais de votação dentro de cada polígono)
  bairros_belem_2t.geojson      polígonos dos bairros com o resultado (abre no QGIS)
  locais_bairro_conferencia.csv locais cujo bairro pelo polígono difere do bairro do cadastro do TSE

  --bairros  Limites dos bairros de Belém em GeoJSON (padrão: data/geo/bairros_belem.geojson). Fonte usada:
             https://github.com/samuel-c-santos/geovisor-belem (data/bairros.geojson; o README do repositório declara
             licença MIT; base IBGE/CODEM). Créditos completos em data/geo/LEIAME.md.
             Sem esse arquivo, o mapa sai igual, só que sem a divisão por bairros.

Uso:
  python mapa_secoes_belem.py            # gera o mapa; abra o HTML (fundos da Esri funcionam do disco)
  python mapa_secoes_belem.py --servir   # gera e abre em http://localhost:8000 (libera também OSM e CARTO)
  python mapa_secoes_belem.py --bu caminho/bweb_2t_PA_BEL_29102026.csv --locais caminho/eleitorado_local_votacao_2026_PA_belem.csv

Decisões de método (importantes para interpretar o mapa):
  * A chave que liga votos e local é MUNICÍPIO + ZONA + SEÇÃO. O número do local de votação NÃO serve de chave:
    ele é numerado por zona (o mesmo número é escolas diferentes em zonas diferentes) e, no boletim de urna, vem o
    local ORIGINAL da seção, não o local temporário em que a votação de fato ocorreu. A coordenada usada é a do
    local efetivamente utilizado no pleito (arquivo de locais de votação).
  * O TSE só fornece o PONTO do local de votação, não o polígono da seção. Por isso o mapa é de pontos.
    Na camada "Seções", as seções de uma mesma escola são espalhadas em volta do ponto só para ficarem visíveis;
    essa dispersão é visual e não representa a posição real das salas.
  * Seções agregadas não têm votos próprios (os votos entram na seção principal) e por isso não aparecem.
  * Coordenadas ausentes (-1) ou fora da região são trocadas pelo centro dos demais locais do mesmo bairro e
    desenhadas com contorno tracejado (posição aproximada). Sem bairro de referência, o local vai para sem_coordenada.csv.
  * Bairros: cada local de votação é associado ao polígono de bairro que contém o seu ponto (ou ao mais próximo, até
    300 m). O resultado do bairro é a soma dos locais de votação dentro dele, NÃO dos eleitores que moram ali:
    o eleitor vota na escola a que sua seção foi vinculada, que pode ficar em outro bairro. Locais sem polígono
    (ilhas ausentes da base, como Cotijuba e Combu) entram no mapa de pontos, mas não no resultado por bairro.
  * Percentuais são sobre votos válidos (Lula + Flávio no 2º turno), a convenção usual da divulgação de resultados.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent

COD_TSE_BELEM = 4278
NUM_LULA, NUM_FLAVIO, NUM_BRANCO, NUM_NULO, NUM_ANULADO = 13, 22, 95, 96, 97
COR_LULA, COR_FLAVIO, COR_EMPATE = "#d7191c", "#1f5bd8", "#7f7f7f"
TOLERANCIA_GRAUS = 0.5  # coordenada a mais que isso da mediana do município é tratada como errada
PADRAO_BU = RAIZ / "data" / "raw" / "secoes" / "bweb_2t_PA_BEL_29102026.csv"
PADRAO_LOCAIS = RAIZ / "data" / "raw" / "secoes" / "eleitorado_local_votacao_2026_PA_belem.csv"
PADRAO_BAIRROS = RAIZ / "data" / "geo" / "bairros_belem.geojson"
DIST_MAX_BAIRRO_M = 300  # local fora de qualquer polígono é ligado ao bairro mais próximo se estiver a até esta distância


# ----------------------------------------------------------------------------- leitura

def num(serie: pd.Series) -> pd.Series:
    """Converte para número; '#NULO#', '#NE' e vazios viram NaN."""
    return pd.to_numeric(serie, errors="coerce")


def ler_csv(caminho: Path) -> pd.DataFrame:
    """Lê CSV do TSE: separador ';' (arquivo original) ou ',' (exportado), UTF-8 ou Latin-1."""
    if not caminho.exists():
        sys.exit(f"Arquivo não encontrado: {caminho}")
    with open(caminho, "rb") as f:
        cab = f.readline().decode("latin1")
    sep = ";" if cab.count(";") > cab.count(",") else ","
    try:
        return pd.read_csv(caminho, sep=sep, encoding="utf-8", dtype=str, keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(caminho, sep=sep, encoding="latin1", dtype=str, keep_default_na=False)


def exigir(df: pd.DataFrame, colunas: set[str], nome: str) -> None:
    falta = colunas - set(df.columns)
    if falta:
        sys.exit(f"{nome}: faltam as colunas {sorted(falta)}. Confira se é o arquivo certo.")


# ----------------------------------------------------------------------------- votos por seção

def votos_por_secao(caminho: Path, mun: int) -> pd.DataFrame:
    bu = ler_csv(caminho)
    exigir(bu, {"NR_TURNO", "CD_MUNICIPIO", "CD_CARGO_PERGUNTA", "CD_TIPO_URNA", "NR_ZONA", "NR_SECAO",
                "NR_LOCAL_VOTACAO", "NR_VOTAVEL", "QT_VOTOS", "QT_APTOS", "QT_COMPARECIMENTO"}, "Boletim de urna")
    b = pd.DataFrame({
        "turno": num(bu.NR_TURNO), "mun": num(bu.CD_MUNICIPIO), "cargo": num(bu.CD_CARGO_PERGUNTA),
        "urna": num(bu.CD_TIPO_URNA), "zona": num(bu.NR_ZONA), "secao": num(bu.NR_SECAO),
        "local_bu": num(bu.NR_LOCAL_VOTACAO), "votavel": num(bu.NR_VOTAVEL), "votos": num(bu.QT_VOTOS),
        "aptos": num(bu.QT_APTOS), "comp": num(bu.QT_COMPARECIMENTO),
    })
    b = b[(b.turno == 2) & (b.mun == mun) & (b.cargo == 1)]  # 2º turno, município, Presidente
    if b.empty:
        sys.exit("Nenhuma linha de Presidente / 2º turno / município "
                 f"{mun} no boletim de urna. Confira o arquivo e --municipio.")
    nao_apuradas = int((b.urna != 1).sum())
    if nao_apuradas:
        print(f"  Aviso: {nao_apuradas} linhas de urnas não apuradas/anuladas foram ignoradas.")
    b = b[b.urna == 1].copy()
    b["votos"] = b.votos.fillna(0).clip(lower=0)
    dup = int(b.duplicated(["zona", "secao", "votavel"]).sum())
    if dup:
        sys.exit(f"Há {dup} linhas repetidas (mesma zona, seção e votável) no boletim de urna. "
                 "Somá-las contaria votos em dobro; verifique se há mais de um arquivo misturado.")

    v = (b.pivot_table(index=["zona", "secao"], columns="votavel", values="votos", aggfunc="sum", fill_value=0)
         .reindex(columns=[NUM_LULA, NUM_FLAVIO, NUM_BRANCO, NUM_NULO, NUM_ANULADO], fill_value=0)
         .rename(columns={NUM_LULA: "lula", NUM_FLAVIO: "flavio", NUM_BRANCO: "brancos",
                          NUM_NULO: "nulos", NUM_ANULADO: "anulados"}))
    extra = b.groupby(["zona", "secao"]).agg(aptos=("aptos", "first"), comparecimento=("comp", "first"),
                                             nr_local_bu=("local_bu", "first"))
    s = v.join(extra).reset_index()
    s["validos"] = s.lula + s.flavio
    s["pct_lula"] = (100 * s.lula / s.validos.where(s.validos > 0)).round(2)
    s["pct_flavio"] = (100 * s.flavio / s.validos.where(s.validos > 0)).round(2)
    s["vencedor"] = "Empate"
    s.loc[s.lula > s.flavio, "vencedor"] = "Lula"
    s.loc[s.flavio > s.lula, "vencedor"] = "Flávio"
    s["margem_pp"] = (s.pct_lula - s.pct_flavio).abs().round(2)
    return s


# ----------------------------------------------------------------------------- locais de votação

def locais_de_votacao(caminho: Path, mun: int) -> pd.DataFrame:
    lv = ler_csv(caminho)
    exigir(lv, {"CD_MUNICIPIO", "NR_ZONA", "NR_SECAO", "NR_LOCAL_VOTACAO", "NM_LOCAL_VOTACAO", "NM_BAIRRO",
                "NR_LATITUDE", "NR_LONGITUDE"}, "Locais de votação")
    mask = num(lv.CD_MUNICIPIO) == mun
    if "NR_TURNO" in lv.columns:  # o arquivo traz 1º e 2º turnos: usar só o 2º
        mask &= num(lv.NR_TURNO) == 2
    lv = lv[mask]
    if lv.empty:
        sys.exit(f"Nenhum local de votação do município {mun} / 2º turno em {caminho.name}.")

    def txt(col):
        return lv[col] if col in lv.columns else pd.Series("", index=lv.index)

    l = pd.DataFrame({
        "zona": num(lv.NR_ZONA), "secao": num(lv.NR_SECAO), "nr_local": num(lv.NR_LOCAL_VOTACAO),
        "nm_local": lv.NM_LOCAL_VOTACAO.str.strip(), "bairro": lv.NM_BAIRRO.str.strip(),
        "endereco": txt("DS_ENDERECO").str.strip(), "tipo_local": txt("DS_TIPO_LOCAL"),
        "tipo_secao": txt("DS_TIPO_SECAO_AGREGADA"),
        "nr_local_original": num(txt("NR_LOCAL_VOTACAO_ORIGINAL")),
        "nm_local_original": txt("NM_LOCAL_VOTACAO_ORIGINAL").str.strip(),
        "lat": num(lv.NR_LATITUDE.str.replace(",", ".", regex=False)),
        "lon": num(lv.NR_LONGITUDE.str.replace(",", ".", regex=False)),
    })
    if l.duplicated(["zona", "secao"]).any():
        sys.exit("Há seções repetidas no arquivo de locais de votação (mesma zona e seção). "
                 "Confira se o filtro de turno/município está correto.")
    return l


def corrigir_coordenadas(l: pd.DataFrame) -> pd.DataFrame:
    """Marca coordenadas inválidas (-1, vazias, fora da região) e as substitui pelo centro do bairro."""
    l = l.copy()
    med_lat, med_lon = l.lat.median(), l.lon.median()
    ok = ((l.lat - med_lat).abs() <= TOLERANCIA_GRAUS) & ((l.lon - med_lon).abs() <= TOLERANCIA_GRAUS)
    l["coord_aprox"] = ~ok
    l.loc[~ok, ["lat", "lon"]] = float("nan")
    centro = l.groupby("bairro")[["lat", "lon"]].transform("mean")  # mean ignora NaN
    sem = l.lat.isna()
    l.loc[sem, "lat"] = centro.loc[sem, "lat"]
    l.loc[sem, "lon"] = centro.loc[sem, "lon"]
    return l


# ----------------------------------------------------------------------------- montagem

def dispersar(df: pd.DataFrame) -> pd.DataFrame:
    """Espalha as seções de uma mesma escola em espiral (só para visualização, ~7 m * raiz(i))."""
    df = df.sort_values(["zona", "nr_local", "secao"]).copy()
    ordem = df.groupby(["zona", "nr_local"]).cumcount()
    ang = ordem * 2.399963  # ângulo áureo
    raio = 7 * ordem.pow(0.5)  # metros
    df["lat_j"] = df.lat + (raio * ang.apply(math.sin)) / 111_320
    df["lon_j"] = df.lon + (raio * ang.apply(math.cos)) / (111_320 * df.lat.apply(lambda x: math.cos(math.radians(x))))
    return df


def agregar_locais(s: pd.DataFrame) -> pd.DataFrame:
    chave = ["zona", "nr_local"]
    g = s.groupby(chave, as_index=False).agg(
        nm_local=("nm_local", "first"), bairro=("bairro", "first"), endereco=("endereco", "first"),
        tipo_local=("tipo_local", "first"), lat=("lat", "first"), lon=("lon", "first"),
        coord_aprox=("coord_aprox", "max"), secoes=("secao", "count"),
        lula=("lula", "sum"), flavio=("flavio", "sum"), brancos=("brancos", "sum"), nulos=("nulos", "sum"),
        aptos=("aptos", "sum"), comparecimento=("comparecimento", "sum"),
        nm_local_original=("nm_local_original", "first"), nr_local_original=("nr_local_original", "first"),
    )
    # só mostra "substituiu" quando o local original é outro
    sub = g.nr_local_original.notna() & (g.nr_local_original != g.nr_local)
    g["substituiu"] = g.nm_local_original.where(sub, "")
    g = g.drop(columns=["nm_local_original", "nr_local_original"])
    g["validos"] = g.lula + g.flavio
    g["pct_lula"] = (100 * g.lula / g.validos.where(g.validos > 0)).round(2)
    g["pct_flavio"] = (100 * g.flavio / g.validos.where(g.validos > 0)).round(2)
    g["vencedor"] = "Empate"
    g.loc[g.lula > g.flavio, "vencedor"] = "Lula"
    g.loc[g.flavio > g.lula, "vencedor"] = "Flávio"
    g["margem_pp"] = (g.pct_lula - g.pct_flavio).abs().round(2)
    return g


def limpo(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicts compatível com JSON (NaN -> None, tipos numpy -> Python)."""
    return json.loads(df.to_json(orient="records", force_ascii=False))


def geojson(df: pd.DataFrame, lat: str = "lat", lon: str = "lon") -> dict:
    feats = []
    for r in limpo(df):
        if r.get(lat) is None or r.get(lon) is None:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r[lon], r[lat]]},
                      "properties": {k: v for k, v in r.items() if k not in ("lat_j", "lon_j")}})
    return {"type": "FeatureCollection", "features": feats}


def conferir_totais(s: pd.DataFrame, lula: int | None, flavio: int | None) -> None:
    """Compara a soma das seções com os totais oficiais do município, se informados (--total-lula/--total-flavio)."""
    if lula is None or flavio is None:
        return
    print(f"  Conferência com o total oficial: Lula {lula:,} / Flávio {flavio:,} "
          f"| diferença nas seções: Lula {int(s.lula.sum() - lula):+,} / Flávio {int(s.flavio.sum() - flavio):+,}")


# ----------------------------------------------------------------------------- bairros (polígonos)

def chaves_bairro(nome) -> set[str]:
    """Nomes normalizados (sem acento/hífen/caixa). 'Vila (Mosqueiro)' -> {'VILA', 'MOSQUEIRO'}.
    Permite comparar o rótulo do TSE ('TERRA FIRME') com o do polígono ('Montese (Terra Firme)')."""
    t = unicodedata.normalize("NFD", str(nome).upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").replace("-", " ")
    return {re.sub(r"\s+", " ", p).strip() for p in re.split(r"[()]", t) if p.strip()}


def chave_unica(nome) -> str:
    t = unicodedata.normalize("NFD", str(nome).upper())
    return re.sub(r"\s+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn").replace("-", " ")).strip()


def carregar_bairros(caminho: Path) -> dict:
    """Lê o GeoJSON e junta as partes de um mesmo bairro (vários bairros vêm em mais de um polígono)."""
    gj = json.loads(caminho.read_text(encoding="utf-8"))
    crs = json.dumps(gj.get("crs") or "")
    if gj.get("crs") and not any(k in crs for k in ("4674", "4326", "CRS84")):
        sys.exit(f"{caminho.name}: sistema de coordenadas não suportado ({crs}). Use EPSG:4674, 4326 ou CRS84 (graus).")
    bairros: dict[str, dict] = {}
    for f in gj["features"]:
        props = f.get("properties") or {}
        nome = next((str(props[k]).strip() for k in ("BAI_NM", "NM_BAIRRO", "NOME", "nome", "Name") if props.get(k)), None)
        if not nome:
            sys.exit(f"{caminho.name}: não achei o nome do bairro nas propriedades {sorted(props)}.")
        geom = f["geometry"]
        pols = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        b = bairros.setdefault(chave_unica(nome), {"nome": nome, "partes": []})
        for pol in pols:
            xs, ys = [c[0] for c in pol[0]], [c[1] for c in pol[0]]
            b["partes"].append(((min(xs), min(ys), max(xs), max(ys)), pol))
    return bairros


def _dentro_anel(x: float, y: float, anel: list) -> bool:
    dentro, j = False, len(anel) - 1
    for i in range(len(anel)):
        xi, yi, xj, yj = anel[i][0], anel[i][1], anel[j][0], anel[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _dist_anel_m(x: float, y: float, anel: list) -> float:
    """Distância (m) do ponto ao contorno do anel, em projeção equiretangular local."""
    kx, ky = 111_320 * math.cos(math.radians(y)), 111_320
    melhor = float("inf")
    for (x1, y1), (x2, y2) in zip((c[:2] for c in anel), (c[:2] for c in anel[1:])):
        ax, ay, bx, by = (x1 - x) * kx, (y1 - y) * ky, (x2 - x) * kx, (y2 - y) * ky
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        u = 0.0 if den == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / den))
        melhor = min(melhor, math.hypot(ax + u * dx, ay + u * dy))
    return melhor


def bairro_do_ponto(lon: float, lat: float, bairros: dict) -> tuple[str | None, float | None]:
    """(nome do bairro, distância em m). Dentro do polígono -> distância 0; senão o mais próximo até o limite."""
    if lon is None or lat is None or math.isnan(lon) or math.isnan(lat):
        return None, None
    for b in bairros.values():
        for (x0, y0, x1, y1), pol in b["partes"]:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _dentro_anel(lon, lat, pol[0]) \
                    and not any(_dentro_anel(lon, lat, h) for h in pol[1:]):
                return b["nome"], 0.0
    mx, my = DIST_MAX_BAIRRO_M / (111_320 * math.cos(math.radians(lat))), DIST_MAX_BAIRRO_M / 111_320
    melhor, dmin = None, float("inf")
    for b in bairros.values():
        for (x0, y0, x1, y1), pol in b["partes"]:
            if x0 - mx <= lon <= x1 + mx and y0 - my <= lat <= y1 + my:
                d = _dist_anel_m(lon, lat, pol[0])
                if d < dmin:
                    melhor, dmin = b["nome"], d
    return (melhor, round(dmin, 1)) if dmin <= DIST_MAX_BAIRRO_M else (None, None)


def associar_bairros(g: pd.DataFrame, bairros: dict) -> pd.DataFrame:
    g = g.copy()
    achados = [bairro_do_ponto(r.lon, r.lat, bairros) for r in g.itertuples()]
    g["bairro_pol"] = [a[0] for a in achados]
    g["bairro_pol_dist_m"] = [a[1] for a in achados]
    return g


def resultado_por_bairro(g: pd.DataFrame, bairros: dict) -> tuple[pd.DataFrame, dict]:
    """Soma os locais de votação de cada bairro; devolve a tabela e o GeoJSON dos polígonos com o resultado."""
    soma = (g.dropna(subset=["bairro_pol"]).groupby("bairro_pol")
            .agg(locais=("nr_local", "count"), secoes=("secoes", "sum"), lula=("lula", "sum"), flavio=("flavio", "sum"),
                 brancos=("brancos", "sum"), nulos=("nulos", "sum"), aptos=("aptos", "sum"),
                 comparecimento=("comparecimento", "sum")))
    linhas, feats = [], []
    for b in bairros.values():
        r = soma.loc[b["nome"]] if b["nome"] in soma.index else None
        if r is None:
            props = {"nome": b["nome"], "locais": 0, "secoes": 0, "lula": 0, "flavio": 0, "validos": 0,
                     "pct_lula": None, "pct_flavio": None, "vencedor": None, "margem_pp": None, "sem_dado": True}
        else:
            validos = int(r.lula + r.flavio)
            pl, pf = round(100 * r.lula / validos, 2), round(100 * r.flavio / validos, 2)
            props = {"nome": b["nome"], "locais": int(r.locais), "secoes": int(r.secoes), "lula": int(r.lula),
                     "flavio": int(r.flavio), "validos": validos, "pct_lula": pl, "pct_flavio": pf,
                     "vencedor": "Lula" if r.lula > r.flavio else "Flávio" if r.flavio > r.lula else "Empate",
                     "margem_pp": round(abs(pl - pf), 2), "sem_dado": False}
            props["brancos"], props["nulos"] = int(r.brancos), int(r.nulos)
            props["aptos"], props["comparecimento"] = int(r.aptos), int(r.comparecimento)
        linhas.append(props)
        coords = [[[[round(x, 5), round(y, 5)] for x, y in (c[:2] for c in anel)] for anel in pol]
                  for _, pol in b["partes"]]
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": coords}})
    tabela = pd.DataFrame(linhas).sort_values("validos", ascending=False)
    return tabela, {"type": "FeatureCollection", "features": feats}


def divergencias_bairro(g: pd.DataFrame) -> pd.DataFrame:
    """Locais em que o bairro do polígono não bate com o bairro do cadastro do TSE (conferência)."""
    t = g.dropna(subset=["bairro_pol"])
    dif = t[[not (chaves_bairro(a) & chaves_bairro(b)) for a, b in zip(t.bairro, t.bairro_pol)]]
    return dif[["zona", "nr_local", "nm_local", "bairro", "bairro_pol", "bairro_pol_dist_m", "validos"]]


# ----------------------------------------------------------------------------- HTML

TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  html, body { height: 100%; margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #mapa { height: 100%; }
  .painel { background: rgba(255,255,255,.96); padding: 10px 12px; border-radius: 8px;
            box-shadow: 0 1px 6px rgba(0,0,0,.35); font-size: 13px; line-height: 1.45; max-width: 290px; }
  .painel h1 { font-size: 14px; margin: 0 0 6px; }
  .painel .nota { color: #555; font-size: 11.5px; margin-top: 6px; }
  .sw { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
  .tip b { font-size: 13px; }
</style>
</head>
<body>
<div id="mapa"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = __DADOS__;
const COR = { "Lula": D.cores.lula, "Flávio": D.cores.flavio, "Empate": D.cores.empate };
const fmt = n => Number(n).toLocaleString("pt-BR");
const pct = x => (x == null ? "–" : Number(x).toFixed(1).replace(".", ",") + "%");
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const map = L.map("mapa");
map.createPane("pBairros").style.zIndex = 330;      // abaixo dos pontos (overlayPane = 400)
map.createPane("pBairrosRes").style.zIndex = 340;
const rBairros = L.svg({ pane: "pBairros" }), rRes = L.svg({ pane: "pBairrosRes" });
// Fundos da Esri funcionam abrindo o arquivo direto do disco (file://), sem chave.
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
const base = {
  "Mapa claro (Esri)": L.tileLayer(ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      { maxNativeZoom: 16, maxZoom: 19, attribution: "Tiles &copy; Esri" }),
  "Ruas (Esri)": L.tileLayer(ESRI + "World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      { maxNativeZoom: 19, maxZoom: 19, attribution: "Tiles &copy; Esri" }),
  "Satélite (Esri)": L.tileLayer(ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxNativeZoom: 19, maxZoom: 19, attribution: "Tiles &copy; Esri" })
};
// OSM e CARTO exigem que a página envie o cabeçalho Referer; arquivos abertos do disco (file://) não enviam,
// e o OSM responde 403 ("tile usage policy"). Por isso só entram quando a página é servida por http(s).
if (location.protocol.startsWith("http")) {
  base["OpenStreetMap"] = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" });
  base["CARTO claro"] = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 19, subdomains: "abcd", attribution: "&copy; OpenStreetMap, &copy; CARTO" });
}
base["Mapa claro (Esri)"].addTo(map);

function dica(p, ehLocal) {
  const t = ehLocal
    ? `<b>${esc(p.nm_local)}</b><br>${esc(p.bairro_pol || p.bairro)} · zona ${p.zona} · ${p.secoes} seç${p.secoes === 1 ? "ão" : "ões"} apurada${p.secoes === 1 ? "" : "s"}`
    : `<b>Seção ${p.secao} · zona ${p.zona}</b><br>${esc(p.nm_local)} (${esc(p.bairro_pol || p.bairro)})`;
  const sub = p.substituiu ? `<br><i>Local temporário (original: ${esc(p.substituiu)})</i>` : "";
  const ap = p.coord_aprox ? `<br><i>Posição aproximada (centro do bairro)</i>` : "";
  return `<div class="tip">${t}${sub}${ap}<br>
    <span class="sw" style="background:${COR["Lula"]}"></span>Lula: ${fmt(p.lula)} (${pct(p.pct_lula)})<br>
    <span class="sw" style="background:${COR["Flávio"]}"></span>Flávio: ${fmt(p.flavio)} (${pct(p.pct_flavio)})<br>
    <b>Venceu: ${p.vencedor}</b> por ${pct(p.margem_pp).replace("%", " p.p.")}</div>`;
}

const camLocais = L.layerGroup();
D.locais.forEach(p => {
  if (p.lat == null) return;
  const r = Math.min(16, 3 + Math.sqrt(p.validos) * 0.11);
  L.circleMarker([p.lat, p.lon], {
    radius: r, color: p.coord_aprox ? "#000" : "#fff", weight: p.coord_aprox ? 1.6 : 0.8,
    dashArray: p.coord_aprox ? "3 3" : null, fillColor: COR[p.vencedor], fillOpacity: 0.85
  }).bindTooltip(dica(p, true), { sticky: true }).addTo(camLocais);
});

const camSecoes = L.layerGroup();
D.secoes.forEach(p => {
  if (p.lat_j == null) return;
  L.circleMarker([p.lat_j, p.lon_j], {
    radius: 3.5, color: p.coord_aprox ? "#000" : "#fff", weight: 0.5,
    fillColor: COR[p.vencedor], fillOpacity: 0.9
  }).bindTooltip(dica(p, false), { sticky: true }).addTo(camSecoes);
});

function dicaBairro(p) {
  if (p.sem_dado) return `<div class="tip"><b>${esc(p.nome)}</b><br><i>Sem local de votação dentro do bairro</i></div>`;
  return `<div class="tip"><b>${esc(p.nome)}</b><br>${fmt(p.locais)} locais · ${fmt(p.secoes)} seções apuradas<br>
    <span class="sw" style="background:${COR["Lula"]}"></span>Lula: ${fmt(p.lula)} (${pct(p.pct_lula)})<br>
    <span class="sw" style="background:${COR["Flávio"]}"></span>Flávio: ${fmt(p.flavio)} (${pct(p.pct_flavio)})<br>
    <b>Venceu: ${p.vencedor}</b> por ${pct(p.margem_pp).replace("%", " p.p.")}<br>
    <i>Soma dos locais de votação do bairro</i></div>`;
}
const camLimites = L.layerGroup(), camBairros = L.layerGroup();
if (D.bairros) {
  L.geoJSON(D.bairros, {
    pane: "pBairros", renderer: rBairros,
    style: () => ({ color: "#444", weight: 1.1, fillColor: "#000", fillOpacity: 0 }),
    onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.nome), { sticky: true })
  }).addTo(camLimites);
  L.geoJSON(D.bairros, {
    pane: "pBairrosRes", renderer: rRes,
    style: f => f.properties.sem_dado
      ? { color: "#777", weight: 1, fillColor: "#999", fillOpacity: 0.08 }
      : { color: "#444", weight: 1, fillColor: COR[f.properties.vencedor], fillOpacity: 0.5 },
    onEachFeature: (f, l) => l.bindTooltip(dicaBairro(f.properties), { sticky: true })
  }).addTo(camBairros);
  camLimites.addTo(map);
}

camLocais.addTo(map);
const sobreposicoes = { "Locais de votação (escolas)": camLocais, "Seções eleitorais": camSecoes };
if (D.bairros) {
  sobreposicoes["Limites dos bairros"] = camLimites;
  sobreposicoes["Bairros: vencedor (soma dos locais)"] = camBairros;
}
L.control.layers(base, sobreposicoes, { collapsed: false, position: "topleft" }).addTo(map);

const info = L.control({ position: "topright" });
info.onAdd = () => {
  const d = L.DomUtil.create("div", "painel");
  const R = D.resumo;
  const linha = (nome, chave) => `<div><span class="sw" style="background:${COR[nome]}"></span><b>${nome}</b>:
      ${fmt(R[chave].votos)} votos (${pct(R[chave].pct)})<br>
      <span style="color:#555">&nbsp;&nbsp;&nbsp;&nbsp;venceu em ${fmt(R[chave].locais)} locais e ${fmt(R[chave].secoes)} seções</span></div>`;
  const emp = R.empate.secoes + R.empate.locais > 0
    ? `<div><span class="sw" style="background:${COR["Empate"]}"></span><b>Empate</b>: ${fmt(R.empate.secoes)} seções e ${fmt(R.empate.locais)} locais</div>` : "";
  d.innerHTML = `<h1>${esc(D.titulo)}</h1>${linha("Lula", "lula")}${linha("Flávio", "flavio")}${emp}
    <div class="nota">${fmt(R.n_secoes)} seções apuradas em ${fmt(R.n_locais)} locais de votação.
    Tamanho do círculo proporcional aos votos válidos do local.
    ${R.n_aprox ? `Contorno tracejado: ${R.n_aprox} locais com posição aproximada (coordenada ausente no cadastro do TSE).` : ""}
    Na camada de seções, a posição dentro da escola é apenas ilustrativa.
    ${R.bairros ? `<br><b>Bairros:</b> Lula venceu em ${R.bairros.lula}, Flávio em ${R.bairros.flavio}${R.bairros.empate ? `, empate em ${R.bairros.empate}` : ""}
      (de ${R.bairros.com_dado} com local de votação; ${R.bairros.sem_dado} sem). Resultado do bairro = soma dos locais de votação dentro dele, não o domicílio dos eleitores.
      ${R.bairros.locais_fora ? `${R.bairros.locais_fora} locais (ilhas) ficam fora dos polígonos.` : ""}` : ""}<br>Fonte: TSE (boletim de urna e eleitorado por local de votação)${D.bairros ? "; limites dos bairros: IBGE/CODEM via geovisor-belem (S. Santos, MIT)" : ""}.</div>`;
  return d;
};
info.addTo(map);

map.fitBounds(D.bounds, { padding: [20, 20] });
</script>
</body>
</html>
"""


def gerar_html(destino: Path, titulo: str, locais: pd.DataFrame, secoes: pd.DataFrame, resumo: dict, bounds: list,
               bairros_geojson: dict | None = None) -> None:
    cols_l = ["zona", "nr_local", "nm_local", "bairro", "secoes", "lula", "flavio", "validos", "pct_lula",
              "pct_flavio", "vencedor", "margem_pp", "lat", "lon", "coord_aprox", "substituiu", "bairro_pol"]
    cols_s = ["zona", "secao", "nm_local", "bairro", "lula", "flavio", "validos", "pct_lula", "pct_flavio",
              "vencedor", "margem_pp", "lat_j", "lon_j", "coord_aprox", "substituiu", "bairro_pol"]
    dados = {
        "titulo": titulo, "resumo": resumo, "bounds": bounds,
        "cores": {"lula": COR_LULA, "flavio": COR_FLAVIO, "empate": COR_EMPATE},
        "locais": limpo(locais[cols_l]), "secoes": limpo(secoes[cols_s]), "bairros": bairros_geojson,
    }
    js = json.dumps(dados, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    destino.write_text(TEMPLATE.replace("__TITULO__", titulo).replace("__DADOS__", js), encoding="utf-8")


# ----------------------------------------------------------------------------- principal

def main(bu: Path, locais: Path, saida: Path, mun: int, arq_bairros: Path | None = None,
         total_lula: int | None = None, total_flavio: int | None = None) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    print("Lendo boletim de urna e locais de votação...")
    votos = votos_por_secao(bu, mun)
    loc = corrigir_coordenadas(locais_de_votacao(locais, mun))
    nome_mun = "Belém" if mun == COD_TSE_BELEM else f"município {mun}"

    s = votos.merge(loc, on=["zona", "secao"], how="left", validate="one_to_one")
    sem_local = int(s.nm_local.isna().sum())
    if sem_local:
        print(f"  Aviso: {sem_local} seções com voto não foram encontradas no arquivo de locais e ficam de fora do mapa.")
    s = s[s.nm_local.notna()].copy()
    s["nr_local"] = s.nr_local.astype(int)
    trocou = s.nr_local_original.notna() & (s.nr_local_original != s.nr_local)
    s["substituiu"] = s.nm_local_original.where(trocou, "")  # nome do local original, se houve substituição
    s = dispersar(s)
    g = agregar_locais(s)

    # --- Bairros: associa cada local ao polígono que o contém (e propaga para as seções)
    bairros, tab_bairros, gj_bairros = None, None, None
    if arq_bairros and arq_bairros.exists():
        bairros = carregar_bairros(arq_bairros)
        g = associar_bairros(g, bairros)
        s = s.merge(g[["zona", "nr_local", "bairro_pol", "bairro_pol_dist_m"]], on=["zona", "nr_local"], how="left")
        tab_bairros, gj_bairros = resultado_por_bairro(g, bairros)
    else:
        print(f"  Aviso: arquivo de bairros não encontrado ({arq_bairros}); o mapa sai sem a divisão por bairros.")
        g["bairro_pol"] = None
        s["bairro_pol"] = None

    n_loc_bu = int((s.nr_local_bu != s.nr_local).sum())
    print(f"  {len(s)} seções apuradas | {len(g)} locais de votação")
    print(f"  Seções cujo local no boletim difere do local usado no pleito (local temporário): {n_loc_bu}")
    print(f"  Totais nas seções: Lula {int(s.lula.sum()):,} | Flávio {int(s.flavio.sum()):,} | "
          f"brancos {int(s.brancos.sum()):,} | nulos {int(s.nulos.sum()):,}")
    conferir_totais(s, total_lula, total_flavio)

    sem_coord = g[g.lat.isna()]
    if len(sem_coord):
        sem_coord.to_csv(saida / "sem_coordenada.csv", index=False)
        print(f"  Aviso: {len(sem_coord)} locais sem nenhuma coordenada utilizável (veja sem_coordenada.csv); "
              f"{int(sem_coord.validos.sum()):,} votos válidos ficam fora do mapa.")
    print(f"  Locais com posição aproximada (centro do bairro): {int(g.coord_aprox.sum())}")

    if bairros:
        fora = g[g.bairro_pol.isna()]
        perto = g[g.bairro_pol_dist_m.fillna(0) > 0]
        print(f"  Bairros: {len(bairros)} polígonos | locais dentro de um polígono: {int((g.bairro_pol_dist_m == 0).sum())} "
              f"| ligados ao mais próximo (<= {DIST_MAX_BAIRRO_M} m): {len(perto)} | fora de qualquer polígono: {len(fora)} "
              f"({int(fora.validos.sum()):,} votos válidos)")
        for r in fora.itertuples():
            print(f"      fora: {r.nm_local} ({r.bairro}), zona {r.zona}, {int(r.validos):,} votos válidos")
        div = divergencias_bairro(g)
        div.to_csv(saida / "locais_bairro_conferencia.csv", index=False)
        print(f"  Bairro do polígono diferente do bairro do cadastro do TSE: {len(div)} de {int(g.bairro_pol.notna().sum())} "
              f"locais (veja locais_bairro_conferencia.csv; comum perto de divisas)")
        tab_bairros.to_csv(saida / "bairros_belem_2t.csv", index=False)
        (saida / "bairros_belem_2t.geojson").write_text(json.dumps(gj_bairros, ensure_ascii=False), encoding="utf-8")
        com = tab_bairros[~tab_bairros.sem_dado]
        print(f"  Bairros vencidos: Lula {int((com.vencedor == 'Lula').sum())} | Flávio {int((com.vencedor == 'Flávio').sum())} "
              f"| empate {int((com.vencedor == 'Empate').sum())} | sem local de votação: {int(tab_bairros.sem_dado.sum())}")

    s.drop(columns=["lat_j", "lon_j"]).to_csv(saida / "secoes_belem_2t.csv", index=False)
    g.to_csv(saida / "locais_belem_2t.csv", index=False)
    (saida / "secoes_belem_2t.geojson").write_text(json.dumps(geojson(s), ensure_ascii=False), encoding="utf-8")
    (saida / "locais_belem_2t.geojson").write_text(json.dumps(geojson(g), ensure_ascii=False), encoding="utf-8")

    tv = int(g.validos.sum())
    resumo = {
        "lula": {"votos": int(g.lula.sum()), "pct": round(100 * g.lula.sum() / tv, 2),
                 "locais": int((g.vencedor == "Lula").sum()), "secoes": int((s.vencedor == "Lula").sum())},
        "flavio": {"votos": int(g.flavio.sum()), "pct": round(100 * g.flavio.sum() / tv, 2),
                   "locais": int((g.vencedor == "Flávio").sum()), "secoes": int((s.vencedor == "Flávio").sum())},
        "empate": {"locais": int((g.vencedor == "Empate").sum()), "secoes": int((s.vencedor == "Empate").sum())},
        "n_secoes": int(len(s)), "n_locais": int(len(g)), "n_aprox": int(g.coord_aprox.sum()),
    }
    if tab_bairros is not None:
        com = tab_bairros[~tab_bairros.sem_dado]
        resumo["bairros"] = {"lula": int((com.vencedor == "Lula").sum()), "flavio": int((com.vencedor == "Flávio").sum()),
                             "empate": int((com.vencedor == "Empate").sum()), "com_dado": int(len(com)),
                             "sem_dado": int(tab_bairros.sem_dado.sum()), "locais_fora": int(g.bairro_pol.isna().sum())}
    print(f"  Locais vencidos: Lula {resumo['lula']['locais']} | Flávio {resumo['flavio']['locais']} "
          f"| Seções vencidas: Lula {resumo['lula']['secoes']} | Flávio {resumo['flavio']['secoes']} "
          f"| empates: {resumo['empate']['secoes']} seções")

    pts = g.dropna(subset=["lat"])
    bounds = [[float(pts.lat.quantile(0.02)), float(pts.lon.quantile(0.02))],
              [float(pts.lat.quantile(0.98)), float(pts.lon.quantile(0.98))]]
    gerar_html(saida / "mapa_belem_2t.html", f"{nome_mun} · 2º turno 2026 · Presidente",
               g.dropna(subset=["lat"]), s.dropna(subset=["lat"]), resumo, bounds, gj_bairros)
    print(f"Pronto. Abra: {saida / 'mapa_belem_2t.html'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mapa por seção e local de votação do 2º turno de 2026 (Presidente).")
    ap.add_argument("--bu", type=Path, default=PADRAO_BU, help="CSV do boletim de urna do 2º turno")
    ap.add_argument("--locais", type=Path, default=PADRAO_LOCAIS, help="CSV do eleitorado por local de votação 2026")
    ap.add_argument("--saida", type=Path, default=RAIZ / "saida", help="pasta de saída")
    ap.add_argument("--municipio", type=int, default=COD_TSE_BELEM, help="código TSE do município (Belém = 4278)")
    ap.add_argument("--bairros", type=Path, default=PADRAO_BAIRROS, help="GeoJSON com os limites dos bairros (opcional)")
    ap.add_argument("--total-lula", type=int, help="total oficial de votos de Lula no município, para conferência")
    ap.add_argument("--total-flavio", type=int, help="total oficial de votos de Flávio no município, para conferência")
    ap.add_argument("--servir", action="store_true",
                    help="depois de gerar, abre o mapa em http://localhost:8000 (habilita OpenStreetMap e CARTO como fundo)")
    a = ap.parse_args()
    main(a.bu, a.locais, a.saida, a.municipio, a.bairros, a.total_lula, a.total_flavio)
    if a.servir:
        servidor = ThreadingHTTPServer(("127.0.0.1", 8000), partial(SimpleHTTPRequestHandler, directory=str(a.saida)))
        url = "http://localhost:8000/mapa_belem_2t.html"
        print(f"Servindo em {url} (Ctrl+C para encerrar)")
        webbrowser.open(url)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            print("\nEncerrado.")
