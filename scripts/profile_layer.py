"""
profile_layer.py

Perfila las columnas de una tabla cargada en postgres y genera el bloque
columns: en el YAML con tres campos de descripción:

  human_description  → vacío, lo completa el curador manualmente
  arcgis_description → alias oficial del Feature Service (solo capas ArcGIS)
  llm_description    → generado por Claude usando el dominio oficial del organismo

Requiere haber corrido: make load LAYER=<id>

Uso:
  python scripts/profile_layer.py --layer establecimientos_de_salud_de_chile_febrero_2026
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI
import psycopg2
import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR  = CATALOG_DIR / "layers"

DEFAULT_DATABASE_URL  = "postgresql://geoportal:geoportal@localhost:5432/geoportal"
LOW_CARDINALITY_LIMIT = 20
N_EXAMPLES            = 3
TIMEOUT               = 20
DOMAIN_MAX_CHARS      = 4000   # contexto máximo para el LLM

PG_TO_YAML_TYPE = {
    "integer":                      "int",
    "bigint":                       "int",
    "smallint":                     "int",
    "double precision":             "float",
    "real":                         "float",
    "numeric":                      "float",
    "text":                         "string",
    "character varying":            "string",
    "date":                         "date",
    "timestamp without time zone":  "datetime",
    "timestamp with time zone":     "datetime",
    "boolean":                      "bool",
    "geometry":                     "geometry",
    "USER-DEFINED":                 "geometry",
}


# ── postgres ──────────────────────────────────────────────────────────────────

def connect(db_url: str):
    try:
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"  ERROR postgres: {e}")
        sys.exit(1)


def get_columns(conn, table: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position
        """, (table,))
        return [{"name": row[0], "pg_type": row[1]} for row in cur.fetchall()]


def profile_column(conn, table: str, col: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                count(*) FILTER (WHERE "{col}" IS NULL)     AS n_nulls,
                count(*) FILTER (WHERE "{col}" IS NOT NULL) AS n_notnull,
                count(DISTINCT "{col}")                      AS n_distinct
            FROM "{table}"
        """)
        n_nulls, n_notnull, n_distinct = cur.fetchone()

    total    = n_nulls + n_notnull
    null_pct = round(n_nulls / total * 100, 1) if total > 0 else 0.0

    known_values = examples = None
    if n_notnull > 0:
        if n_distinct <= LOW_CARDINALITY_LIMIT:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT DISTINCT "{col}" FROM "{table}"
                    WHERE "{col}" IS NOT NULL ORDER BY "{col}"
                """)
                known_values = [r[0] for r in cur.fetchall()]
        else:
            with conn.cursor() as cur:
                cur.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {N_EXAMPLES}')
                examples = [r[0] for r in cur.fetchall()]

    return {
        "null_pct":       null_pct,
        "distinct_count": int(n_distinct),
        "known_values":   known_values,
        "examples":       examples,
    }


# ── ArcGIS aliases ────────────────────────────────────────────────────────────

def get_arcgis_aliases(source: dict) -> dict[str, str]:
    """Alias oficiales del Feature Service → human_label automático."""
    url = f"{source['service_url']}/{source['layer_id']}?f=json"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return {
            f["name"]: f["alias"]
            for f in data.get("fields", [])
            if f.get("alias") and f["alias"] != f["name"]
        }
    except Exception as e:
        print(f"  WARN aliases ArcGIS: {e}")
        return {}


# ── dominio oficial (fuente para LLM) ─────────────────────────────────────────

def fetch_domain_content(contact_url: str, catalog_resumen: str) -> str:
    """
    Busca contenido relevante dentro del dominio del organismo.
    Estrategia: página principal + links que parezcan diccionarios o manuales.
    """
    if not contact_url:
        return ""

    keywords = ["diccionario", "variable", "manual", "establecimientos", "datos", "estadística"]

    def fetch_text(url: str) -> str:
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)
        except Exception:
            return ""

    # Página principal
    main_text = fetch_text(contact_url)

    # Buscar links relevantes en la página principal
    try:
        r = requests.get(contact_url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        relevant_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if any(kw in a["href"].lower() or kw in a.get_text().lower() for kw in keywords)
            and a["href"].startswith("http")
        ][:3]
    except Exception:
        relevant_links = []

    extra_texts = [fetch_text(link) for link in relevant_links]
    combined = " ".join([main_text] + extra_texts)

    # Truncar para no exceder el contexto del LLM
    return combined[:DOMAIN_MAX_CHARS]


# ── LLM descriptions ──────────────────────────────────────────────────────────

def get_llm_descriptions(
    data_cols: list[dict],
    profiles: dict,
    catalog_data: dict,
    domain_content: str,
) -> dict[str, str]:
    """Llama a Claude API para generar una descripción por columna."""

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        print("  WARN: DEEPSEEK_API_KEY no configurada — llm_description quedará vacío")
        return {}

    resumen = catalog_data.get("identificacion", {}).get("resumen", "")
    titulo  = catalog_data.get("identificacion", {}).get("titulo", "")

    cols_info = []
    for col in data_cols:
        name = col["name"]
        p    = profiles.get(name, {})
        cols_info.append({
            "nombre":        name,
            "tipo":          PG_TO_YAML_TYPE.get(col["pg_type"], "string"),
            "known_values":  p.get("known_values"),
            "ejemplos":      p.get("examples"),
        })

    context_block = f"""--- FUENTE OFICIAL ({catalog_data.get('contacto', {}).get('url', '')}) ---
{domain_content or '(sin contenido recuperable)'}
--- FIN FUENTE OFICIAL ---""" if domain_content else ""

    prompt = f"""Eres un experto en datos públicos chilenos. Debes documentar las columnas de la capa de datos "{titulo}".

Descripción oficial de la capa:
{resumen}

{context_block}

INSTRUCCIÓN: Para cada columna escribe UNA oración descriptiva en español.
- Usa PRINCIPALMENTE la fuente oficial si contiene información relevante.
- Si la fuente no cubre la columna, usa tu conocimiento del dominio ({catalog_data.get('contacto', {}).get('organizacion', '')}).
- Sé preciso y directo. No repitas el nombre de la columna en la descripción.
- Para columnas con known_values, menciona qué representa cada categoría si lo sabes.

Columnas:
{json.dumps(cols_info, ensure_ascii=False, indent=2)}

Responde ÚNICAMENTE con un objeto JSON: {{"nombre_columna": "descripción", ...}}
Sin texto adicional antes ni después del JSON."""

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        message = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.choices[0].message.content.strip()
        # Extraer JSON aunque haya texto extra
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"  WARN LLM: {e}")

    return {}


# ── generación YAML ───────────────────────────────────────────────────────────

def _yaml_str(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if any(c in s for c in (':', '#', '[', ']', '{', '}', ',', '&', '*', '?', '|',
                             '-', '<', '>', '=', '!', '%', '@', '`', "'", '"', '\n')) \
            or s in ("true", "false", "null", ""):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def build_columns_yaml(
    columns: list[dict],
    profiles: dict,
    arcgis_aliases: dict,
    llm_descriptions: dict,
) -> str:
    lines = ["columns:"]

    for col in columns:
        name      = col["name"]
        pg_type   = col["pg_type"]
        yaml_type = PG_TO_YAML_TYPE.get(pg_type, "string")
        profile   = profiles.get(name, {})

        lines.append(f"\n  {name}:")
        lines.append(f"    type: {yaml_type}")

        if yaml_type == "geometry":
            lines.append(f'    notes: "Columna espacial PostGIS. Usar ST_AsText(geom) para ver coordenadas."')
            lines.append(f"    human_description: \"\"")
            lines.append(f"    arcgis_description: null")
            lines.append(f"    llm_description: null")
            continue

        null_pct       = profile.get("null_pct", 0.0)
        distinct_count = profile.get("distinct_count", 0)
        known_values   = profile.get("known_values")
        examples       = profile.get("examples")

        lines.append(f"    null_pct: {null_pct}")
        lines.append(f"    distinct_count: {distinct_count}")

        if known_values is not None:
            lines.append(f"    known_values:")
            for v in known_values:
                lines.append(f"      - {_yaml_str(v)}")
        elif examples is not None:
            lines.append(f"    examples:")
            for v in examples:
                lines.append(f"      - {_yaml_str(v)}")

        lines.append(f"    human_description: \"\"")

        alias = arcgis_aliases.get(name)
        lines.append(f"    arcgis_description: {_yaml_str(alias) if alias else 'null'}")

        llm_desc = llm_descriptions.get(name, "")
        lines.append(f"    llm_description: {_yaml_str(llm_desc) if llm_desc else 'null'}")

        lines.append(f"    is_pk: false")
        lines.append(f"    is_fk: false")

    return "\n".join(lines) + "\n"


# ── YAML update ───────────────────────────────────────────────────────────────

def update_yaml(layer_id: str, columns_yaml: str) -> None:
    path = LAYERS_DIR / f"{layer_id}.yaml"
    if not path.exists():
        print(f"  ERROR: no se encontró {path}")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")

    pattern  = r"(# -{10,}\n# Columnas.*?# -{10,}\n)columns:.*"
    new_text = re.sub(pattern, r"\g<1>" + columns_yaml, text, flags=re.DOTALL)

    if new_text == text:
        new_text = re.sub(r"^columns:.*$", columns_yaml.rstrip(), text, flags=re.MULTILINE | re.DOTALL)

    path.write_text(new_text, encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Perfila columnas y actualiza el YAML con tres descripciones")
    parser.add_argument("--layer", required=True, help="ID de la capa")
    args   = parser.parse_args()
    layer  = args.layer

    db_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    print(f"\n{'='*60}")
    print(f"Perfilando: {layer}")

    # Leer YAML para obtener source y catalog
    yaml_path = LAYERS_DIR / f"{layer}.yaml"
    if not yaml_path.exists():
        print(f"  ERROR: {yaml_path} no encontrado")
        sys.exit(1)
    layer_yaml   = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    source       = layer_yaml.get("source", {})
    catalog_data = layer_yaml.get("catalog", {})
    contact_url  = catalog_data.get("contacto", {}).get("url", "")

    # 1. Profiling desde postgres
    print("  Conectando a postgres...", end=" ", flush=True)
    conn    = connect(db_url)
    print("OK")
    columns = get_columns(conn, layer)
    if not columns:
        print(f"  ERROR: tabla '{layer}' no existe. Corre make load primero.")
        sys.exit(1)

    geo_cols  = [c for c in columns if PG_TO_YAML_TYPE.get(c["pg_type"]) == "geometry" or c["pg_type"] == "USER-DEFINED"]
    data_cols = [c for c in columns if c not in geo_cols]

    print(f"  Columnas: {len(data_cols)} datos + {len(geo_cols)} espaciales")
    print(f"  Perfilando", end=" ", flush=True)
    profiles = {}
    for i, col in enumerate(data_cols):
        profiles[col["name"]] = profile_column(conn, layer, col["name"])
        if (i + 1) % 10 == 0:
            print(f"{i+1}", end=" ", flush=True)
    print("OK")
    conn.close()

    # 2. ArcGIS aliases (si aplica)
    arcgis_aliases = {}
    if source.get("type") == "arcgis_rest":
        print("  Obteniendo aliases ArcGIS...", end=" ", flush=True)
        arcgis_aliases = get_arcgis_aliases(source)
        print(f"OK ({len(arcgis_aliases)} aliases)")
    else:
        print("  Fuente WFS — arcgis_description: null para todas las columnas")

    # 3. Contenido del dominio oficial
    print(f"  Buscando contenido en {contact_url or '(sin URL de contacto)'}...", end=" ", flush=True)
    domain_content = fetch_domain_content(contact_url, catalog_data.get("identificacion", {}).get("resumen", ""))
    print(f"OK ({len(domain_content)} chars)")

    # 4. Descripciones LLM
    print("  Generando llm_description con Claude API...", end=" ", flush=True)
    llm_descriptions = get_llm_descriptions(data_cols, profiles, catalog_data, domain_content)
    print(f"OK ({len(llm_descriptions)} descripciones)")

    # 5. Generar y escribir YAML
    all_columns  = data_cols + geo_cols
    columns_yaml = build_columns_yaml(all_columns, profiles, arcgis_aliases, llm_descriptions)
    update_yaml(layer, columns_yaml)
    print(f"  YAML actualizado: {yaml_path.name}")

    # Resumen
    low_card = sum(1 for p in profiles.values() if p.get("known_values") is not None)
    nulls    = sum(1 for p in profiles.values() if p.get("null_pct", 0) > 0)
    print(f"\n  Resumen:")
    print(f"    {low_card} columnas categóricas con known_values")
    print(f"    {nulls} columnas con nulos")
    print(f"    {len(arcgis_aliases)} aliases ArcGIS aplicados")
    print(f"    {len(llm_descriptions)} descripciones LLM generadas")
    print(f"\n  Siguiente: abrir el YAML y completar human_description")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
