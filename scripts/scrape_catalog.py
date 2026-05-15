"""
scrape_catalog.py

Flujo completo de inicialización de una capa nueva:
  1. Descarga la página del catálogo de geoportal.cl y extrae metadatos verbatim
  2. Auto-detecta la fuente de datos (WFS o ArcGIS REST)
  3. Solo interrumpe si hay ambigüedad que no puede resolver solo
  4. Crea catalog/layers/{slug}.yaml listo para curado

Uso:
  python scripts/scrape_catalog.py --url URL
"""

import argparse
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR  = CATALOG_DIR / "layers"
GEOSERVER_BASE = "https://geoportal.cl/geoserver"
TIMEOUT_SECONDS = 30

SECTION_TITLES = {
    "identificacion": "Información sobre la identificación de los datos",
    "contacto":       "Información de contacto",
    "servicio_mapas": "Servicio de mapas",
    "descargas":      "Acceso a recursos descargables",
    "ambito_espacial":"Ámbito espacial",
}

ARCGIS_GEOM_MAP = {
    "esriGeometryPoint":      "point",
    "esriGeometryMultipoint": "multipoint",
    "esriGeometryPolyline":   "linestring",
    "esriGeometryPolygon":    "polygon",
}


# ── utilidades ────────────────────────────────────────────────────────────────

def title_to_slug(title: str) -> str:
    """'Establecimientos de salud Chile 2026' → 'establecimientos_de_salud_chile_2026'"""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_str  = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_str.lower()).strip("_")
    return slug


def tokenize(s: str) -> set[str]:
    """Tokeniza separando por no-alfanuméricos Y en transiciones letra↔número.
    'Censo2024_v2' → {'censo', '2024'}   (maneja slugs y acrónimos ArcGIS)
    """
    raw = re.findall(r"[a-z0-9]+", s.lower())
    expanded: list[str] = []
    for token in raw:
        expanded.extend(re.findall(r"[a-z]+|[0-9]+", token))
    return set(t for t in expanded if len(t) > 1)


def jaccard(a: str, b: str) -> float:
    wa, wb = tokenize(a), tokenize(b)
    return len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0


def get_json(url: str) -> dict | None:
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  WARN: {e}")
        return None


def prompt_choice(options: list, label_fn, prompt: str = "Elige una opción"):
    print()
    for i, opt in enumerate(options):
        print(f"    [{i}] {label_fn(opt)}")
    while True:
        try:
            idx = int(input(f"\n  {prompt} [0-{len(options)-1}]: ").strip())
            if 0 <= idx < len(options):
                return options[idx]
            print("  Fuera de rango.")
        except (ValueError, EOFError):
            print("  Valor inválido.")
        except KeyboardInterrupt:
            print("\n  Cancelado.")
            sys.exit(0)


def _pending_source(source_type: str, **kwargs) -> dict:
    return {
        "source": {
            "type": source_type,
            **kwargs,
            "_status": "pendiente — completar manualmente",
        },
        "geometry_type": None,
        "feature_count": None,
    }


# ── scraping HTML ─────────────────────────────────────────────────────────────

def fetch_html(url: str) -> str:
    print("  Descargando página del catálogo...", end=" ", flush=True)
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        print("OK")
        return r.text
    except Exception as e:
        print(f"FALLÓ: {e}")
        sys.exit(1)


def parse_section(soup: BeautifulSoup, title: str) -> dict:
    headers = soup.find_all("div", class_="rounded-t-md")
    header  = next((h for h in headers if title in h.get_text(strip=True)), None)
    if not header:
        return {}
    content_div = header.parent.find_next_sibling("div", class_="p-4")
    if not content_div:
        return {}
    pairs: dict = {}
    for label_div in content_div.find_all("div", class_=lambda c: c and "col-span-1" in c):
        label     = label_div.get_text(strip=True)
        value_div = label_div.find_next_sibling("div", class_=lambda c: c and "col-span-2" in c)
        if not value_div:
            continue
        links = value_div.find_all("a", href=True)
        value = [a["href"].strip() for a in links] if links else value_div.get_text(" ", strip=True)
        if label in pairs:
            existing = pairs[label] if isinstance(pairs[label], list) else [pairs[label]]
            pairs[label] = existing + (value if isinstance(value, list) else [value])
        else:
            pairs[label] = value
    return {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in pairs.items()}


def scrape_catalog(url: str) -> dict:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    raw  = {key: parse_section(soup, title) for key, title in SECTION_TITLES.items()}

    id_data  = raw["identificacion"]
    contacto = raw["contacto"]
    svc      = raw["servicio_mapas"]
    desc     = raw["descargas"]
    ambito   = raw["ambito_espacial"]

    nombres = desc.get("Nombre:", [])
    urls    = desc.get("URL:", [])
    if isinstance(nombres, str): nombres = [nombres]
    if isinstance(urls, str):    urls    = [urls]
    downloads = [{"nombre": n, "url": u} for n, u in zip(nombres, urls)]

    return {
        "url": url,
        "identificacion": {
            "titulo":     id_data.get("Título del recurso:", ""),
            "resumen":    id_data.get("Resumen del recurso:", ""),
            "fecha":      id_data.get("Fecha del Recurso (yyyy-mm-dd):", ""),
            "tipo_fecha": id_data.get("Tipo de fecha:", ""),
            "categorias": (
                [id_data["Categorías:"]]
                if isinstance(id_data.get("Categorías:"), str)
                else id_data.get("Categorías:", [])
            ),
        },
        "contacto": {
            "organizacion":          contacto.get("Nombre de la Organización:", ""),
            "responsable_metadatos": contacto.get("Responsable de los Metadatos:", ""),
            "email":                 contacto.get("E-Mail:", ""),
            "telefono":              contacto.get("Teléfono:", ""),
            "url":                   contacto.get("URL:", ""),
            "direccion":             contacto.get("Dirección:", ""),
            "ciudad":                contacto.get("Ciudad:", ""),
            "region":                contacto.get("Estado:", ""),
        },
        "servicio_mapas": {"wfs_capabilities": svc.get("URL:", "")},
        "descargas": downloads,
        "ambito_espacial": {
            "oeste": float(ambito.get("Coordenadas Oeste:", 0) or 0),
            "este":  float(ambito.get("Coordenadas Este:",  0) or 0),
            "sur":   float(ambito.get("Coordenadas Sur:",   0) or 0),
            "norte": float(ambito.get("Coordenadas Norte:", 0) or 0),
        },
    }


# ── detección WFS ─────────────────────────────────────────────────────────────

def detect_wfs(capabilities_url: str) -> dict:
    print("  Fuente detectada: WFS (GeoServer)")

    match = re.search(r"/geoserver/([^/?]+)/wfs", capabilities_url)
    if not match:
        print("  ERROR: no se pudo extraer workspace de la URL de capabilities")
        return _pending_source("wfs")
    workspace = match.group(1)
    print(f"  Workspace: {workspace}")

    print("  Descubriendo typename via GetCapabilities...", end=" ", flush=True)
    caps_url = f"{GEOSERVER_BASE}/{workspace}/wfs?service=WFS&version=1.0.0&request=GetCapabilities"
    try:
        r = requests.get(caps_url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        ns = {"wfs": "http://www.opengis.net/wfs"}
        typenames = [
            n.text.strip()
            for n in root.findall(".//wfs:FeatureType/wfs:Name", ns)
            if n.text
        ]
    except Exception as e:
        print(f"FALLÓ: {e}")
        return _pending_source("wfs", workspace=workspace)

    if not typenames:
        print("FALLÓ (sin typenames en la respuesta)")
        return _pending_source("wfs", workspace=workspace)

    if len(typenames) == 1:
        typename = typenames[0]
        print(f"OK → {typename}")
    else:
        print(f"OK ({len(typenames)} encontrados — selección requerida)")
        typename = prompt_choice(typenames, label_fn=str, prompt="¿Cuál typename curar?")

    geom_type, count = _wfs_sample_info(workspace, typename)
    return {
        "source": {"type": "wfs", "workspace": workspace, "typename": typename},
        "geometry_type": geom_type,
        "feature_count": count,
    }


def _wfs_sample_info(workspace: str, typename: str) -> tuple[str | None, int | None]:
    short = typename.split(":")[-1]
    url   = (
        f"{GEOSERVER_BASE}/{workspace}/wfs"
        f"?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName={short}&maxFeatures=1&outputFormat=application/json"
    )
    data = get_json(url)
    if not data:
        return None, None
    features  = data.get("features", [])
    geom_type = None
    if features:
        geom = features[0].get("geometry") or {}
        raw  = geom.get("type", "")
        geom_type = raw.lower() if raw else None
    return geom_type, data.get("totalFeatures")


# ── detección ArcGIS REST ─────────────────────────────────────────────────────

def detect_arcgis(descargas: list, title: str) -> dict:
    print("  Fuente detectada: ArcGIS REST (sin WFS en GeoServer)")

    arcgis_url = next(
        (d["url"] for d in descargas if "arcgis.com" in d.get("url", "")),
        None,
    )
    if not arcgis_url:
        print("  ERROR: no se encontró URL de ArcGIS en las descargas")
        return _pending_source("arcgis_rest")

    print(f"  Dashboard: {arcgis_url}")

    match = re.search(r"https://([^/]+)\.maps\.arcgis\.com/.+?/([a-f0-9]{32})", arcgis_url)
    if not match:
        print("  ERROR: no se pudo extraer org e item ID")
        return _pending_source("arcgis_rest")
    org     = match.group(1)
    item_id = match.group(2)

    print(f"  Consultando metadata del item {item_id}...", end=" ", flush=True)
    item_data = get_json(f"https://{org}.maps.arcgis.com/sharing/rest/content/items/{item_id}?f=json")
    if not item_data:
        print("FALLÓ")
        return _pending_source("arcgis_rest")
    owner = item_data.get("owner")
    print(f"OK  (owner: {owner})")

    # Extraer año del título para refinar la búsqueda (los nombres ArcGIS son acrónimos)
    year_match = re.search(r"\b(20\d{2})\b", title)
    year_kw    = year_match.group(1) if year_match else ""
    kw_query   = f"owner:{owner}+type:Feature+Service" + (f"+{year_kw}" if year_kw else "")

    print(f"  Buscando Feature Services de '{owner}'" + (f" (año {year_kw})" if year_kw else "") + "...", end=" ", flush=True)
    search_data = get_json(
        f"https://{org}.maps.arcgis.com/sharing/rest/search?q={kw_query}&f=json&num=20"
    )
    services = [r for r in (search_data or {}).get("results", []) if r.get("url")]

    if not services:
        # Fallback sin año
        print(f"0 — reintentando sin filtro de año...", end=" ", flush=True)
        search_data = get_json(
            f"https://{org}.maps.arcgis.com/sharing/rest/search"
            f"?q=owner:{owner}+type:Feature+Service&f=json&num=50"
        )
        if not search_data:
            print("FALLÓ")
            return _pending_source("arcgis_rest")
        services = [r for r in search_data.get("results", []) if r.get("url")]

    print(f"OK  ({len(services)} encontrados)")

    if not services:
        print("  ERROR: no se encontraron Feature Services para este owner")
        return _pending_source("arcgis_rest")

    scored = sorted(
        [(jaccard(title, s["title"]), i, s) for i, s in enumerate(services)],
        reverse=True,
    )
    best_score, _, best_service = scored[0]

    auto_select = len(scored) == 1 or (best_score > 0.25 and best_score > scored[1][0] * 1.4)
    if auto_select:
        print(f"  → Auto-seleccionado: '{best_service['title']}' (similitud {best_score:.0%})")
        chosen_service = best_service
    else:
        print("  Múltiples candidatos — selección requerida:")
        chosen_service = prompt_choice(
            [s for _, _, s in scored[:8]],
            label_fn=lambda s: f"{s['title']}",
            prompt="¿Qué Feature Service curar?",
        )

    service_url = chosen_service["url"]

    print(f"  Consultando capas...", end=" ", flush=True)
    service_data = get_json(f"{service_url}?f=json")
    if not service_data:
        print("FALLÓ")
        return _pending_source("arcgis_rest")
    layers = service_data.get("layers", [])
    print(f"OK  ({len(layers)} capas)")

    if not layers:
        print("  ERROR: el Feature Service no tiene capas")
        return _pending_source("arcgis_rest")

    if len(layers) == 1:
        chosen_layer = layers[0]
        print(f"  → Única capa: [{chosen_layer['id']}] {chosen_layer['name']}")
    else:
        print("  Múltiples capas — selección requerida:")
        chosen_layer = prompt_choice(
            layers,
            label_fn=lambda l: f"[{l['id']}] {l['name']}  ({l.get('geometryType', 'tabla')})",
            prompt="¿Qué capa curar?",
        )

    layer_id   = chosen_layer["id"]
    layer_name = chosen_layer["name"]
    raw_geom   = chosen_layer.get("geometryType", "")
    geom_type  = ARCGIS_GEOM_MAP.get(raw_geom, raw_geom.replace("esriGeometry", "").lower() or None)

    count_data = get_json(f"{service_url}/{layer_id}/query?where=1=1&returnCountOnly=true&f=json")
    count      = count_data.get("count") if count_data else None

    return {
        "source": {
            "type":        "arcgis_rest",
            "service_url": service_url,
            "layer_id":    layer_id,
            "layer_name":  layer_name,
        },
        "geometry_type": geom_type,
        "feature_count": count,
    }


# ── detección static (snapshot descargable) ───────────────────────────────────

_CONTENT_TYPE_TO_FORMAT = {
    "application/zip":                "shapefile",
    "application/x-zip-compressed":   "shapefile",
    "application/geo+json":           "geojson",
    "application/json":               "geojson",
    "text/csv":                        "csv",
    "application/x-rar":              "rar",
    "application/x-rar-compressed":   "rar",
    "application/vnd.rar":            "rar",
}

def _detect_format_from_headers(url: str) -> str | None:
    try:
        r = requests.head(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
        ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
        cd = r.headers.get("content-disposition", "").lower()
        fmt = _CONTENT_TYPE_TO_FORMAT.get(ct)
        if not fmt:
            for kw, f in _FORMAT_KEYWORDS.items():
                if kw in cd:
                    fmt = f
                    break
        if fmt:
            print(f"  → formato detectado por headers: {fmt}")
        return fmt
    except Exception:
        return None


_FORMAT_KEYWORDS = {
    "geojson": "geojson",
    "shapefile": "shapefile",
    "shp": "shapefile",
    "csv": "csv",
}
_FORMAT_PRIORITY = ["geojson", "shapefile", "csv"]


def detect_static(descargas: list) -> dict:
    print("  Fuente detectada: snapshot estática (sin WFS ni ArcGIS)")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    candidates = []
    for d in descargas:
        name_lower = d.get("nombre", "").lower()
        url = d.get("url", "")
        if not url:
            continue
        fmt = next((f for kw, f in _FORMAT_KEYWORDS.items() if kw in name_lower), None)
        if fmt:
            candidates.append((fmt, url))

    if not candidates:
        # Fallback: inspeccionar content-type / content-disposition via HEAD
        for d in descargas:
            url = d.get("url", "")
            if not url:
                continue
            fmt = _detect_format_from_headers(url)
            if fmt:
                candidates.append((fmt, url))
                break

    if not candidates:
        print("  WARN: no se pudo detectar el formato de ninguna descarga")
        return _pending_source("static")

    priority = {f: i for i, f in enumerate(_FORMAT_PRIORITY)}
    candidates.sort(key=lambda x: priority.get(x[0], 99))

    chosen_fmt, chosen_url = candidates[0]
    print(f"  → {chosen_fmt}: {chosen_url}")

    return {
        "source": {
            "type": "static",
            "download_url": chosen_url,
            "format": chosen_fmt,
            "downloaded_at": today,
        },
        "geometry_type": None,
        "feature_count": None,
    }


# ── dispatcher ────────────────────────────────────────────────────────────────

def detect_source(catalog_data: dict, title: str) -> dict:
    svc_url   = catalog_data.get("servicio_mapas", {}).get("wfs_capabilities", "")
    descargas = catalog_data.get("descargas", [])

    if "geoserver" in svc_url:
        return detect_wfs(svc_url)
    if any("arcgis.com" in d.get("url", "") for d in descargas):
        return detect_arcgis(descargas, title)

    return detect_static(descargas)


# ── generación del YAML ───────────────────────────────────────────────────────

def build_yaml(layer_id: str, catalog_data: dict, source_info: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = catalog_data["identificacion"]["titulo"]

    catalog_yaml = yaml.dump(
        {"catalog": catalog_data},
        allow_unicode=True, default_flow_style=False,
        sort_keys=False, indent=2, width=100,
    )
    source_yaml = yaml.dump(
        {"source": source_info["source"]},
        allow_unicode=True, default_flow_style=False,
        sort_keys=False, indent=2, width=100,
    )
    geom  = source_info.get("geometry_type") or "null"
    count = source_info.get("feature_count")
    count_str = str(count) if count is not None else "null"

    source_type = source_info["source"].get("type", "unknown")
    source_comment = (
        "Fuente de datos (snapshot estática — no es bodega viva)"
        if source_type == "static"
        else "Fuente de datos (bodega viva)"
    )

    sep = "-" * 78
    return (
        f"# {'=' * 78}\n"
        f"# Capa: {title}\n"
        f"# Generado automáticamente por scrape_catalog.py — {today}\n"
        f"# {'=' * 78}\n\n"
        f"id: {layer_id}\n"
        f"schema_status: pending_verification   # verified | pending_verification | outdated\n"
        f"last_reviewed: \"{today}\"\n"
        f"schema_hash: null         # rellenado por check_schema_drift.py en primer run\n\n"
        f"# {sep}\n"
        f"# Metadatos del catálogo (verbatim desde geoportal.cl)\n"
        f"# Fuente: {catalog_data['url']}\n"
        f"# {sep}\n"
        f"{catalog_yaml}\n"
        f"# {sep}\n"
        f"# {source_comment}\n"
        f"# {sep}\n"
        f"{source_yaml}"
        f"geometry_type: {geom}\n"
        f"feature_count: {count_str}\n\n"
        f"# {sep}\n"
        f"# Para qué sirve esta capa — completar manualmente\n"
        f"# {sep}\n"
        f"use_cases: []\n\n"
        f"# {sep}\n"
        f"# Relaciones con otras capas — completar manualmente\n"
        f"#\n"
        f"# Formato:\n"
        f"#   - target: <id del YAML de destino, ej: division_politica_administrativa_2023>\n"
        f"#     join_on: <columna_esta_tabla> = <columna_otra_tabla>\n"
        f"#     join_type: one_to_one | one_to_many | many_to_one\n"
        f"# {sep}\n"
        f"relations: []\n\n"
        f"# {sep}\n"
        f"# Columnas — completar durante el curado\n"
        f"# Leyenda:\n"
        f"#   truncated: nombre cortado por límite shapefile (10 chars)\n"
        f"#   tags (vocabulario controlado):\n"
        f"#     relevante  — columna con valor analítico, candidata a mostrar en frontend\n"
        f"#     fk         — clave foránea para JOIN con otra capa\n"
        f"#     geografica — coordenada o geometría espacial\n"
        f"# {sep}\n"
        f"columns: {{}}\n"
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Inicializa el YAML de una capa desde el catálogo de geoportal.cl"
    )
    parser.add_argument("--url", required=True, help="URL de la página del catálogo")
    args = parser.parse_args()

    print(f"\n{'=' * 60}")

    catalog_data = scrape_catalog(args.url)
    title = catalog_data["identificacion"]["titulo"]
    if not title:
        print("ERROR: no se pudo extraer el título del catálogo")
        sys.exit(1)

    layer_id = title_to_slug(title)
    print(f"  Título : {title}")
    print(f"  ID     : {layer_id}")

    source_info = detect_source(catalog_data, title)

    out_path = LAYERS_DIR / f"{layer_id}.yaml"
    if out_path.exists():
        print(f"\n  AVISO: {out_path.name} ya existe — se sobreescribe")

    out_path.write_text(build_yaml(layer_id, catalog_data, source_info), encoding="utf-8")

    print(f"\n  YAML creado: {out_path.name}")
    print(f"  Siguiente paso: make load LAYER={layer_id}")
    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    main()
