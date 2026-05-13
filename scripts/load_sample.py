"""
load_sample.py

Paso 2 del flujo de curado: descarga N filas de una capa vía WFS, ArcGIS REST
o snapshot estática (GeoJSON/Shapefile) y las carga en PostgreSQL + PostGIS.
También guarda la muestra como JSON en catalog/samples/{layer}/{fecha}.json.

Uso:
  python scripts/load_sample.py --layer establecimientos_salud
  python scripts/load_sample.py --layer establecimientos_salud --rows 50

Variables de entorno:
  DATABASE_URL  (default: postgresql://geoportal:geoportal@localhost:5432/geoportal)
"""

import argparse
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
import yaml

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR = CATALOG_DIR / "layers"
SAMPLES_DIR = CATALOG_DIR / "samples"

GEOSERVER_BASE = "https://geoportal.cl/geoserver"
TIMEOUT_SECONDS = 30
DEFAULT_ROWS = 100
DEFAULT_DATABASE_URL = "postgresql://geoportal:geoportal@localhost:5432/geoportal"

YAML_TO_PG = {
    "int":      "INTEGER",
    "float":    "DOUBLE PRECISION",
    "string":   "TEXT",
    "date":     "DATE",
    "geometry": "geometry",  # manejado por separado
}


# ── helpers ───────────────────────────────────────────────────────────────────

def load_layer_yaml(layer_id: str) -> dict:
    path = LAYERS_DIR / f"{layer_id}.yaml"
    if not path.exists():
        print(f"ERROR: no se encontró {path}")
        sys.exit(1)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def fetch_sample(layer: dict, n: int) -> dict | None:
    source = layer.get("source", {})
    source_type = source.get("type")
    if source_type == "wfs":
        return _fetch_wfs(source, n)
    elif source_type == "arcgis_rest":
        return _fetch_arcgis(source, n)
    elif source_type == "static":
        return _fetch_static(source, n)
    else:
        print(f"FALLÓ: tipo de fuente desconocido: {repr(source_type)}")
        return None


def _fetch_wfs(source: dict, n: int) -> dict | None:
    workspace = source["workspace"]
    typename = source["typename"]
    short_typename = typename.split(":")[-1] if ":" in typename else typename
    url = (
        f"{GEOSERVER_BASE}/{workspace}/wfs"
        f"?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName={short_typename}&maxFeatures={n}&outputFormat=application/json"
    )
    print(f"  Descargando {n} filas desde WFS...", end=" ", flush=True)
    return _get_json(url, total_key="totalFeatures")


def _fetch_static(source: dict, n: int) -> dict | None:
    fmt = source.get("format", "")
    url = source.get("download_url", "")
    if fmt == "geojson":
        return _fetch_static_geojson(url)
    elif fmt == "shapefile":
        return _fetch_static_shapefile(url)
    elif fmt == "rar":
        return _fetch_static_rar(url)
    else:
        print(f"FALLÓ: formato estático no soportado: {repr(fmt)}")
        return None


def _fetch_static_geojson(url: str) -> dict | None:
    print(f"  Descargando GeoJSON estático...", end=" ", flush=True)
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        data = r.json()
        if data.get("type") != "FeatureCollection":
            print(f"FALLÓ: tipo inesperado {repr(data.get('type'))}")
            return None
        total = len(data.get("features", []))
        data["totalFeatures"] = total
        print(f"OK  ({total} features en el archivo)")
        return data
    except Exception as e:
        print(f"FALLÓ: {e}")
        return None


def _fetch_static_shapefile(url: str) -> dict | None:
    try:
        import fiona
    except ImportError:
        print("FALLÓ: fiona no instalado — ejecuta: pip install fiona")
        return None

    print(f"  Descargando Shapefile ZIP...", end=" ", flush=True)
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
    except Exception as e:
        print(f"FALLÓ: {e}")
        return None
    print(f"OK  ({len(r.content) // 1024} KB)")

    print(f"  Leyendo Shapefile...", end=" ", flush=True)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                zf.extractall(tmpdir)
            shp_files = list(Path(tmpdir).rglob("*.shp"))
            if not shp_files:
                print("FALLÓ: no se encontró .shp en el ZIP")
                return None
            shp_path = str(shp_files[0])
            features = []
            with fiona.open(shp_path) as src:
                total = len(src)
                for feat in src:
                    features.append({
                        "type": "Feature",
                        "geometry": dict(feat["geometry"]) if feat["geometry"] else None,
                        "properties": dict(feat["properties"]),
                    })
        print(f"OK  ({total} features en el archivo)")
        return {
            "type": "FeatureCollection",
            "features": features,
            "totalFeatures": total,
        }
    except Exception as e:
        print(f"FALLÓ: {e}")
        return None


def _fetch_static_rar(url: str) -> dict | None:
    try:
        import rarfile
    except ImportError:
        print("FALLÓ: rarfile no instalado — ejecuta: pip install rarfile")
        return None

    print(f"  Descargando RAR...", end=" ", flush=True)
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
    except Exception as e:
        print(f"FALLÓ: {e}")
        return None
    print(f"OK  ({len(r.content) // 1024} KB)")

    print(f"  Leyendo Shapefile desde RAR...", end=" ", flush=True)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            rar_path = os.path.join(tmpdir, "archive.rar")
            with open(rar_path, "wb") as f:
                f.write(r.content)
            with rarfile.RarFile(rar_path) as rf:
                rf.extractall(tmpdir)
            shp_files = list(Path(tmpdir).rglob("*.shp"))
            if not shp_files:
                print("FALLÓ: no se encontró .shp en el RAR")
                return None
            import fiona
            features = []
            with fiona.open(str(shp_files[0])) as src:
                total = len(src)
                for feat in src:
                    features.append({
                        "type": "Feature",
                        "geometry": dict(feat["geometry"]) if feat["geometry"] else None,
                        "properties": dict(feat["properties"]),
                    })
        print(f"OK  ({total} features en el archivo)")
        return {
            "type": "FeatureCollection",
            "features": features,
            "totalFeatures": total,
        }
    except Exception as e:
        print(f"FALLÓ: {e}")
        return None


def _fetch_arcgis(source: dict, n: int) -> dict | None:
    service_url = source["service_url"]
    layer_id = source["layer_id"]

    # Total de features
    count_url = f"{service_url}/{layer_id}/query?where=1=1&returnCountOnly=true&f=json"
    try:
        total = requests.get(count_url, timeout=TIMEOUT_SECONDS).json().get("count", "?")
    except Exception:
        total = "?"

    url = (
        f"{service_url}/{layer_id}/query"
        f"?where=1=1&outFields=*&resultRecordCount={n}&returnGeometry=true&f=geojson"
    )
    print(f"  Descargando {n} filas desde ArcGIS REST...", end=" ", flush=True)
    data = _get_json(url, total_override=total)
    if data and "totalFeatures" not in data:
        data["totalFeatures"] = total
    return data


def _get_json(url: str, total_key: str = None, total_override=None) -> dict | None:
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        total = total_override if total_override is not None else data.get(total_key, "?")
        print(f"OK  ({total} totales en la capa)")
        return data
    except requests.exceptions.Timeout:
        print("FALLÓ: timeout")
    except requests.exceptions.HTTPError as e:
        print(f"FALLÓ: HTTP {e.response.status_code}")
    except requests.exceptions.ConnectionError:
        print("FALLÓ: no se pudo conectar")
    except ValueError:
        print("FALLÓ: respuesta no es JSON válido")
    return None


def validate_geojson(data: dict) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "no es un objeto JSON"
    if data.get("type") != "FeatureCollection":
        return False, f"type={repr(data.get('type'))} — se esperaba 'FeatureCollection'"
    features = data.get("features", [])
    if not features:
        return False, "sin features (¿typename incorrecto?)"
    for i, f in enumerate(features):
        if f.get("type") != "Feature":
            return False, f"feature[{i}].type incorrecto"
        if not isinstance(f.get("properties"), dict):
            return False, f"feature[{i}] sin properties"
    return True, ""


def detect_geom_type(features: list) -> str | None:
    for f in features:
        geom = f.get("geometry")
        if geom:
            return geom.get("type")
    return None


def build_column_defs(layer: dict, sample_props: dict) -> list[tuple[str, str]]:
    """
    Retorna [(col_name, pg_type), ...] para todas las columnas de properties.
    Usa tipos del YAML cuando están disponibles; infiere del valor si no.
    """
    yaml_cols = layer.get("columns") or {}
    cols = []
    for col_name, value in sample_props.items():
        if col_name in yaml_cols:
            yaml_type = yaml_cols[col_name].get("type", "string")
            pg_type = YAML_TO_PG.get(yaml_type, "TEXT")
        else:
            if isinstance(value, bool):
                pg_type = "BOOLEAN"
            elif isinstance(value, int):
                pg_type = "INTEGER"
            elif isinstance(value, float):
                pg_type = "DOUBLE PRECISION"
            else:
                pg_type = "TEXT"
        cols.append((col_name, pg_type))
    return cols


def create_table(conn, table_name: str, col_defs: list[tuple[str, str]], geom_pg_type: str) -> None:
    col_parts = [f'"{name}" {pg_type}' for name, pg_type in col_defs]
    col_parts.append(f'"geom" {geom_pg_type}')
    ddl = (
        f'DROP TABLE IF EXISTS "{table_name}";\n'
        f'CREATE TABLE "{table_name}" (\n  '
        + ",\n  ".join(col_parts)
        + "\n);"
    )
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    print(f"  Tabla '{table_name}' creada ({len(col_defs)} columnas + geom)")


def insert_rows(conn, table_name: str, col_defs: list[tuple[str, str]], features: list) -> None:
    col_names = [name for name, _ in col_defs]
    placeholders = ", ".join(["%s"] * len(col_names) + ["ST_GeomFromGeoJSON(%s)"])
    quoted_cols = ", ".join(f'"{c}"' for c in col_names) + ', "geom"'
    sql = f'INSERT INTO "{table_name}" ({quoted_cols}) VALUES ({placeholders})'

    rows = []
    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry")
        values = []
        for col_name, pg_type in col_defs:
            val = props.get(col_name)
            if pg_type == "DATE" and isinstance(val, str) and val.endswith("Z"):
                val = val[:-1]
            elif pg_type == "DATE" and isinstance(val, int):
                # Serial Excel: días desde 1899-12-30
                val = (date(1899, 12, 30) + timedelta(days=val)).isoformat()
            elif isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            values.append(val)
        values.append(json.dumps(geom) if geom else None)
        rows.append(tuple(values))

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows)
    conn.commit()
    print(f"  {len(rows)} filas insertadas en '{table_name}'")


def save_sample_json(layer_id: str, data: dict, n: int) -> Path:
    out_dir = SAMPLES_DIR / layer_id
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"{today}.json"
    sample = {
        "layer_id": layer_id,
        "sample_date": today,
        "total_features_in_layer": data.get("totalFeatures"),
        "sample_size": min(n, len(data.get("features", []))),
        "features": data.get("features", [])[:n],
    }
    out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Muestra guardada: {out_path}")
    return out_path


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Carga muestra de una capa en PostgreSQL + PostGIS")
    parser.add_argument("--layer", required=True, help="ID de la capa (ej: establecimientos_salud)")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help=f"Filas a descargar (default: {DEFAULT_ROWS})")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    print(f"\n{'='*60}")
    print(f"Capa: {args.layer}  ({args.rows} filas)")

    layer = load_layer_yaml(args.layer)
    source = layer.get("source", {})

    if not source or not source.get("type"):
        print("ERROR: bloque 'source' no definido en el YAML")
        sys.exit(1)

    print(f"  Fuente: {source['type']}")

    # 1. Descargar muestra desde bodega viva
    data = fetch_sample(layer, args.rows)
    if data is None:
        sys.exit(1)

    # 2. Validar GeoJSON (RFC 7946)
    print("  Validando GeoJSON...", end=" ", flush=True)
    ok, reason = validate_geojson(data)
    if not ok:
        print(f"FALLÓ — {reason}")
        sys.exit(1)
    geom_type = detect_geom_type(data["features"])
    print(f"OK  (geometría={geom_type})")

    # 3. Guardar muestra JSON en catalog/samples/
    save_sample_json(args.layer, data, args.rows)

    # 4. Preparar esquema de tabla
    sample_props = data["features"][0]["properties"]
    col_defs = build_column_defs(layer, sample_props)
    geom_pg_type = "geometry(Geometry, 4326)" if geom_type else "geometry"

    # 5. Conectar a postgres
    print(f"  Conectando a postgres...", end=" ", flush=True)
    try:
        conn = psycopg2.connect(db_url)
        print("OK")
    except Exception as e:
        print(f"FALLÓ: {e}")
        sys.exit(1)

    # 6. Crear tabla e insertar filas
    create_table(conn, args.layer, col_defs, geom_pg_type)
    insert_rows(conn, args.layer, col_defs, data["features"])
    conn.close()

    print(f"\nListo. Tabla '{args.layer}' disponible en postgres.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
