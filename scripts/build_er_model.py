"""
build_er_model.py

Lee todos los YAMLs en catalog/layers/ y genera:
  - catalog/er_model.json   (grafo estructurado, para el futuro frontend)
  - catalog/er_model.png    (diagrama ER estático)

Los nodos son las capas documentadas en el catálogo.
Las aristas emergen de la sección `relations:` de cada YAML.
Las capas referenciadas pero no presentes en el catálogo aparecen como
nodos fantasma (borde punteado) para indicar que están pendientes de curar.

Verificación de relaciones (activada por defecto):
  Por cada relación declarada en `relations:`, se consultan los valores
  distintos de las columnas FK y PK directamente contra la fuente viva
  (WFS / ArcGIS REST) o el archivo estático completo. La relación pasa
  solo si se cumple integridad referencial y cardinalidad declarada.
  Si alguna relación falla, el build termina con código de salida 1.
  Los resultados se cachean 24h en catalog/verification_cache/.

Uso:
  python scripts/build_er_model.py
  python scripts/build_er_model.py --no-verify
  python scripts/build_er_model.py --clear-cache
  python scripts/build_er_model.py --format svg
  python scripts/build_er_model.py --out catalog/mi_diagrama
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from graphviz import Digraph

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR = CATALOG_DIR / "layers"
CACHE_DIR = CATALOG_DIR / "verification_cache"
CACHE_TTL_HOURS = 24

# ── paleta visual ─────────────────────────────────────────────────────────────
STATUS_STYLE = {
    "verified":             ("#2980B9", "white",   "#EBF5FB", "#2980B9"),
    "pending_verification": ("#7F8C8D", "white",   "#F8F9FA", "#7F8C8D"),
    "outdated":             ("#C0392B", "white",   "#FDEDEC", "#C0392B"),
}
GHOST_STYLE = ("#BDC3C7", "#7F8C8D", "#FDFEFE", "#BDC3C7")

GEOMETRY_ICON = {
    "point":   "●",
    "polygon": "⬡",
    "line":    "〜",
    "raster":  "▦",
}

ARROW_STYLE = {
    "many_to_one":  {"arrowhead": "normal", "arrowtail": "none"},
    "one_to_many":  {"arrowhead": "crow",   "arrowtail": "none"},
    "one_to_one":   {"arrowhead": "tee",    "arrowtail": "tee"},
    "many_to_many": {"arrowhead": "crow",   "arrowtail": "crow"},
}

# Color de arista según resultado de verificación
EDGE_COLOR = {
    "pass":  "#27AE60",  # verde — relación verificada
    "fail":  "#C0392B",  # rojo  — relación inválida
    "skip":  "#E67E22",  # naranja — nodo fantasma, no verificable
    "error": "#C0392B",  # rojo  — error de red o parseo
    None:    "#555555",  # gris  — verificación no ejecutada (--no-verify)
}


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    edge: dict
    status: str   # "pass" | "fail" | "skip" | "error"
    reason: str
    details: dict = field(default_factory=dict)


# ── caché de valores ──────────────────────────────────────────────────────────

def _cache_path(layer_id: str, column: str) -> Path:
    return CACHE_DIR / f"{layer_id}__{column}.json"


def _read_cache(layer_id: str, column: str) -> dict | None:
    path = _cache_path(layer_id, column)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if (time.time() - data["timestamp"]) / 3600 > CACHE_TTL_HOURS:
        return None
    return data


def _write_cache(layer_id: str, column: str, distinct: set, total: int):
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(layer_id, column).write_text(json.dumps({
        "timestamp": time.time(),
        "layer_id": layer_id,
        "column": column,
        "distinct_values": sorted(str(v) for v in distinct if v is not None),
        "total_count": total,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_cache():
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
        print(f"Caché borrada: {CACHE_DIR}")


# ── fetch: WFS ────────────────────────────────────────────────────────────────

def _fetch_wfs(source: dict, layer_id: str, column: str) -> tuple[set, int]:
    """
    Retorna (distinct_values, total_count) consultando todos los valores
    de `column` vía WFS GetFeature paginado con propertyName.
    total_count incluye duplicados para verificar cardinalidad.
    """
    cached = _read_cache(layer_id, column)
    if cached:
        print(f"      (desde caché)")
        return set(cached["distinct_values"]), cached["total_count"]

    base_url = f"https://geoportal.cl/geoserver/{source['workspace']}/wfs"
    typename = source["typename"]
    page_size = 5000
    start_index = 0
    all_values: list = []

    while True:
        resp = requests.get(base_url, params={
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": typename,
            "propertyName": column,
            "outputFormat": "application/json",
            "maxFeatures": page_size,
            "startIndex": start_index,
        }, timeout=120)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            break
        for f in features:
            val = (f.get("properties") or {}).get(column)
            all_values.append(str(val) if val is not None else None)
        if len(features) < page_size:
            break
        start_index += page_size

    distinct = {v for v in all_values if v is not None}
    total = len(all_values)
    _write_cache(layer_id, column, distinct, total)
    return distinct, total


# ── fetch: ArcGIS REST ────────────────────────────────────────────────────────

def _fetch_arcgis(source: dict, layer_id: str, column: str) -> tuple[set, int]:
    """
    Retorna (distinct_values, total_count) vía ArcGIS REST.
    distinct_values: usando returnDistinctValues=true (paginado).
    total_count: usando returnCountOnly=true (para verificar unicidad).
    """
    cached = _read_cache(layer_id, column)
    if cached:
        print(f"      (desde caché)")
        return set(cached["distinct_values"]), cached["total_count"]

    base = f"{source['service_url']}/{source['layer_id']}/query"

    # total de features (con duplicados)
    count_resp = requests.get(base, params={
        "where": "1=1",
        "returnCountOnly": "true",
        "f": "json",
    }, timeout=60)
    count_resp.raise_for_status()
    count_data = count_resp.json()
    if "error" in count_data:
        raise RuntimeError(f"ArcGIS error: {count_data['error']}")
    total_count = count_data.get("count", 0)

    # valores distintos (paginado)
    distinct: set = set()
    offset = 0
    page_size = 2000

    while True:
        resp = requests.get(base, params={
            "where": "1=1",
            "outFields": column,
            "returnDistinctValues": "true",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        features = data.get("features", [])
        for f in features:
            val = (f.get("attributes") or {}).get(column)
            if val is not None:
                distinct.add(str(val))
        if not data.get("exceededTransferLimit", False) or not features:
            break
        offset += page_size

    _write_cache(layer_id, column, distinct, total_count)
    return distinct, total_count


# ── fetch: static ─────────────────────────────────────────────────────────────

DEFAULT_DATABASE_URL = "postgresql://geoportal:geoportal@localhost:5432/geoportal"


def _fetch_static(source: dict, layer_id: str, column: str) -> tuple[set, int]:
    """
    Consulta los valores de `column` desde la tabla postgres del layer.
    La tabla la carga `make load` y contiene el 100% de los datos del archivo estático.
    Requiere postgres corriendo con DATABASE_URL configurado.
    """
    cached = _read_cache(layer_id, column)
    if cached:
        print(f"      (desde caché)")
        return set(cached["distinct_values"]), cached["total_count"]

    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 no instalado. Ejecutar: pip install psycopg2-binary")

    db_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    print(f"      Consultando postgres: tabla={layer_id!r}, columna={column!r}")

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        raise RuntimeError(
            f"No se pudo conectar a postgres ({db_url}): {e}\n"
            f"Ejecutar 'make db-up' y luego 'make load LAYER={layer_id}' antes de build-er."
        )

    try:
        with conn.cursor() as cur:
            # total de filas (con duplicados) para verificar cardinalidad
            cur.execute(f'SELECT COUNT(*) FROM "{layer_id}"')
            total = cur.fetchone()[0]

            # todos los valores distintos de la columna
            cur.execute(f'SELECT DISTINCT "{column}" FROM "{layer_id}" WHERE "{column}" IS NOT NULL')
            distinct = {str(row[0]) for row in cur.fetchall()}
    except psycopg2.errors.UndefinedTable:
        raise RuntimeError(
            f"Tabla '{layer_id}' no existe en postgres. "
            f"Ejecutar 'make load LAYER={layer_id}' primero."
        )
    finally:
        conn.close()

    _write_cache(layer_id, column, distinct, total)
    return distinct, total


# ── dispatcher ────────────────────────────────────────────────────────────────

def fetch_column_values(layer: dict, column: str) -> tuple[set, int]:
    """
    Retorna (distinct_values, total_count) para `column` en `layer`.
    Delega a WFS, ArcGIS REST o static según source.type.
    """
    layer_id = layer.get("id", "unknown")
    source = layer.get("source", {})
    src_type = source.get("type", "")

    if src_type == "wfs":
        return _fetch_wfs(source, layer_id, column)
    elif src_type == "arcgis_rest":
        return _fetch_arcgis(source, layer_id, column)
    elif src_type == "static":
        return _fetch_static(source, layer_id, column)
    else:
        raise RuntimeError(f"Tipo de fuente desconocido: {src_type!r} en capa {layer_id!r}")


# ── verificación de relaciones ────────────────────────────────────────────────

def _apply_transform(values: set, transform: str | None) -> set:
    """Aplica un transform de vocabulario controlado a un conjunto de valores."""
    if not transform:
        return values
    if transform.startswith("zfill:"):
        n = int(transform.split(":")[1])
        return {v.zfill(n) for v in values}
    raise ValueError(f"join_transform desconocido: {transform!r}. Transforms válidos: zfill:N")


def _parse_join_on(join_on: str) -> tuple[str, str]:
    parts = re.split(r"\s*=\s*", join_on.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"Formato inválido en join_on: {join_on!r}. Esperado: 'col_a = col_b'")
    return parts[0].strip(), parts[1].strip()


def verify_edge(edge: dict, layers: dict) -> VerificationResult:
    src_id   = edge["from"]
    tgt_id   = edge["to"]
    join_on  = edge.get("join_on", "")
    jtype    = edge.get("join_type", "many_to_one")

    src_layer = layers.get(src_id)
    tgt_layer = layers.get(tgt_id)

    if not src_layer or not tgt_layer:
        return VerificationResult(
            edge=edge, status="skip",
            reason=f"nodo fantasma: {tgt_id!r} no tiene YAML — agregar capa al catálogo para verificar",
        )

    try:
        src_col, tgt_col = _parse_join_on(join_on)
    except ValueError as e:
        return VerificationResult(edge=edge, status="error", reason=str(e))

    transform    = edge.get("join_transform") or {}
    src_tf       = transform.get("src")
    tgt_tf       = transform.get("tgt")
    exceptions   = set(edge.get("join_exceptions") or [])
    tf_label     = f" [transform: src={src_tf or 'none'}, tgt={tgt_tf or 'none'}]" if (src_tf or tgt_tf) else ""
    print(f"  [{jtype}] {src_id}.{src_col} → {tgt_id}.{tgt_col}{tf_label}")

    try:
        src_distinct_raw, src_total = fetch_column_values(src_layer, src_col)
        tgt_distinct_raw, tgt_total = fetch_column_values(tgt_layer, tgt_col)
    except Exception as e:
        return VerificationResult(edge=edge, status="error",
            reason=f"error al consultar datos: {e}")

    try:
        src_distinct = _apply_transform(src_distinct_raw, src_tf)
        tgt_distinct = _apply_transform(tgt_distinct_raw, tgt_tf)
    except ValueError as e:
        return VerificationResult(edge=edge, status="error", reason=str(e))

    details = {
        "src_col": src_col, "tgt_col": tgt_col,
        "src_distinct_count": len(src_distinct), "src_total": src_total,
        "tgt_distinct_count": len(tgt_distinct), "tgt_total": tgt_total,
        "join_transform": transform,
    }

    failures: list[str] = []

    def _check_orphans(orphaned: set, label: str) -> None:
        real = orphaned - exceptions
        if real:
            sample = ", ".join(sorted(real)[:5])
            failures.append(
                f"{len(real)} valor(es) de {label} sin referencia "
                f"(ejemplos: {sample})"
            )
        if orphaned & exceptions:
            details.setdefault("known_exceptions_applied", []).extend(
                sorted(orphaned & exceptions)
            )

    if jtype == "many_to_one":
        orphaned   = src_distinct - tgt_distinct
        tgt_unique = (len(tgt_distinct) == tgt_total)
        details["orphaned_src_keys"] = sorted(orphaned - exceptions)[:20]
        details["tgt_col_unique"]    = tgt_unique
        _check_orphans(orphaned, f"{src_id}.{src_col}")
        if not tgt_unique:
            dups = tgt_total - len(tgt_distinct)
            failures.append(
                f"{tgt_id}.{tgt_col} tiene {dups} fila(s) duplicadas "
                f"— no puede ser el lado 'one' de many_to_one"
            )

    elif jtype == "one_to_many":
        orphaned   = tgt_distinct - src_distinct
        src_unique = (len(src_distinct) == src_total)
        details["orphaned_tgt_keys"] = sorted(orphaned - exceptions)[:20]
        details["src_col_unique"]    = src_unique
        _check_orphans(orphaned, f"{tgt_id}.{tgt_col}")
        if not src_unique:
            dups = src_total - len(src_distinct)
            failures.append(
                f"{src_id}.{src_col} tiene {dups} fila(s) duplicadas "
                f"— no puede ser el lado 'one' de one_to_many"
            )

    elif jtype == "one_to_one":
        orphaned_fwd = src_distinct - tgt_distinct
        orphaned_bwd = tgt_distinct - src_distinct
        src_unique   = (len(src_distinct) == src_total)
        tgt_unique   = (len(tgt_distinct) == tgt_total)
        details.update({
            "orphaned_src_keys": sorted(orphaned_fwd - exceptions)[:20],
            "orphaned_tgt_keys": sorted(orphaned_bwd - exceptions)[:20],
            "src_col_unique":    src_unique,
            "tgt_col_unique":    tgt_unique,
        })
        _check_orphans(orphaned_fwd, f"{src_id}.{src_col}")
        _check_orphans(orphaned_bwd, f"{tgt_id}.{tgt_col}")
        if not src_unique:
            failures.append(f"{src_id}.{src_col} tiene duplicados — no puede ser lado 'one'")
        if not tgt_unique:
            failures.append(f"{tgt_id}.{tgt_col} tiene duplicados — no puede ser lado 'one'")

    elif jtype == "many_to_many":
        overlap = src_distinct & tgt_distinct
        details["overlap_count"] = len(overlap)
        if not overlap:
            failures.append(
                f"Sin valores en común entre {src_id}.{src_col} y {tgt_id}.{tgt_col} "
                f"— el JOIN no produciría ningún resultado"
            )

    if failures:
        return VerificationResult(
            edge=edge, status="fail",
            reason="; ".join(failures),
            details=details,
        )

    return VerificationResult(
        edge=edge, status="pass",
        reason=(
            f"{len(src_distinct)} distinct en {src_id}.{src_col}, "
            f"{len(tgt_distinct)} distinct en {tgt_id}.{tgt_col}"
        ),
        details=details,
    )


def verify_all_relations(layers: dict, edges: list) -> list[VerificationResult]:
    print("\nVerificando relaciones contra datos reales...")
    results: list[VerificationResult] = []
    for edge in edges:
        result = verify_edge(edge, layers)
        icon = {"pass": "✓", "fail": "✗", "skip": "⚠", "error": "✗"}.get(result.status, "?")
        print(f"    {icon} {result.status.upper()}: {result.reason}")
        results.append(result)
    return results


# ── carga de capas ────────────────────────────────────────────────────────────

def load_layers() -> dict[str, dict]:
    layers: dict[str, dict] = {}
    for f in sorted(LAYERS_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        layer_id = data.get("id", f.stem)
        layers[layer_id] = data
    return layers


def collect_edges(layers: dict[str, dict]) -> list[dict]:
    seen: set[tuple] = set()
    edges: list[dict] = []
    for src_id, layer in layers.items():
        for rel in (layer.get("relations") or []):
            tgt_id  = rel.get("target")
            join_on = rel.get("join_on", "")
            key = (src_id, tgt_id, join_on)
            if key in seen or not tgt_id:
                continue
            seen.add(key)
            transform  = rel.get("join_transform") or {}
            exceptions = [e["value"] for e in (rel.get("join_exceptions") or [])]
            edges.append({
                "from":                  src_id,
                "to":                    tgt_id,
                "join_on":               join_on,
                "join_type":             rel.get("join_type", "many_to_one"),
                "description":           rel.get("description", ""),
                "join_transform":        {
                    "src": transform.get("src"),
                    "tgt": transform.get("tgt"),
                },
                "join_transform_note":   rel.get("join_transform_note", ""),
                "join_exceptions":       exceptions,
                "verification_status":   None,
                "verification_date":     None,
                "verification_details":  {},
            })
    return edges


def ghost_node_ids(layers: dict[str, dict], edges: list[dict]) -> set[str]:
    return {e["to"] for e in edges} - set(layers.keys())


# ── construcción del JSON ─────────────────────────────────────────────────────

def build_json(layers: dict, edges: list, ghosts: set) -> dict:
    nodes = []
    for layer_id, l in layers.items():
        nodes.append({
            "id":            layer_id,
            "name":          l.get("name", layer_id),
            "source":        l.get("source", ""),
            "organization":  l.get("organization", ""),
            "geoportal_id":  l.get("geoportal_id"),
            "geometry_type": l.get("geometry_type", ""),
            "feature_count": l.get("feature_count"),
            "schema_status": l.get("schema_status", "pending_verification"),
            "use_cases":     l.get("use_cases", []),
            "is_ghost":      False,
        })
    for gid in sorted(ghosts):
        nodes.append({"id": gid, "name": gid, "is_ghost": True})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes":        nodes,
        "edges":        edges,
    }


# ── construcción del grafo Graphviz ──────────────────────────────────────────

def _node_label(layer_id: str, layer: dict) -> str:
    status = layer.get("schema_status", "pending_verification")
    header_bg, header_font, row_bg, _ = STATUS_STYLE.get(status, STATUS_STYLE["pending_verification"])

    name       = layer.get("name", layer_id)
    source_data = layer.get("source", {})
    source_type = source_data.get("type", "") if isinstance(source_data, dict) else ""
    source_label = {
        "wfs": "WFS", "arcgis_rest": "ArcGIS REST", "static": "Snapshot estática",
    }.get(source_type, source_type)
    geom      = GEOMETRY_ICON.get(layer.get("geometry_type", ""), "")
    count     = layer.get("feature_count")
    count_str = f"{count:,}".replace(",", ".") + " registros" if count else "N registros"
    status_label = {
        "verified":             "✓ verificada",
        "pending_verification": "⚠ pendiente",
        "outdated":             "✗ desactualizada",
    }.get(status, status)

    geom_count = f"{geom}  {count_str}" if geom else count_str
    return (
        f'<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" BGCOLOR="{row_bg}">'
        f'<TR><TD BGCOLOR="{header_bg}"><FONT COLOR="{header_font}"><B>{name}</B></FONT></TD></TR>'
        f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="10">{geom_count}  ·  {source_label}</FONT></TD></TR>'
        f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9" COLOR="{header_bg}">{status_label}</FONT></TD></TR>'
        f'</TABLE>>'
    )


def _ghost_label(layer_id: str) -> str:
    header_bg, header_font, row_bg, _ = GHOST_STYLE
    return (
        f'<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" BGCOLOR="{row_bg}">'
        f'<TR><TD BGCOLOR="{header_bg}"><FONT COLOR="{header_font}"><B>{layer_id}</B></FONT></TD></TR>'
        f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9" COLOR="#7F8C8D">pendiente de curar</FONT></TD></TR>'
        f'</TABLE>>'
    )


def build_graph(layers: dict, edges: list, ghosts: set) -> Digraph:
    dot = Digraph(
        name="Geoportal Chile — Modelo ER",
        comment="Generado por build_er_model.py",
    )
    dot.attr(
        rankdir="TB", splines="ortho", nodesep="0.6", ranksep="0.9",
        fontname="Helvetica", bgcolor="white",
        label=r"Geoportal Chile — Modelo Entidad-Relación\nGenerado automáticamente desde catalog/layers/",
        labelloc="t", fontsize="14",
    )
    dot.attr("node", shape="none", fontname="Helvetica", margin="0")
    dot.attr("edge", fontname="Helvetica", fontsize="9")

    for layer_id, layer in layers.items():
        status = layer.get("schema_status", "pending_verification")
        _, _, _, border = STATUS_STYLE.get(status, STATUS_STYLE["pending_verification"])
        dot.node(layer_id, label=_node_label(layer_id, layer), color=border, style="")

    for gid in sorted(ghosts):
        dot.node(gid, label=_ghost_label(gid), color=GHOST_STYLE[3], style="dashed")

    for edge in edges:
        arrow  = ARROW_STYLE.get(edge["join_type"], ARROW_STYLE["many_to_one"])
        vstatus = edge.get("verification_status")
        color  = EDGE_COLOR.get(vstatus, EDGE_COLOR[None])
        vdate  = edge.get("verification_date", "")
        suffix = f"  [{vdate[:10]}]" if vstatus == "pass" and vdate else ""
        dot.edge(
            edge["from"], edge["to"],
            label=f'  {edge["join_on"]}{suffix}  ',
            dir="both",
            color=color,
            **arrow,
        )

    return dot


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Genera el modelo ER del catálogo de capas")
    parser.add_argument("--format", default="png", choices=["png", "svg", "pdf"])
    parser.add_argument("--out", default=None, help="Ruta de salida sin extensión")
    parser.add_argument("--no-verify", action="store_true",
                        help="Omitir verificación de relaciones contra datos reales")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Borrar caché de verificación antes de ejecutar")
    args = parser.parse_args()

    if args.clear_cache:
        clear_cache()

    out_base = Path(args.out) if args.out else CATALOG_DIR / "er_model"

    print("Cargando capas del catálogo...")
    layers = load_layers()
    print(f"  {len(layers)} capas: {', '.join(layers.keys())}")

    edges  = collect_edges(layers)
    ghosts = ghost_node_ids(layers, edges)
    print(f"  {len(edges)} relaciones, {len(ghosts)} nodos fantasma: "
          f"{', '.join(sorted(ghosts)) or 'ninguno'}")

    # ── verificación ──────────────────────────────────────────────────────────
    if not args.no_verify and edges:
        results = verify_all_relations(layers, edges)

        now_iso = datetime.now(timezone.utc).isoformat()
        result_by_key: dict[tuple, VerificationResult] = {
            (r.edge["from"], r.edge["to"], r.edge["join_on"]): r
            for r in results
        }
        for edge in edges:
            key = (edge["from"], edge["to"], edge["join_on"])
            r   = result_by_key.get(key)
            if r:
                edge["verification_status"]  = r.status
                edge["verification_date"]    = now_iso
                edge["verification_details"] = r.details

        failures = [r for r in results if r.status in ("fail", "error")]
        print()
        if failures:
            print("=" * 60)
            print(f"VERIFICACIÓN FALLIDA — {len(failures)} relación(es) inválida(s):\n")
            for r in failures:
                e = r.edge
                print(f"  ✗ {e['from']} → {e['to']} [{e['join_on']}]")
                print(f"    {r.reason}")
                if r.details.get("orphaned_src_keys"):
                    print(f"    Claves huérfanas: {r.details['orphaned_src_keys']}")
                print()
            print("Corregir las relaciones en los YAML antes de generar el ER.")
            print("=" * 60)
            sys.exit(1)
        else:
            passes = sum(1 for r in results if r.status == "pass")
            skips  = sum(1 for r in results if r.status == "skip")
            print(f"Verificación OK: {passes} relación(es) válida(s)"
                  + (f", {skips} omitida(s) por nodo fantasma" if skips else "") + ".")
    elif args.no_verify:
        print("Verificación omitida (--no-verify).")

    # ── JSON ──────────────────────────────────────────────────────────────────
    json_path = out_base.with_suffix(".json")
    model = build_json(layers, edges, ghosts)
    json_path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON guardado: {json_path}")

    # ── imagen ────────────────────────────────────────────────────────────────
    dot      = build_graph(layers, edges, ghosts)
    rendered = dot.render(str(out_base), format=args.format, cleanup=True)
    print(f"Imagen guardada: {rendered}")
    print("\nListo.")


if __name__ == "__main__":
    main()
