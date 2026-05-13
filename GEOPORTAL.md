# GEOPORTAL Chile — Guía técnica para agentes IA y análisis de datos

> Documento vivo. Actualizar con cada nueva sesión de exploración.
> Propósito: proveer contexto suficiente para que un agente IA pueda razonar sobre el ecosistema del geoportal sin necesidad de redescubrir lo ya aprendido.

---

## Índice

1. [Visión de alto nivel: ¿Qué es un GIS y qué es el geoportal?](#1-visión-de-alto-nivel-qué-es-un-gis-y-qué-es-el-geoportal)
2. [Stack tecnológico del geoportal](#2-stack-tecnológico-del-geoportal)
3. [Operaciones WFS fundamentales](#3-operaciones-wfs-fundamentales-con-ejemplos-reales)
4. [ArcGIS REST: fuente de datos alternativa](#4-arcgis-rest-fuente-de-datos-alternativa)
5. [Estructura de la capa principal: Establecimientos de Salud](#5-estructura-de-la-capa-principal-establecimientos-de-salud)
6. [El CUT: la clave foránea universal del Estado de Chile](#6-el-cut-la-clave-foránea-universal-del-estado-de-chile)
7. [Mapa de relaciones: tablas enlazables](#7-mapa-de-relaciones-tablas-enlazables)
8. [Datasets enlazables: catálogo de fuentes](#8-datasets-enlazables-catálogo-de-fuentes)
9. [Problemas prácticos conocidos](#9-problemas-prácticos-conocidos)
10. [Esquema de tablas para el ejercicio de alumnos](#10-esquema-de-tablas-para-el-ejercicio-de-alumnos)
11. [Flujo de curado de una capa](#11-flujo-de-curado-de-una-capa)
12. [Checklist para sesión de trabajo](#12-checklist-para-sesión-de-trabajo-con-el-geoportal)
13. [URLs de referencia](#13-urls-de-referencia)

---

## 1. Visión de alto nivel: ¿Qué es un GIS y qué es el geoportal?

### GIS (Geographic Information System)
Un GIS es un sistema que permite **almacenar, analizar y visualizar datos que tienen una componente espacial** — es decir, datos asociados a una ubicación en la Tierra.

La diferencia clave con una base de datos relacional convencional es que cada registro tiene una **geometría** (punto, línea o polígono) además de sus atributos. Eso permite:
- Hacer consultas espaciales: "dame todos los hospitales dentro de 10 km de esta ciudad"
- Producir mapas temáticos (coropléticos, de calor, etc.)
- Calcular distancias, áreas, intersecciones entre capas

### ¿Qué es una "capa" (layer)?
En terminología GIS, una **capa** es una tabla de datos geoespaciales que representa una sola categoría de fenómeno:
- Una capa de **puntos** → establecimientos de salud (cada punto = un establecimiento)
- Una capa de **polígonos** → comunas de Chile (cada polígono = el territorio de una comuna)
- Una capa de **líneas** → red vial (cada línea = un tramo de carretera)

Las capas son el equivalente GIS de las tablas en una base de datos relacional. Se pueden **unir (JOIN)** entre sí, tanto por atributos comunes (claves foráneas) como por relaciones espaciales (intersección, distancia, etc.).

### El geoportal como infraestructura
`geoportal.cl` es la **Infraestructura de Datos Espaciales (IDE)** oficial del Estado de Chile. Funciona como un repositorio centralizado de capas GIS publicadas por distintos organismos del Estado (MINSAL, INE, MINEDUC, BCN, SUBDERE, etc.).

---

## 2. Stack tecnológico del geoportal

### GeoServer
El backend del geoportal es **GeoServer**, un servidor open-source Java que publica capas GIS a través de estándares OGC. La URL base es:

```
https://geoportal.cl/geoserver/
```

Cada dataset vive dentro de un **workspace** (espacio de trabajo), que actúa como un namespace/esquema. El patrón de URL es:

```
https://geoportal.cl/geoserver/{workspace}/{servicio}?{parámetros}
```

Ejemplo real:
```
https://geoportal.cl/geoserver/EstablecimientosdesaluddeChile2025/wfs?...
```

### Protocolos OGC (Open Geospatial Consortium)
GeoServer implementa varios estándares:

| Protocolo | Para qué sirve | URL típica |
|-----------|---------------|-----------|
| **WFS** (Web Feature Service) | Descarga de datos vectoriales (GeoJSON, XML) | `.../{workspace}/wfs?service=WFS&...` |
| **WMS** (Web Map Service) | Obtener imágenes/tiles del mapa (PNG, JPEG) | `.../{workspace}/wms?service=WMS&...` |
| **WCS** (Web Coverage Service) | Datos raster (grillas, DEM, imágenes satelitales) | `.../{workspace}/wcs?service=WCS&...` |

### Para análisis de datos: siempre WFS
WFS devuelve los **datos en bruto** en formato GeoJSON o XML. Es el equivalente a un `SELECT * FROM tabla`. Use WFS cuando quiera hacer análisis, limpiar datos, o construir un tablero.

### Estructura del GeoJSON: qué es tabla y qué no

GeoJSON (RFC 7946) es el formato estándar que devuelve el WFS. Cada registro tiene dos partes:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [-67.60039, -54.93521]
  },
  "properties": {
    "nombre": "Hospital Cristina Calderón",
    "tipo": "Hospital",
    "cut_comuna": "12201"
  }
}
```

| Parte | Qué es | Analizable como tabla |
|---|---|---|
| `properties` | Atributos del objeto — columnas, valores | **Sí** — es la tabla |
| `geometry` | Ubicación y forma geográfica | No directamente |

**Tipos de geometría:**
```
Point       → [longitud, latitud]                   ← un establecimiento, un hito
LineString  → [[lon1,lat1], [lon2,lat2], ...]       ← una calle, un río
Polygon     → [[[lon1,lat1], [lon2,lat2], ...]]     ← una comuna, un predio
```

Regla: el orden de coordenadas es siempre **longitud primero, latitud segundo** — al revés de Google Maps.

La relación entre `properties` y `geometry` es 1 a 1: cada fila de la tabla tiene exactamente una geometría. `properties` describe el **qué**, `geometry` describe el **dónde**.

---

### Anatomía de una URL WFS

```
https://geoportal.cl
    /geoserver/                                ← el servidor GeoServer
    EstablecimientosdesaluddeChile2025/        ← workspace (como un schema en postgres)
    wfs                                        ← declara que usamos el protocolo WFS
    ?service=WFS                               ← redundante, confirma el protocolo
    &version=1.0.0                             ← versión del estándar OGC
    &request=GetFeature                        ← la operación (el "verbo")
    &typeName=establecimientos_de_..._2025     ← la capa (el "recurso" / la tabla)
    &maxFeatures=100                           ← LIMIT 100
    &outputFormat=application/json             ← quiero GeoJSON, no XML
```

### Vitrina, bodega viva y bodega muerta

Cada capa en el geoportal puede tener hasta tres representaciones:

```
VITRINA (HTML para humanos)
https://geoportal.cl/geoportal/catalog/{id}/...
  │
  ├── BODEGA VIVA (WFS — API, datos consultables en tiempo real)
  │   https://geoportal.cl/geoserver/{workspace}/wfs?request=GetCapabilities
  │   → Podés filtrar, pedir columnas específicas, paginar
  │
  └── BODEGA MUERTA (GeoJSON descargable — snapshot fijo)
      https://geoportal.cl/{organismo}/catalog/download/{uuid}
      → Archivo guardado en una fecha fija, sin filtros, descarga total
```

| | WFS (bodega viva) | GeoJSON descargable (bodega muerta) |
|---|---|---|
| Datos | En tiempo real desde GeoServer | Snapshot de una fecha fija |
| Filtros | Sí (`cql_filter`, `maxFeatures`) | No, descargás todo |
| Para qué | Análisis programático, dashboards | Backup, trabajo offline |

---

## 3. Operaciones WFS fundamentales (con ejemplos reales)

### 3.1 DescribeFeatureType — "¿cuáles son las columnas de esta capa?"
```
GET https://geoportal.cl/geoserver/EstablecimientosdesaluddeChile2025/wfs
  ?service=WFS
  &version=2.0.0
  &request=DescribeFeatureType
  &typeName=EstablecimientosdesaluddeChile2025:establecimientos_de_salud_diciembre_2025
```
Responde con XML Schema (XSD). Hay que parsear los `<element name="...">` para obtener los nombres de columnas.

### 3.2 GetFeature — "dame los datos"
```
GET https://geoportal.cl/geoserver/EstablecimientosdesaluddeChile2025/wfs
  ?service=WFS
  &version=2.0.0
  &request=GetFeature
  &typeName=EstablecimientosdesaluddeChile2025:establecimientos_de_salud_diciembre_2025
  &outputFormat=application/json
  &count=100                         # límite de registros
  &cql_filter=nom_comuna='SANTIAGO'  # filtro CQL (equivalente a WHERE)
  &propertyName=nombre,tipo,cut_comuna  # columnas específicas (equivalente a SELECT)
```

### 3.3 GetCapabilities — "¿qué capas tiene este workspace?"
```
GET https://geoportal.cl/geoserver/{workspace}/wfs
  ?service=WFS
  &version=2.0.0
  &request=GetCapabilities
```
**Problema conocido**: el endpoint global `https://geoportal.cl/geoserver/wfs?...GetCapabilities` retorna error `NoApplicableCode: No workspace specified`. Se debe especificar el workspace en la URL.

### 3.4 CQL Filter — sintaxis de filtros
CQL (Common Query Language) es el WHERE de WFS:

```
# Igualdad
cql_filter=nom_region='REGIÓN DE LOS LAGOS'

# Múltiples valores (IN)
cql_filter=tipo IN ('Hospital','Clínica')

# AND/OR
cql_filter=cod_reg=5 AND urgencia='SI'

# Operadores espaciales
cql_filter=DWITHIN(geom, POINT(-70.6 -33.4), 50000, meters)
```

---

## 4. ArcGIS REST: fuente de datos alternativa

El geoportal.cl a veces referencia capas que no viven en GeoServer sino en ArcGIS Online (ESRI). Estas capas tienen su propio protocolo de acceso: **ArcGIS Feature Service REST API**.

### Por qué existe

Algunos organismos (INE, GORE, municipios) publican sus datos en ArcGIS Online en vez de en GeoServer. El geoportal los lista en su catálogo, pero la bodega viva está en otro servidor.

Ejemplo: el Censo 2024 del INE está en:
```
https://services5.arcgis.com/hUyD8u3TeZLKPe4T/arcgis/rest/services/Censo2024_v2/FeatureServer
```

### Cómo descubrir el Feature Service desde el catálogo

Cuando la página del catálogo tiene `servicio_mapas.wfs_capabilities` vacío pero sí tiene un dashboard ArcGIS en `descargas`, buscar Feature Services del organismo via ArcGIS REST:

```bash
# 1. Obtener el orgId del dashboard (aparece en la URL de la respuesta del item)
curl "https://ine-chile.maps.arcgis.com/sharing/rest/content/items/{dashboard_id}?f=json"
# → buscar "orgId"

# 2. Buscar Feature Services del owner
curl "https://{org}.maps.arcgis.com/sharing/rest/search?q=owner:{owner}+type:Feature+Service+{keyword}&f=json&num=10"

# 3. Ver las capas dentro de un Feature Service
curl "{service_url}?f=json"
```

### Operaciones equivalentes a WFS

| WFS | ArcGIS REST | Para qué |
|---|---|---|
| `GetCapabilities` | `{service_url}?f=json` | Listar capas disponibles |
| `DescribeFeatureType` | `{service_url}/{layer_id}?f=json` | Ver columnas y tipos |
| `GetFeature` | `{service_url}/{layer_id}/query?where=1=1&outFields=*&f=geojson` | Descargar datos |

### Formato de respuesta

ArcGIS soporta `f=geojson` y devuelve GeoJSON estándar (RFC 7946), idéntico al WFS. El pipeline de validación e inserción en postgres es el mismo.

```bash
# Descargar 100 comunas del Censo 2024
curl "https://services5.arcgis.com/hUyD8u3TeZLKPe4T/arcgis/rest/services/Censo2024_v2/FeatureServer/11/query
  ?where=1=1
  &outFields=CUT,COMUNA,n_per,n_hog
  &resultRecordCount=100
  &returnGeometry=true
  &f=geojson"
```

### Tipos de campo ArcGIS → postgres

| Tipo ArcGIS | Postgres |
|---|---|
| `esriFieldTypeInteger` / `esriFieldTypeOID` | `INTEGER` |
| `esriFieldTypeDouble` | `DOUBLE PRECISION` |
| `esriFieldTypeString` | `TEXT` |
| `esriFieldTypeDate` | `BIGINT` (timestamp en ms — convertir manualmente) |
| `esriGeometryPolygon` | `geometry(MultiPolygon, 4326)` |
| `esriGeometryPoint` | `geometry(Point, 4326)` |

### El bloque `source` en el YAML

Para WFS:
```yaml
source:
  type: wfs
  workspace: EstablecimientosdesaluddeChile2026
  typename: "EstablecimientosdesaluddeChile2026:establecimientos_de_salud_febrero_2026"
```

Para ArcGIS REST:
```yaml
source:
  type: arcgis_rest
  service_url: "https://services5.arcgis.com/hUyD8u3TeZLKPe4T/arcgis/rest/services/Censo2024_v2/FeatureServer"
  layer_id: 11
  layer_name: "Comunal_CPV24"
```

En ambos casos el flujo es idéntico: `make load LAYER={id}` descarga 100 filas y las carga en postgres.

### Caso real: Censo 2024 (INE)

- **Catálogo geoportal:** `https://geoportal.cl/geoportal/catalog/36568/`
- **Feature Service:** `https://services5.arcgis.com/hUyD8u3TeZLKPe4T/arcgis/rest/services/Censo2024_v2/FeatureServer`
- **Capa usada:** `[11] Comunal_CPV24` — 346 comunas, ~215 columnas (población, vivienda, empleo, educación)
- **Clave de unión:** `CUT` (idéntico al estándar SUBDERE)

---

## 5. Estructura de la capa principal: Establecimientos de Salud

### Workspace
```
EstablecimientosdesaluddeChile2025
```

### TypeName
```
EstablecimientosdesaluddeChile2025:establecimientos_de_salud_diciembre_2025
```

### Columnas completas (verificadas vía DescribeFeatureType)

| Columna | Tipo | Descripción | ¿Clave foránea? |
|---------|------|-------------|----------------|
| `id_orig` | int | ID secuencial interno | — |
| `cod_ant` | str | Código DEIS antiguo (formato "XX-XXX") | — |
| `cod_vig` | int | **Código DEIS vigente** (e.g., 126704) | **FK → DEIS REM** |
| `cod_m_ant` | str | Código MINSAL antiguo | — |
| `cod_m_nuev` | int | Código MINSAL nuevo | — |
| `cod_reg` | int | Código de región (1–16) | FK → DPA regiones |
| `nom_reg` | str | Nombre de región | — |
| `cod_dep` | int | **Código del Servicio de Salud/SEREMI** | **FK → Servicios de Salud** |
| `dependenc` | str | Nombre del Servicio de Salud (e.g., "Servicio de Salud Magallanes") | — |
| `pertenenci` | str | Pertenencia al SNSS | — |
| `tipo` | str | Tipo: Hospital, CESFAM, Posta Rural, etc. | — |
| `ambito` | str | Ámbito del establecimiento | — |
| `nombre` | str | Nombre completo | — |
| `certifi` | str | Certificación | — |
| `dep_adm` | str | Dependencia administrativa (Servicio de Salud, Municipal, etc.) | — |
| `nivel` | str | Nivel: Primario, Secundario, Terciario | — |
| `cod_com` | int | Código de comuna (5 dígitos) | FK → DPA comunas |
| `nom_com` | str | Nombre de comuna (mayúsculas) | — |
| `via` | str | Tipo de vía (Calle, Avenida, etc.) | — |
| `numero` | str | Número de dirección | — |
| `direccion` | str | Nombre de la calle | — |
| `fono` | float | Teléfono | — |
| `f_inicio` | date | Fecha de inicio de operaciones | — |
| `urgencia` | str | Tiene urgencia: SI/NO | — |
| `tipo_urge` | str | Tipo de urgencia (UEH, SU, etc.) | — |
| `clas_sapu` | str | Clasificación SAPU | — |
| `latitud` | float | Latitud WGS84 | — |
| `longitud` | float | Longitud WGS84 | — |
| `prestador` | str | Público/Privado/Municipal | — |
| `estado` | str | Estado operacional | — |
| `complejida` | str | Complejidad: Alta, Media, Baja | — |
| `tipo_aten` | str | Tipo de atención: Abierta/Cerrada | — |
| `cut_comuna` | int | **CUT de comuna** (estándar nacional, 5 dígitos) | **FK → INE, DPA, FONASA** |
| `cut_region` | int | **CUT de región** (1–16) | **FK → INE, DPA** |
| `cut_provin` | int | **CUT de provincia** (3 dígitos) | **FK → DPA** |
| `nom_region` | str | Nombre de región (mayúsculas) | — |
| `nom_provin` | str | Nombre de provincia (mayúsculas) | — |
| `nom_comuna` | str | Nombre de comuna (mayúsculas) | — |
| `ir_google` | str | URL Google Maps | — |
| `simbologia` | str | Categoría para simbología del mapa | — |
| `geom` | geometry | Punto geográfico (EPSG:4326) | — |

### Ejemplo de registro real
```json
{
  "cod_vig": 126704,
  "cod_dep": 26,
  "dependenc": "Servicio de Salud Magallanes",
  "tipo": "Hospital",
  "nombre": "Hospital Comunitario Cristina Calderón de Puerto Williams",
  "cut_region": "12",
  "cut_provin": "122",
  "cut_comuna": "12201",
  "nom_region": "REGIÓN DE MAGALLANES Y DE LA ANTÁRTICA CHILENA",
  "nom_provin": "ANTÁRTICA CHILENA",
  "nom_comuna": "CABO DE HORNOS",
  "latitud": -54.93521,
  "longitud": -67.60039,
  "prestador": "Público",
  "tipo_aten": "Atención Cerrada-Hospitalaria",
  "complejida": "Baja Complejidad"
}
```

---

## 6. El CUT: la clave foránea universal del Estado de Chile

El **CUT (Código Único Territorial)** es el estándar nacional para identificar unidades político-administrativas. Es mantenido por SUBDERE (Subsecretaría de Desarrollo Regional).

### Estructura jerárquica
```
Región     → 2 dígitos     → ej: 13 (Región Metropolitana)
Provincia  → 3 dígitos     → ej: 131 (Provincia de Santiago)
Comuna     → 5 dígitos     → ej: 13101 (Santiago)
```

### Por qué es tan importante
El CUT aparece en **todos** los datasets del Estado de Chile:
- INE (censos, proyecciones de población)
- SUBDERE (presupuestos, indicadores comunales)
- FONASA (beneficiarios por comuna)
- MINEDUC (establecimientos escolares)
- MINSAL/DEIS (salud)
- SII (actividad económica)

Es la llave que permite hacer JOIN entre cualquier dataset del Estado.

---

## 7. Mapa de relaciones: tablas enlazables

```
┌─────────────────────────────────────────────────────────────────────┐
│              establecimientos_de_salud_diciembre_2025               │
│                         (5.181 registros)                           │
│                                                                     │
│  cod_vig ──────────────────────────────────────┐                    │
│  cut_region ─────────────┐  ┌──────────────────│────────────┐       │
│  cut_provin ─────────────│──┤                  │            │       │
│  cut_comuna ─────────────│──┘                  │            │       │
│  cod_dep ────────────────│──────────────────┐  │            │       │
└─────────────────────────│──────────────────│──│────────────│───────┘
                          │                  │  │            │
                          ▼                  ▼  ▼            ▼
         ┌────────────────────┐  ┌──────────────────┐  ┌──────────────┐
         │ División Política   │  │ Servicios de     │  │  DEIS REM   │
         │ Administrativa 2023 │  │ Salud (MINSAL)   │  │ (Producción)│
         │ (BCN/SUBDERE)       │  │                  │  │             │
         │ geoportal ID: 36391 │  │ 29 servicios     │  │ por cod_vig │
         │                     │  │ cod_dep = clave  │  │             │
         │ Polígonos de:        │  └──────────────────┘  └──────────────┘
         │  - 16 regiones       │
         │  - 56 provincias     │
         │  - 346 comunas       │
         └────────────────────┘
                   │
                   │ cut_comuna
                   ▼
    ┌──────────────────────────┐  ┌──────────────────────────┐
    │  INE - Proyecciones de   │  │  FONASA - Beneficiarios  │
    │  Población por Comunas   │  │  por Comuna              │
    │                          │  │                          │
    │  Población, densidad,    │  │  % cobertura,            │
    │  estructura etaria       │  │  N° inscritos FONASA     │
    └──────────────────────────┘  └──────────────────────────┘

    ┌──────────────────────────┐
    │  Establecimientos de     │  cut_comuna
    │  Educación Escolar       │──────────────── (join comunal)
    │  geoportal ID: 35408     │
    └──────────────────────────┘
```

---

## 8. Datasets enlazables: catálogo de fuentes

### 7.1 División Política Administrativa 2023
- **Fuente**: Biblioteca del Congreso Nacional (BCN) / SUBDERE
- **geoportal ID**: 36391
- **Formato**: WFS (polígonos)
- **Clave de unión**: `cut_region`, `cut_provin`, `cut_comuna`
- **Qué aporta**: Geometría de comunas/provincias/regiones → permite hacer mapas coropléticos
- **Uso en tablero**: colorear comunas según densidad de establecimientos

### 7.2 Proyecciones de Población por Comunas (INE)
- **Fuente**: Instituto Nacional de Estadísticas (INE)
- **URL**: https://www.ine.gob.cl/estadisticas/sociales/demografia-y-vitales/proyecciones-de-poblacion
- **Formato**: Excel/CSV descargable
- **Clave de unión**: `cut_comuna` (INE lo llama "Código")
- **Qué aporta**: Población total, densidad, estructura etaria por comuna
- **KPI derivado**: establecimientos por 100.000 habitantes (por tipo, por comuna)

### 7.3 FONASA - Beneficiarios por Comuna
- **Fuente**: FONASA (datos estadísticos)
- **URL**: https://www.fonasa.cl/sites/fonasa/institucional/estadisticas
- **Formato**: Excel descargable
- **Clave de unión**: `cut_comuna`
- **Qué aporta**: N° beneficiarios FONASA, % cobertura pública
- **KPI derivado**: beneficiarios FONASA por establecimiento público de atención primaria

### 7.4 DEIS REM (Reportes Estadísticos Mensuales)
- **Fuente**: DEIS/MINSAL
- **URL**: https://deis.minsal.cl/estadisticas-deis/
- **Formato**: Excel por año
- **Clave de unión**: `cod_vig` (código DEIS del establecimiento)
- **Qué aporta**: Consultas, hospitalizaciones, procedimientos, urgencias por establecimiento
- **Uso**: comparar capacidad instalada (tipo, nivel) vs producción real
- **Nota**: el campo `cod_vig` en el geoportal corresponde al código DEIS de 6 dígitos (e.g., 126704)

### 7.5 Establecimientos Educación Escolar
- **Fuente**: MINEDUC
- **geoportal ID**: 35408
- **Formato**: WFS (puntos)
- **Clave de unión**: `cut_comuna` (análisis por proximidad comunal)
- **Qué aporta**: Colegios, escuelas con matrícula → análisis de infraestructura social por comuna

### 7.6 Servicios de Salud (MINSAL)
- **Fuente**: MINSAL
- **Referencia**: Chile tiene 29 Servicios de Salud + SEREMIs
- **Clave de unión**: `cod_dep` (código del servicio) o `dependenc` (nombre)
- **Qué aporta**: Área geográfica de cobertura, presupuesto, dotación de camas
- **Nota**: No confirmado como WFS en geoportal; puede estar en datos.gob.cl

### 7.7 Jardines Infantiles Fundación Integra
- **Fuente**: Fundación Integra / geoportal.cl
- **Formato**: WFS (puntos)
- **Clave de unión**: `cut_comuna`
- **Qué aporta**: Cobertura de cuidado infantil → análisis junto con establecimientos pediátricos

---

## 9. Problemas prácticos conocidos

### 8.1 GetCapabilities global falla
```
GET https://geoportal.cl/geoserver/wfs?service=WFS&version=2.0.0&request=GetCapabilities
→ Error: "No workspace specified"
```
**Solución**: siempre incluir el workspace en la URL:
```
GET https://geoportal.cl/geoserver/{workspace}/wfs?...&request=GetCapabilities
```

### 8.2 Descubrimiento de workspace names
Los nombres de workspace NO son autodescubribles desde el exterior (no hay endpoint público de listado). Los nombres conocidos:
- `EstablecimientosdesaluddeChile2025` → establecimientos de salud diciembre 2025
- `EstablecimientosdesaluddeChile2026` → (inferido para el dataset de febrero 2026)

Para descubrir workspaces de otros datasets: buscar en el visualizador del geoportal, inspeccionar las peticiones de red del navegador al cargar una capa.

### 8.3 Campos con nombres truncados
Algunos campos tienen nombres truncados (GeoServer impone límite de ~10 chars en shapefiles subyacentes):
- `complejida` (no `complejidad`)
- `cut_provin` (no `cut_provincia`)
- `dependenc` (no `dependencia`)
- `pertenenci` (no `pertenencia`)

### 8.4 Tipos mixtos en campos numéricos
- `cut_region` y `cut_provin` y `cut_comuna` pueden llegar como **string** en el GeoJSON a pesar de ser códigos numéricos. Siempre castear antes de JOIN:
  ```python
  df['cut_comuna'] = df['cut_comuna'].astype(int)
  ```

### 8.5 CQL Filter vs ECQL
El parámetro correcto para filtros es `cql_filter` (no `filter`). Los strings van con comillas simples:
```
cql_filter=nom_comuna='SANTIAGO'   # correcto
cql_filter=nom_comuna="SANTIAGO"   # incorrecto
```

### 8.6 Caracteres especiales en nombres de comunas
`nom_comuna` viene en mayúsculas sin tildes en algunos campos y con tildes en otros:
- `nom_com` → mayúsculas sin procesar (e.g., `CABO DE HORNOS`)
- `nom_comuna` → mayúsculas (e.g., `CABO DE HORNOS`)
- En CQL filter usar mayúsculas: `cql_filter=nom_comuna='ÑUÑOA'`

### 8.7 Descarga total: sin paginación explícita
La capa de establecimientos tiene ~5.181 registros. El servidor los devuelve todos sin paginación. Para capas grandes, usar el parámetro `startIndex` + `count`:
```
&count=1000&startIndex=0
&count=1000&startIndex=1000
```

### 8.8 DEIS vs geoportal: actualización asincrónica
El geoportal publica snapshots (diciembre 2025, febrero 2026), no datos en tiempo real. El DEIS actualiza sus datos con periodicidad mensual/anual. Al cruzar datos de producción (REM) con el mapa del geoportal, verificar que los `cod_vig` correspondan al mismo período.

---

## 10. Esquema de tablas para el ejercicio de alumnos

```
TABLA PRINCIPAL (geoportal WFS):
establecimientos_salud
├── cod_vig         PK del establecimiento (DEIS)
├── cut_region      FK → dim_region
├── cut_provin      FK → dim_provincia
├── cut_comuna      FK → dim_comuna
├── cod_dep         FK → dim_servicio_salud
├── tipo
├── nombre
├── prestador       (Público, Privado, Municipal)
├── nivel           (Primario, Secundario, Terciario)
├── complejida
├── urgencia
├── tipo_aten
├── latitud
└── longitud

DIMENSIÓN TERRITORIAL (DPA + INE):
dim_comuna
├── cut_comuna      PK
├── nom_comuna
├── cut_provin      FK → dim_provincia
├── poblacion       (INE 2024)
├── superficie_km2
└── geom_poligono   (de DPA geoportal)

dim_provincia
├── cut_provin      PK
├── nom_provin
└── cut_region      FK → dim_region

dim_region
├── cut_region      PK
└── nom_region

DIMENSIÓN SALUD (MINSAL):
dim_servicio_salud
├── cod_dep         PK
├── nombre_servicio
└── tipo            (Servicio de Salud / SEREMI)

HECHOS DE PRODUCCIÓN (DEIS REM):
fact_produccion
├── cod_vig         FK → establecimientos_salud
├── año
├── mes
├── n_consultas
├── n_urgencias
├── n_hospitalizaciones
└── n_procedimientos

BENEFICIARIOS (FONASA):
fact_fonasa
├── cut_comuna      FK → dim_comuna
├── año
└── n_beneficiarios
```

---

## 11. Flujo de curado de una capa

El curado se hace siempre contra la **bodega viva**, nunca contra el GeoJSON descargable. La fuente puede ser WFS (geoportal) o ArcGIS REST (otros portales). Los valores documentados son válidos a la fecha del curado y pueden cambiar.

### Setup inicial (una sola vez)

```bash
make setup       # crea .venv/ e instala dependencias
make db-up       # levanta postgres + PostGIS en Docker
```

Crear el archivo `.env` en la raíz del proyecto (gitignoreado):
```
DEEPSEEK_API_KEY=sk-...    # platform.deepseek.com
DATABASE_URL=postgresql://geoportal:geoportal@localhost:5432/geoportal
```

### Pasos por capa

**1. Crear el YAML desde el catálogo**
```bash
make scrape URL=https://geoportal.cl/geoportal/catalog/{id}/...
```
El scraper:
- Extrae verbatim las 5 secciones del catálogo (identificación, contacto, servicio de mapas, descargas, ámbito espacial)
- Auto-detecta la fuente: WFS (si hay URL de GeoServer) o ArcGIS REST (si hay URL `arcgis.com` en descargas)
- Para WFS: descubre `workspace` y `typename` vía GetCapabilities
- Para ArcGIS: busca el Feature Service del organismo, puntúa por similitud con el título, pide confirmación si hay ambigüedad
- Crea `catalog/layers/{slug_del_titulo}.yaml` listo para curar

**2. Cargar muestra en postgres**
```bash
make load LAYER={id}
```
- Descarga 100 filas vía WFS o ArcGIS REST y valida GeoJSON (RFC 7946)
- Guarda la muestra en `catalog/samples/{id}/{fecha}.json` (respaldo con geometría incluida)
- Crea la tabla en postgres con columna `geom` PostGIS

**3. Generar stubs de columnas**
```bash
make profile LAYER={id}
```
Para cada columna genera automáticamente en el YAML:
- `null_pct` y `distinct_count` desde los datos reales
- `known_values` (si ≤ 20 valores distintos) o `examples` (si > 20)
- `arcgis_description` — alias oficial del Feature Service (solo capas ArcGIS)
- `llm_description` — descripción generada por DeepSeek usando el dominio oficial del organismo como contexto
- `human_description: ""` — campo vacío que rellena el curador

**4. Curar columna a columna en DBeaver**

Conexión postgres:
- Host: `localhost` · Puerto: `5432` · Base de datos: `geoportal`
- Usuario: `geoportal` · Contraseña: `geoportal`

Para cada columna, abrir el YAML y completar `human_description` apoyándose en DBeaver:
```sql
-- Valores únicos de un campo categórico
SELECT DISTINCT tipo, count(*) FROM {tabla} GROUP BY tipo ORDER BY count DESC;

-- Detectar nulos
SELECT count(*) FILTER (WHERE fono IS NULL)     AS nulos,
       count(*) FILTER (WHERE fono IS NOT NULL)  AS con_valor
FROM {tabla};

-- Tipo real de una columna
SELECT pg_typeof(cut_comuna) FROM {tabla} LIMIT 1;

-- Ver geometría en texto
SELECT nombre, ST_AsText(geom), ST_X(geom) AS lon, ST_Y(geom) AS lat
FROM {tabla} LIMIT 5;
```

Completar también `is_pk`, `is_fk`, `fk_target` para las claves foráneas.

**5. Dar check**
```bash
# En el YAML:
schema_status: verified
last_reviewed: "YYYY-MM-DD"

# Luego:
make build-er    # regenera el diagrama ER
```

---

## 12. Checklist para sesión de trabajo con el geoportal

**Regla de curado:** siempre contra la bodega viva (WFS o ArcGIS REST), nunca contra el GeoJSON descargable. La bodega muerta puede estar desincronizada. Registrar la fecha del curado.

- [ ] `make scrape URL=...` — crear YAML desde el catálogo
- [ ] `make load LAYER=...` — cargar 100 filas en postgres
- [ ] `make profile LAYER=...` — generar stubs de columnas con llm_description
- [ ] Completar `human_description` columna a columna usando DBeaver
- [ ] Verificar tipos de datos de claves foráneas (int vs string)
- [ ] Completar `is_pk`, `is_fk`, `fk_target` para columnas clave
- [ ] Completar `use_cases` y `relations` en el YAML
- [ ] Cambiar `schema_status: verified` y actualizar `last_reviewed`
- [ ] `make build-er` — regenerar diagrama ER
- [ ] Verificar `totalFeatures` en el GeoJSON para saber el tamaño total
- [ ] Confirmar encoding de caracteres especiales (UTF-8)
- [ ] Al cruzar con INE/FONASA: estandarizar `cut_comuna` como int de 5 dígitos
- [ ] Al cruzar con DEIS REM: verificar que `cod_vig` no tenga ceros iniciales

---

## 13. URLs de referencia

| Recurso | URL |
|---------|-----|
| Geoportal catálogo | https://geoportal.cl/catalog |
| Geoportal visualizador | https://geoportal.cl/geoportal/map/4 |
| GeoServer base | https://geoportal.cl/geoserver/ |
| DEIS estadísticas | https://deis.minsal.cl/estadisticas-deis/ |
| INE proyecciones población | https://www.ine.gob.cl/estadisticas/sociales/demografia-y-vitales/proyecciones-de-poblacion |
| FONASA estadísticas | https://www.fonasa.cl/sites/fonasa/institucional/estadisticas |
| Codigos CUT (SUBDERE) | https://www.subdere.gov.cl/documentacion/codigos-unicos-territoriales-cut |
| Dataset salud DPA 2023 | https://geoportal.cl geoportal ID: 36391 |
| Dataset establecimientos dic 2025 | https://geoportal.cl geoportal ID: 36779 |
| Dataset establecimientos feb 2026 | https://geoportal.cl geoportal ID: 37171 |
| Dataset educación escolar | https://geoportal.cl geoportal ID: 35408 |
