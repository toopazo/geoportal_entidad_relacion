# Log de sesiones

> Registro cronológico de conversaciones con Claude.
> Cada sesión agrega una entrada al inicio (más reciente primero).

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
