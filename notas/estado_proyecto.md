# Log de sesiones

> Registro cronológico de conversaciones con Claude.
> Cada sesión agrega una entrada al inicio (más reciente primero).

---

## Sesión 2026-05-13

### Qué se discutió

**Conceptos GIS aclarados.** Se explicaron las diferencias entre: API/WFS/capa/recurso, GeoJSON vs tabla, geometrías Point/LineString/Polygon, vitrina/bodega viva/bodega muerta, WFS vs ArcGIS REST, y la relación entre `properties` (tabla) y `geometry` (dónde) en un GeoJSON.

**Fuentes de datos más allá del WFS.** El geoportal referencia capas que viven en ArcGIS Online (INE, GORE). Se implementó soporte para `arcgis_rest` como tipo de fuente, con descubrimiento automático de Feature Services via la API de ArcGIS. Caso real: Censo 2024 en `services5.arcgis.com`.

**Flujo de curado rediseñado.** El flujo anterior era manual y basado en Docker. El nuevo flujo usa `make` con venv permanente:
1. `make scrape URL=...` — crea el YAML desde la página del catálogo (auto-detecta WFS o ArcGIS)
2. `make load LAYER=...` — descarga 100 filas y carga en postgres
3. `make profile LAYER=...` — genera stubs de columnas con datos reales + descripciones LLM

**Descripciones automáticas de columnas.** Cada columna tiene tres campos de descripción: `human_description` (manual), `arcgis_description` (alias oficial de ArcGIS cuando aplica), `llm_description` (generado por DeepSeek usando el dominio oficial del organismo como contexto).

### Qué se construyó

- `scripts/scrape_catalog.py` — scraper completo del catálogo geoportal.cl: extrae 5 secciones verbatim, auto-detecta WFS o ArcGIS, descubre workspace/typename o service_url/layer_id, crea el YAML desde cero. Solo interrumpe al usuario si hay ambigüedad irresoluble.
- `scripts/load_sample.py` — refactorizado con dispatcher WFS/ArcGIS. Ambas fuentes producen GeoJSON estándar y se cargan igual en postgres.
- `scripts/profile_layer.py` — perfila columnas desde postgres: % nulos, distinct count, known_values (≤20 valores), ejemplos (>20), + llm_description via DeepSeek API + arcgis_description via aliases.
- `scripts/check_schema_drift.py` — actualizado para soportar `source: {type: wfs}` y `source: {type: arcgis_rest}`.
- `docker-compose.yml` — agrega servicio `postgres` (PostGIS 16) y `load-sample`.
- `Makefile` — punto de entrada unificado: `make scrape/load/profile/schema-check/build-er/db-up`.
- `GEOPORTAL.md` — documentación ampliada: GeoJSON, ArcGIS REST, flujo de curado, vitrina/bodega.
- `.env` (gitignoreado) — `DEEPSEEK_API_KEY` y `DATABASE_URL`.
- YAMLs renombrados con el nombre exacto del catálogo: `establecimientos_de_salud_de_chile_febrero_2026.yaml`, `resultados_censo_de_poblacion_y_vivienda_2024.yaml`.

### Decisiones tomadas

| Decisión | Razonamiento |
|---|---|
| Nombre YAML = slug del título del catálogo | Trazabilidad directa: el nombre del archivo refleja el recurso exacto |
| Tres campos de descripción por columna | Separar lo institucional (arcgis), lo generado (llm) y lo validado (human) |
| DeepSeek como LLM para descripciones | Costo y calidad suficiente para el caso de uso |
| ArcGIS REST soportado nativamente | El geoportal referencia fuentes externas; el sistema debe seguirlas |
| Curado siempre contra bodega viva (WFS/ArcGIS) | Nunca GeoJSON descargable — puede estar desincronizado |
| PostgreSQL + PostGIS en Docker | Backend definitivo para muestras + futura API FastAPI |

### Próximos pasos

- [ ] Curar `establecimientos_de_salud_de_chile_febrero_2026.yaml` — completar `human_description` columna a columna en DBeaver
- [ ] Cambiar `schema_status: verified` al terminar el curado
- [ ] Correr `make scrape` para `dpa_2023` y `establecimientos_educacion`
- [ ] Curar `resultados_censo_de_poblacion_y_vivienda_2024.yaml` (ArcGIS — probar flujo completo)
- [ ] Definir `relations:` y `use_cases:` en ambos YAMLs
- [ ] Empezar backend FastAPI

---

## Sesión 2026-05-11

### Qué se discutió

**Misión y visión del proyecto.** El repo era un placeholder vacío. Se definió que el objetivo es hacer accesible el valor práctico de los datos del Geoportal IDE Chile para usuarios no técnicos (funcionarios, municipios, consultores, periodistas de datos). No reemplazar al geoportal — agregar una capa de interpretación: documentar columnas en lenguaje humano, exponer relaciones entre datasets, permitir descarga contextualizada.

**Estrategia de curaduría y mantenimiento.** Se analizó el problema de que las capas se republican cada año con posibles cambios de esquema. Conclusión: separar detección automática (script que corre `DescribeFeatureType` y compara hashes) de revisión semántica (siempre manual). El script detecta *qué* cambió; el humano decide *qué significa*. Sin GitHub Actions — todo a demanda con `docker compose run`.

**Modelo ER.** El diagrama no se construye por separado: emerge directamente de los YAMLs. Cada YAML es un nodo; cada entrada en `relations:` es una arista. Un script genera el PNG y el JSON. El PNG estático es el punto de partida; el ER interactivo (clickeable, expandible) es la evolución futura.

### Qué se construyó

- `README.md` con misión, visión, diagrama ER embebido y guía de inicio rápido.
- `catalog/layers/establecimientos_salud.yaml` — 34 columnas documentadas, relaciones FK, use cases, notas de calidad de datos. Única capa completamente verificada.
- `catalog/layers/dpa_2023.yaml` y `establecimientos_educacion.yaml` — stubs con columnas esperadas, `workspace: null` pendiente de descubrir.
- `scripts/build_er_model.py` — genera `catalog/er_model.png` y `catalog/er_model.json` desde los YAMLs.
- `scripts/check_schema_drift.py` — detecta cambios de esquema WFS comparando contra snapshots.
- `Dockerfile.tools` + `docker-compose.yml` con servicios `build-er` y `schema-check`.
- `catalog/er_model.png` — diagrama generado y commiteado al repo.

### Decisiones tomadas

| Decisión | Razonamiento |
|---|---|
| Scope curado (8-15 capas), no exhaustivo | Calidad > cantidad. 10 capas bien documentadas > 500 mal explicadas |
| Sin GitHub Actions | El usuario controla cuándo correr cada proceso |
| YAML como fuente de verdad | Editable a mano, versionable en git, genera el ER automáticamente |
| `schema_status: verified / pending_verification` | Distinguir honestamente lo verificado de lo esperado |
| Nodos fantasma en el ER | Mostrar qué falta curar sin ocultar las relaciones pendientes |
| Stack: FastAPI + JS ligero | Sin frameworks pesados; funcional primero, bonito después |
| Log de sesiones en el repo | Memoria visible para el usuario y para Claude en futuras sesiones |

### Próximos pasos

- [ ] Descubrir `workspace`/`typename` de `dpa_2023` (geoportal ID: 36391) — inspeccionar Network tab al cargar la capa en geoportal.cl
- [ ] Ídem para `establecimientos_educacion` (ID: 35408)
- [ ] `docker compose run --rm schema-check --discover` para ver columnas reales de ambas capas
- [ ] Documentar columnas en los YAMLs (curaduría manual)
- [ ] Regenerar PNG con `docker compose run --rm build-er`
- [ ] Agregar capas pendientes: Jardines Integra, FONASA, INE proyecciones, DEIS REM
- [ ] Empezar backend FastAPI (proxy WFS, endpoint catálogo, descarga filtrada)
- [ ] Evolucionar el PNG a diagrama ER interactivo (D3 o similar)
