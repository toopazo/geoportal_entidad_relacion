# Estado del proyecto — notas de avance

> Archivo mantenido por Claude y el usuario como registro de progreso.
> Actualizar al inicio y al final de cada sesión de trabajo.

---

## Sesión 2026-05-11

### Lo que se construyó

**Catálogo de capas** (`catalog/layers/`)
- `establecimientos_salud.yaml` — completamente curada: 34 columnas documentadas con `human_label`, `description`, `is_fk`, `fk_target`, `known_values` y notas de calidad. `schema_status: verified`. Snapshot guardado en `catalog/snapshots/establecimientos_salud.json`.
- `dpa_2023.yaml` — stub: columnas esperadas pero no verificadas. `workspace: null` (pendiente descubrir). `schema_status: pending_verification`.
- `establecimientos_educacion.yaml` — stub: columnas esperadas. `workspace: null`. `schema_status: pending_verification`.

**Scripts** (`scripts/`)
- `build_er_model.py` — lee los YAMLs y genera `catalog/er_model.json` + `catalog/er_model.png` (Graphviz).
- `check_schema_drift.py` — llama a `DescribeFeatureType` WFS, compara contra snapshots locales, reporta columnas agregadas/eliminadas/con tipo cambiado.

**Infraestructura**
- `Dockerfile.tools` + `docker-compose.yml` con servicios `schema-check` y `build-er`. Sin automatización — todo se corre a mano.

**Diagrama ER**
- `catalog/er_model.png` — 3 nodos curados (1 verificado, 2 pendientes) + 4 nodos fantasma: `deis_rem`, `dim_servicio_salud`, `fonasa_beneficiarios`, `ine_proyecciones_poblacion`.

---

## Próximos pasos

- [ ] Descubrir `workspace` y `typename` de `dpa_2023` (geoportal ID: 36391): abrir el visualizador en geoportal.cl, cargar esa capa, inspeccionar el Network tab del browser para capturar la URL WFS → actualizar el YAML.
- [ ] Ídem para `establecimientos_educacion` (geoportal ID: 35408).
- [ ] Correr `docker compose run --rm schema-check --discover` para ver columnas reales de ambas capas.
- [ ] Documentar columnas en los YAMLs (curaduría manual).
- [ ] Regenerar PNG: `docker compose run --rm build-er`.
- [ ] Agregar capas pendientes del README: Jardines Integra, FONASA, INE proyecciones, DEIS REM.
- [ ] Empezar backend FastAPI (proxy WFS, endpoint de catálogo, descarga filtrada).
- [ ] Evolucionar el PNG estático a diagrama ER interactivo (D3 o similar).

---

## Decisiones de diseño tomadas

| Decisión | Motivo |
|---|---|
| Scope curado (8-15 capas), no exhaustivo | Calidad > cantidad |
| Sin GitHub Actions | El usuario controla cuándo correr cada proceso |
| YAML como fuente de verdad del catálogo | Editable a mano, versionable en git |
| `schema_status` en cada capa y columna | Distinguir lo verificado de lo esperado |
| Nodos fantasma en el ER | Mostrar honestamente qué falta curar |
| Stack: FastAPI + JS ligero | Sin frameworks pesados; primero funcional, luego bonito |
