"""
check_schema_drift.py

Para cada capa en catalog/layers/*.yaml con schema_status != 'pending_verification':
  1. Llama a DescribeFeatureType en el GeoServer del geoportal
  2. Parsea las columnas del esquema XSD
  3. Compara contra el snapshot guardado en catalog/snapshots/<id>.json
  4. Muestra qué cambió (columnas nuevas, eliminadas, tipo cambiado)
  5. Guarda un nuevo snapshot con el estado actual

Para capas con schema_status == 'pending_verification':
  - Llama a DescribeFeatureType si workspace y typename están definidos
  - Muestra las columnas descubiertas para que el curador las documente
  - NO guarda snapshot (la capa no está curada aún)

Uso:
  python scripts/check_schema_drift.py                # todas las capas
  python scripts/check_schema_drift.py --layer establecimientos_salud
  python scripts/check_schema_drift.py --discover     # solo capas pending
  python scripts/check_schema_drift.py --save-all     # fuerza sobreescribir snapshots
"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR = CATALOG_DIR / "layers"
SNAPSHOTS_DIR = CATALOG_DIR / "snapshots"

GEOSERVER_BASE = "https://geoportal.cl/geoserver"
TIMEOUT_SECONDS = 30

XSD_NS = "http://www.w3.org/2001/XMLSchema"
# GeoServer usa tanto xsd: como xs: como prefijo — manejar ambos
XSD_PREFIXES = [f"{{{XSD_NS}}}", "{http://www.opengis.net/wfs/2.0}"]


# ── helpers ───────────────────────────────────────────────────────────────────

def load_layer_yamls(layer_id: str | None) -> list[dict]:
    files = sorted(LAYERS_DIR.glob("*.yaml"))
    layers = []
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        data["_file"] = f
        if layer_id is None or data.get("id") == layer_id:
            layers.append(data)
    return layers


GEOJSON_GEOMETRY_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
}


def fetch_geojson_sample(workspace: str, typename: str, n: int = 3) -> dict | None:
    """Descarga n features vía WFS GetFeature y devuelve el JSON parseado."""
    url = (
        f"{GEOSERVER_BASE}/{workspace}/wfs"
        f"?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName={typename}&maxFeatures={n}&outputFormat=application/json"
    )
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        print(f"  ERROR: timeout al contactar {url}")
    except requests.exceptions.HTTPError as e:
        print(f"  ERROR HTTP {e.response.status_code}: {url}")
    except requests.exceptions.ConnectionError:
        print(f"  ERROR: no se pudo conectar a {url}")
    except ValueError:
        print("  ERROR: la respuesta no es JSON válido")
    return None


def validate_geojson(data: dict) -> tuple[bool, str]:
    """
    Valida que data sea un GeoJSON FeatureCollection válido (RFC 7946).
    Retorna (True, "") si es válido, (False, motivo) si no.
    """
    if not isinstance(data, dict):
        return False, "la respuesta no es un objeto JSON"
    if data.get("type") != "FeatureCollection":
        return False, f"type={repr(data.get('type'))} — se esperaba 'FeatureCollection'"
    features = data.get("features")
    if not isinstance(features, list):
        return False, "el campo 'features' no es una lista"
    if not features:
        return False, "la capa no devolvió ningún feature (¿typename incorrecto?)"
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            return False, f"feature[{i}] no es un objeto"
        if f.get("type") != "Feature":
            return False, f"feature[{i}].type={repr(f.get('type'))} — se esperaba 'Feature'"
        if "properties" not in f or not isinstance(f["properties"], dict):
            return False, f"feature[{i}] no tiene 'properties' como objeto"
        geom = f.get("geometry")
        if geom is not None:
            if not isinstance(geom, dict):
                return False, f"feature[{i}].geometry no es un objeto"
            if geom.get("type") not in GEOJSON_GEOMETRY_TYPES:
                return False, f"feature[{i}].geometry.type={repr(geom.get('type'))} no es un tipo GeoJSON válido"
    return True, ""


def fetch_describe_feature_type(workspace: str, typename: str) -> str | None:
    url = (
        f"{GEOSERVER_BASE}/{workspace}/wfs"
        f"?service=WFS&version=2.0.0&request=DescribeFeatureType"
        f"&typeName={typename}"
    )
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.text
    except requests.exceptions.Timeout:
        print(f"  ERROR: timeout al contactar {url}")
    except requests.exceptions.HTTPError as e:
        print(f"  ERROR HTTP {e.response.status_code}: {url}")
    except requests.exceptions.ConnectionError:
        print(f"  ERROR: no se pudo conectar a {url}")
    return None


def parse_xsd_columns(xsd_text: str) -> dict[str, str]:
    """Devuelve {nombre_columna: tipo_xsd} desde el XSD de DescribeFeatureType."""
    root = ET.fromstring(xsd_text)
    columns: dict[str, str] = {}

    # Buscar todos los elementos xsd:element con atributo name y type
    for elem in root.iter(f"{{{XSD_NS}}}element"):
        name = elem.get("name")
        type_ = elem.get("type", "")
        if not name or name in ("", "the_geom"):
            continue
        # Limpiar prefijo de namespace del tipo (xsd:int → int, gml:... → gml:...)
        if ":" in type_:
            prefix, local = type_.split(":", 1)
            if prefix in ("xsd", "xs"):
                type_ = local
        columns[name] = type_ or "unknown"

    return columns


def schema_hash(columns: dict[str, str]) -> str:
    """Hash SHA-256 determinístico del esquema (sorted por nombre)."""
    canonical = json.dumps(dict(sorted(columns.items())), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_snapshot(layer_id: str) -> dict | None:
    path = SNAPSHOTS_DIR / f"{layer_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def fetch_arcgis_schema(service_url: str, layer_id: int) -> dict[str, str] | None:
    """Obtiene el esquema de un ArcGIS Feature Service layer como {nombre: tipo}."""
    url = f"{service_url}/{layer_id}?f=json"
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        return {
            f["name"]: f["type"].replace("esriFieldType", "").lower()
            for f in data.get("fields", [])
            if f["name"] not in ("Shape__Area", "Shape__Length")
        }
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def fetch_arcgis_sample(service_url: str, layer_id: int) -> dict | None:
    url = f"{service_url}/{layer_id}/query?where=1=1&outFields=*&resultRecordCount=3&returnGeometry=true&f=geojson"
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def save_snapshot(layer_id: str, source_ref: str, columns: dict[str, str]) -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_ref": source_ref,
        "hash": schema_hash(columns),
        "columns": dict(sorted(columns.items())),
    }
    path = SNAPSHOTS_DIR / f"{layer_id}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Snapshot guardado: {path.relative_to(Path.cwd()) if Path.cwd() in path.parents else path}")


def diff_schemas(old: dict[str, str], new: dict[str, str]) -> dict:
    added = {k: v for k, v in new.items() if k not in old}
    removed = {k: v for k, v in old.items() if k not in new}
    type_changed = {
        k: {"before": old[k], "after": new[k]}
        for k in new
        if k in old and old[k] != new[k]
    }
    return {"added": added, "removed": removed, "type_changed": type_changed}


def yaml_columns(layer: dict) -> set[str]:
    return set((layer.get("columns") or {}).keys())


# ── lógica principal ──────────────────────────────────────────────────────────

def check_layer(layer: dict, force_save: bool) -> bool:
    """Retorna True si no hubo errores."""
    layer_id = layer.get("id", layer["_file"].stem)
    source = layer.get("source", {})
    source_type = source.get("type")
    status = layer.get("schema_status", "pending_verification")

    print(f"\n{'='*60}")
    print(f"Capa: {layer_id}  [{status}]  fuente: {source_type or 'no definida'}")

    if not source_type:
        print("  SALTAR: bloque 'source' no definido")
        return True

    # ── WFS ──────────────────────────────────────────────────────────────────
    if source_type == "wfs":
        workspace = source.get("workspace")
        typename = source.get("typename")
        if not workspace or not typename:
            print("  SALTAR: workspace o typename no definidos")
            return True

        print(f"  Workspace : {workspace}")
        print(f"  TypeName  : {typename}")

        print("  Validando GeoJSON...", end=" ", flush=True)
        sample = fetch_geojson_sample(workspace, typename)
        if sample is None:
            print("FALLÓ (no se pudo descargar muestra)")
            return False
        is_valid, reason = validate_geojson(sample)
        if not is_valid:
            print(f"FALLÓ — {reason}")
            return False
        geom_type = sample["features"][0].get("geometry", {}).get("type", "desconocida")
        print(f"OK  (FeatureCollection · geometría={geom_type})")

        print("  Consultando DescribeFeatureType...", end=" ", flush=True)
        xsd_text = fetch_describe_feature_type(workspace, typename)
        if xsd_text is None:
            print("FALLÓ")
            return False
        live_columns = parse_xsd_columns(xsd_text)
        if not live_columns:
            print("FALLÓ (no se encontraron columnas en el XSD)")
            return False
        source_ref = f"wfs:{typename}"

    # ── ArcGIS REST ──────────────────────────────────────────────────────────
    elif source_type == "arcgis_rest":
        service_url = source.get("service_url")
        layer_id_arcgis = source.get("layer_id")
        layer_name = source.get("layer_name", f"layer_{layer_id_arcgis}")
        if not service_url or layer_id_arcgis is None:
            print("  SALTAR: service_url o layer_id no definidos")
            return True

        print(f"  Service URL : {service_url}")
        print(f"  Layer       : [{layer_id_arcgis}] {layer_name}")

        print("  Validando GeoJSON...", end=" ", flush=True)
        sample = fetch_arcgis_sample(service_url, layer_id_arcgis)
        if sample is None:
            print("FALLÓ (no se pudo descargar muestra)")
            return False
        is_valid, reason = validate_geojson(sample)
        if not is_valid:
            print(f"FALLÓ — {reason}")
            return False
        geom_type = sample["features"][0].get("geometry", {}).get("type", "desconocida")
        print(f"OK  (FeatureCollection · geometría={geom_type})")

        print("  Consultando esquema ArcGIS...", end=" ", flush=True)
        live_columns = fetch_arcgis_schema(service_url, layer_id_arcgis)
        if not live_columns:
            print("FALLÓ")
            return False
        source_ref = f"arcgis:{service_url}/{layer_id_arcgis}"

    else:
        print(f"  SALTAR: tipo de fuente desconocido: {repr(source_type)}")
        return True

    live_hash = schema_hash(live_columns)
    print(f"OK  ({len(live_columns)} columnas, hash={live_hash})")

    # Capa pendiente de verificación: solo mostrar columnas descubiertas
    if status == "pending_verification":
        print("\n  Columnas descubiertas (no guardadas — capa sin curar):")
        for col, typ in sorted(live_columns.items()):
            print(f"    {col:<30} {typ}")
        print("\n  → Documenta estas columnas en el YAML y cambia schema_status a 'verified'")
        return True

    # Capa verificada: comparar con snapshot
    snapshot = load_snapshot(layer_id)

    if snapshot is None:
        print("  Sin snapshot previo — guardando estado actual.")
        save_snapshot(layer_id, source_ref, live_columns)
        _report_yaml_gaps(layer, live_columns)
        return True

    if snapshot["hash"] == live_hash:
        print(f"  Sin cambios desde {snapshot['timestamp'][:10]}")
        _report_yaml_gaps(layer, live_columns)
        return True

    # Hay diferencias
    diff = diff_schemas(snapshot["columns"], live_columns)
    print(f"\n  *** CAMBIOS DETECTADOS respecto a snapshot del {snapshot['timestamp'][:10]} ***")

    if diff["added"]:
        print(f"\n  Columnas NUEVAS ({len(diff['added'])}):")
        for col, typ in diff["added"].items():
            print(f"    + {col:<30} {typ}  ← documentar en el YAML")

    if diff["removed"]:
        print(f"\n  Columnas ELIMINADAS ({len(diff['removed'])}):")
        for col, typ in diff["removed"].items():
            print(f"    - {col:<30} {typ}  ← marcar como obsoleta en el YAML")

    if diff["type_changed"]:
        print(f"\n  Columnas con TIPO CAMBIADO ({len(diff['type_changed'])}):")
        for col, change in diff["type_changed"].items():
            print(f"    ~ {col:<30} {change['before']} → {change['after']}  ← revisar cast")

    if force_save:
        save_snapshot(layer_id, source_ref, live_columns)
        print("\n  Snapshot actualizado (--save-all).")
    else:
        print("\n  Snapshot NO actualizado. Revisa los cambios y corre con --save-all para aceptarlos.")

    _report_yaml_gaps(layer, live_columns)
    return True


def _report_yaml_gaps(layer: dict, live_columns: dict[str, str]) -> None:
    """Avisa si el YAML documenta columnas que ya no existen en el servidor."""
    documented = yaml_columns(layer)
    ghost_cols = documented - set(live_columns.keys())
    if ghost_cols:
        print(f"\n  AVISO: el YAML documenta columnas que NO están en el servidor:")
        for col in sorted(ghost_cols):
            print(f"    ? {col}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Detecta cambios de esquema en capas del geoportal")
    parser.add_argument("--layer", help="ID de una capa específica (ej: establecimientos_salud)")
    parser.add_argument("--discover", action="store_true", help="Solo procesar capas pending_verification")
    parser.add_argument("--save-all", action="store_true", dest="save_all", help="Sobreescribir todos los snapshots")
    args = parser.parse_args()

    layers = load_layer_yamls(args.layer)

    if not layers:
        print(f"No se encontraron capas{' con id=' + args.layer if args.layer else ''}.")
        sys.exit(1)

    if args.discover:
        layers = [l for l in layers if l.get("schema_status") == "pending_verification"]
        if not layers:
            print("No hay capas con schema_status: pending_verification.")
            sys.exit(0)

    errors = 0
    for layer in layers:
        ok = check_layer(layer, force_save=args.save_all)
        if not ok:
            errors += 1

    print(f"\n{'='*60}")
    print(f"Resultado: {len(layers) - errors}/{len(layers)} capas procesadas sin errores.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
