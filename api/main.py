"""
api/main.py — Geoportal Curado API v0.1

Proxy inteligente sobre las fuentes vivas del Geoportal IDE Chile.
Lee la configuración de joins desde catalog/layers/*.yaml y ejecuta
los joins con los transforms documentados, devolviendo datos limpios.

Uso:
  make serve          → uvicorn con reload en localhost:8000
  GET /               → estado de la API
  GET /catalog        → lista de capas curadas
  GET /joins          → lista de joins disponibles
  GET /joins/{src}/{tgt}          → ejecutar join completo
  GET /joins/{src}/{tgt}?limit=10 → primeros N registros (para pruebas)
"""

import os
import re
from pathlib import Path
from typing import Any

import requests
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

CATALOG_DIR  = Path(__file__).parent.parent / "catalog"
LAYERS_DIR   = CATALOG_DIR / "layers"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
DEFAULT_DATABASE_URL = "postgresql://geoportal:geoportal@localhost:5432/geoportal"

app = FastAPI(
    title="Geoportal Curado",
    description=(
        "Proxy inteligente con relaciones verificadas sobre el Geoportal IDE Chile. "
        "Los joins entre capas se ejecutan con los transforms documentados — "
        "el usuario recibe datos limpios sin necesidad de conocer los bugs de las fuentes."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── catálogo ──────────────────────────────────────────────────────────────────

def load_layers() -> dict[str, dict]:
    layers: dict[str, dict] = {}
    for f in sorted(LAYERS_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        layer_id = data.get("id", f.stem)
        layers[layer_id] = data
    return layers


def find_relation(layers: dict, src_id: str, tgt_id: str) -> dict | None:
    for rel in (layers.get(src_id, {}).get("relations") or []):
        if rel.get("target") == tgt_id:
            return rel
    return None


def parse_join_on(join_on: str) -> tuple[str, str]:
    parts = re.split(r"\s*=\s*", join_on.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"join_on inválido: {join_on!r}. Formato esperado: 'col_a = col_b'")
    return parts[0].strip(), parts[1].strip()


# ── fetch: WFS ────────────────────────────────────────────────────────────────

def fetch_wfs(source: dict, limit: int | None = None) -> list[dict]:
    base_url = f"https://geoportal.cl/geoserver/{source['workspace']}/wfs"
    typename = source["typename"]
    page_size = min(limit, 1000) if limit else 1000
    start_index = 0
    records: list[dict] = []

    while True:
        params = {
            "service":      "WFS",
            "version":      "1.1.0",
            "request":      "GetFeature",
            "typeName":     typename,
            "outputFormat": "application/json",
            "maxFeatures":  page_size,
            "startIndex":   start_index,
        }
        resp = requests.get(base_url, params=params, timeout=120)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            break
        records.extend(f.get("properties", {}) for f in features)
        if limit and len(records) >= limit:
            return records[:limit]
        if len(features) < page_size:
            break
        start_index += page_size

    return records


# ── fetch: ArcGIS REST ────────────────────────────────────────────────────────

def fetch_arcgis(source: dict, limit: int | None = None) -> list[dict]:
    base = f"{source['service_url']}/{source['layer_id']}/query"
    page_size = min(limit, 2000) if limit else 2000
    records: list[dict] = []
    offset = 0

    while True:
        params = {
            "where":             "1=1",
            "outFields":         "*",
            "returnGeometry":    "false",
            "f":                 "json",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
        }
        resp = requests.get(base, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        features = data.get("features", [])
        records.extend(f.get("attributes", {}) for f in features)
        if limit and len(records) >= limit:
            return records[:limit]
        if not data.get("exceededTransferLimit", False) or not features:
            break
        offset += page_size

    return records


# ── fetch: static → postgres ──────────────────────────────────────────────────

def fetch_postgres(layer_id: str, limit: int | None = None) -> list[dict]:
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 no instalado")

    db_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        raise RuntimeError(
            f"No se pudo conectar a postgres: {e}. "
            f"Ejecutar 'make db-up' y 'make load LAYER={layer_id}'."
        )

    try:
        with conn.cursor() as cur:
            # Columnas de tipo geometry no son JSON-serializables y pueden ser muy grandes
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = %s
                  AND udt_name    != 'geometry'
                ORDER BY ordinal_position
                """,
                (layer_id,),
            )
            col_names = [row[0] for row in cur.fetchall()]
            if not col_names:
                raise RuntimeError(f"Tabla '{layer_id}' sin columnas o no existe.")

            col_str      = ", ".join(f'"{c}"' for c in col_names)
            limit_clause = f"LIMIT {limit}" if limit else ""
            cur.execute(f'SELECT {col_str} FROM "{layer_id}" {limit_clause}')
            return [dict(zip(col_names, row)) for row in cur.fetchall()]
    except Exception as e:
        raise RuntimeError(f"Error al consultar tabla '{layer_id}': {e}")
    finally:
        conn.close()


# ── dispatcher ────────────────────────────────────────────────────────────────

def fetch_layer(layer: dict, limit: int | None = None) -> list[dict]:
    source   = layer.get("source") or {}
    src_type = source.get("type", "")
    layer_id = layer.get("id", "")

    if src_type == "wfs":
        return fetch_wfs(source, limit)
    elif src_type == "arcgis_rest":
        return fetch_arcgis(source, limit)
    elif src_type == "static":
        return fetch_postgres(layer_id, limit)
    else:
        raise ValueError(f"Tipo de fuente no soportado: {src_type!r}")


# ── transform ─────────────────────────────────────────────────────────────────

def apply_transform(value: Any, transform: str | None) -> str:
    s = "" if value is None else str(value)
    if not transform:
        return s
    if transform.startswith("zfill:"):
        return s.zfill(int(transform.split(":")[1]))
    raise ValueError(f"Transform desconocido: {transform!r}")


# ── join ──────────────────────────────────────────────────────────────────────

def do_join(
    src_records: list[dict],
    tgt_records: list[dict],
    src_col: str,
    tgt_col: str,
    src_transform: str | None,
    tgt_transform: str | None,
    tgt_prefix: str,
) -> tuple[list[dict], dict]:
    # Construir lookup tgt indexado por clave transformada
    tgt_lookup: dict[str, dict] = {}
    for r in tgt_records:
        key = apply_transform(r.get(tgt_col), tgt_transform)
        tgt_lookup[key] = r

    matched = unmatched = 0
    result: list[dict] = []

    for r in src_records:
        key      = apply_transform(r.get(src_col), src_transform)
        tgt_match = tgt_lookup.get(key)
        if tgt_match:
            matched += 1
            tgt_fields = {f"{tgt_prefix}{k}": v for k, v in tgt_match.items()}
            result.append({**r, **tgt_fields})
        else:
            unmatched += 1
            result.append({**r, "_join_status": "no_match"})

    return result, {
        "total_src":  len(src_records),
        "total_tgt":  len(tgt_records),
        "matched":    matched,
        "unmatched":  unmatched,
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", summary="Interfaz web")
def root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health", summary="Estado de la API")
def health():
    return {
        "status":  "ok",
        "version": "0.1.0",
        "ui":      "/",
        "docs":    "/docs",
        "catalog": "/catalog",
        "joins":   "/joins",
    }


@app.get("/catalog", summary="Lista de capas curadas")
def get_catalog():
    layers = load_layers()
    result = []
    for layer_id, l in layers.items():
        rels = [
            {
                "target":        r.get("target"),
                "join_on":       r.get("join_on"),
                "join_type":     r.get("join_type"),
                "has_transform": bool((r.get("join_transform") or {}).get("src")
                                      or (r.get("join_transform") or {}).get("tgt")),
                "endpoint":      f"/joins/{layer_id}/{r.get('target')}",
            }
            for r in (l.get("relations") or [])
            if r.get("target")
        ]
        result.append({
            "id":            layer_id,
            "name":          l.get("name", layer_id),
            "source_type":   (l.get("source") or {}).get("type"),
            "geometry_type": l.get("geometry_type"),
            "feature_count": l.get("feature_count"),
            "schema_status": l.get("schema_status"),
            "relations":     rels,
        })
    return result


@app.get("/joins", summary="Lista de joins disponibles")
def list_joins():
    layers = load_layers()
    joins  = []
    for src_id, l in layers.items():
        for r in (l.get("relations") or []):
            tgt_id    = r.get("target")
            transform = r.get("join_transform") or {}
            has_tf    = bool(transform.get("src") or transform.get("tgt"))
            joins.append({
                "src":           src_id,
                "tgt":           tgt_id,
                "join_on":       r.get("join_on"),
                "join_type":     r.get("join_type"),
                "has_transform": has_tf,
                "transform":     transform if has_tf else None,
                "endpoint":      f"/joins/{src_id}/{tgt_id}",
            })
    return joins


@app.get("/joins/{src_id}/{tgt_id}", summary="Ejecutar un join entre dos capas")
def execute_join(
    src_id: str,
    tgt_id: str,
    limit: int | None = Query(
        default=None,
        description="Limitar número de registros origen (útil para pruebas). Sin límite por defecto.",
        ge=1,
    ),
):
    layers    = load_layers()
    src_layer = layers.get(src_id)
    tgt_layer = layers.get(tgt_id)

    if not src_layer:
        raise HTTPException(404, f"Capa no encontrada: {src_id!r}")
    if not tgt_layer:
        raise HTTPException(404, f"Capa no encontrada: {tgt_id!r}")

    relation = find_relation(layers, src_id, tgt_id)
    if not relation:
        raise HTTPException(
            404,
            f"No hay relación declarada de {src_id!r} hacia {tgt_id!r}. "
            f"Consultar /joins para ver las relaciones disponibles.",
        )

    try:
        src_col, tgt_col = parse_join_on(relation["join_on"])
    except ValueError as e:
        raise HTTPException(500, str(e))

    transform  = relation.get("join_transform") or {}
    src_tf     = transform.get("src")
    tgt_tf     = transform.get("tgt")
    exceptions = [e["value"] for e in (relation.get("join_exceptions") or [])]

    # Obtener datos de ambas fuentes
    try:
        src_records = fetch_layer(src_layer, limit=limit)
    except Exception as e:
        raise HTTPException(502, f"Error al obtener datos de '{src_id}': {e}")

    try:
        tgt_records = fetch_layer(tgt_layer)   # tgt siempre completo
    except Exception as e:
        raise HTTPException(502, f"Error al obtener datos de '{tgt_id}': {e}")

    # Prefijo para columnas del target (primera palabra del layer_id)
    tgt_prefix = tgt_id.split("_")[0] + "_"

    try:
        data, stats = do_join(
            src_records, tgt_records,
            src_col, tgt_col,
            src_tf, tgt_tf,
            tgt_prefix,
        )
    except Exception as e:
        raise HTTPException(500, f"Error en el join: {e}")

    return {
        "join_info": {
            "src":              src_id,
            "tgt":              tgt_id,
            "join_on":          relation["join_on"],
            "join_type":        relation.get("join_type"),
            "join_transform":   transform if (src_tf or tgt_tf) else None,
            "known_exceptions": exceptions or None,
            "tgt_col_prefix":   tgt_prefix,
            **stats,
        },
        "data": data,
    }
