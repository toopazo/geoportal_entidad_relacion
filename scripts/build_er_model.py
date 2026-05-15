"""
build_er_model.py

Lee todos los YAMLs en catalog/layers/ y genera:
  - catalog/er_model.json   (grafo estructurado, para el futuro frontend)
  - catalog/er_model.png    (diagrama ER estático)

Los nodos son las capas documentadas en el catálogo.
Las aristas emergen de la sección `relations:` de cada YAML.
Las capas referenciadas pero no presentes en el catálogo aparecen como
nodos fantasma (borde punteado) para indicar que están pendientes de curar.

Uso:
  python scripts/build_er_model.py
  python scripts/build_er_model.py --format svg
  python scripts/build_er_model.py --out catalog/mi_diagrama
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from graphviz import Digraph

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
LAYERS_DIR = CATALOG_DIR / "layers"

# ── paleta visual ─────────────────────────────────────────────────────────────
# (header_bg, header_font, row_bg, border_color)
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


# ── carga de datos ────────────────────────────────────────────────────────────

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
            tgt_id = rel.get("target")
            join_on = rel.get("join_on", "")
            key = (src_id, tgt_id, join_on)
            if key in seen or not tgt_id:
                continue
            seen.add(key)
            edges.append({
                "from":        src_id,
                "to":          tgt_id,
                "join_on":     join_on,
                "join_type":   rel.get("join_type", "many_to_one"),
                "description": rel.get("description", ""),
            })

    return edges


def ghost_node_ids(layers: dict[str, dict], edges: list[dict]) -> set[str]:
    referenced = {e["to"] for e in edges}
    return referenced - set(layers.keys())


# ── construcción del JSON ─────────────────────────────────────────────────────

def build_json(layers: dict[str, dict], edges: list[dict], ghosts: set[str]) -> dict:
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
        "nodes": nodes,
        "edges": edges,
    }


# ── construcción del grafo Graphviz ──────────────────────────────────────────

def _node_label(layer_id: str, layer: dict) -> str:
    status = layer.get("schema_status", "pending_verification")
    style = STATUS_STYLE.get(status, STATUS_STYLE["pending_verification"])
    header_bg, header_font, row_bg, _ = style

    name = layer.get("name", layer_id)
    source_data = layer.get("source", {})
    source_type = source_data.get("type", "") if isinstance(source_data, dict) else ""
    source_label = {"wfs": "WFS", "arcgis_rest": "ArcGIS REST", "static": "Snapshot estática"}.get(source_type, source_type)
    geom = GEOMETRY_ICON.get(layer.get("geometry_type", ""), "")
    count = layer.get("feature_count")
    count_str = f"{count:,}".replace(",", ".") + " registros" if count else "N registros"
    status_label = {"verified": "✓ verificada", "pending_verification": "⚠ pendiente", "outdated": "✗ desactualizada"}.get(status, status)

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


def build_graph(layers: dict[str, dict], edges: list[dict], ghosts: set[str]) -> Digraph:
    dot = Digraph(
        name="Geoportal Chile — Modelo ER",
        comment="Generado por build_er_model.py",
    )
    dot.attr(
        rankdir="TB",
        splines="ortho",
        nodesep="0.6",
        ranksep="0.9",
        fontname="Helvetica",
        bgcolor="white",
        label=r"Geoportal Chile — Modelo Entidad-Relación\nGenerado automáticamente desde catalog/layers/",
        labelloc="t",
        fontsize="14",
    )
    dot.attr("node", shape="none", fontname="Helvetica", margin="0")
    dot.attr("edge", fontname="Helvetica", fontsize="9", color="#555555")

    # Nodos reales
    for layer_id, layer in layers.items():
        status = layer.get("schema_status", "pending_verification")
        _, _, _, border = STATUS_STYLE.get(status, STATUS_STYLE["pending_verification"])
        dot.node(
            layer_id,
            label=_node_label(layer_id, layer),
            color=border,
            style="",
        )

    # Nodos fantasma (referenciados pero sin YAML)
    for gid in sorted(ghosts):
        dot.node(
            gid,
            label=_ghost_label(gid),
            color=GHOST_STYLE[3],
            style="dashed",
        )

    # Aristas
    for edge in edges:
        arrow = ARROW_STYLE.get(edge["join_type"], ARROW_STYLE["many_to_one"])
        dot.edge(
            edge["from"],
            edge["to"],
            label=f'  {edge["join_on"]}  ',
            dir="both",
            **arrow,
        )

    return dot


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Genera el modelo ER del catálogo de capas")
    parser.add_argument("--format", default="png", choices=["png", "svg", "pdf"], help="Formato de salida (default: png)")
    parser.add_argument("--out", default=None, help="Ruta de salida sin extensión (default: catalog/er_model)")
    args = parser.parse_args()

    out_base = Path(args.out) if args.out else CATALOG_DIR / "er_model"

    print("Cargando capas del catálogo...")
    layers = load_layers()
    print(f"  {len(layers)} capas encontradas: {', '.join(layers.keys())}")

    edges = collect_edges(layers)
    ghosts = ghost_node_ids(layers, edges)
    print(f"  {len(edges)} relaciones, {len(ghosts)} nodos fantasma: {', '.join(sorted(ghosts)) or 'ninguno'}")

    # JSON
    json_path = out_base.with_suffix(".json")
    model = build_json(layers, edges, ghosts)
    json_path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON guardado: {json_path}")

    # Imagen
    dot = build_graph(layers, edges, ghosts)
    rendered = dot.render(str(out_base), format=args.format, cleanup=True)
    print(f"Imagen guardada: {rendered}")
    print("\nListo.")


if __name__ == "__main__":
    main()
