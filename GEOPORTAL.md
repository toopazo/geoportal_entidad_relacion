# GEOPORTAL Chile — Guía técnica para agentes IA y análisis de datos

> Documento vivo. Actualizar con cada nueva sesión de exploración.
> Propósito: proveer contexto suficiente para que un agente IA pueda razonar sobre el ecosistema del geoportal sin necesidad de redescubrir lo ya aprendido.

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

## 4. Estructura de la capa principal: Establecimientos de Salud

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

## 5. El CUT: la clave foránea universal del Estado de Chile

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

## 6. Mapa de relaciones: tablas enlazables

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

## 7. Datasets enlazables: catálogo de fuentes

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

## 8. Problemas prácticos conocidos

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

## 9. Esquema de tablas para el ejercicio de alumnos

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

## 10. Checklist para sesión de trabajo con el geoportal

- [ ] Identificar el workspace name del dataset (inspeccionar el visualizador si es necesario)
- [ ] Correr `DescribeFeatureType` para obtener columnas exactas
- [ ] Verificar tipos de datos de claves foráneas (int vs string)
- [ ] Verificar `totalFeatures` en el GeoJSON para saber el tamaño total
- [ ] Confirmar encoding de caracteres especiales (UTF-8)
- [ ] Al cruzar con INE/FONASA: estandarizar `cut_comuna` como int de 5 dígitos
- [ ] Al cruzar con DEIS REM: verificar que `cod_vig` no tenga ceros iniciales

---

## 11. URLs de referencia

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
