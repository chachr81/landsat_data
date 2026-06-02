<div align="center">

# Aculeo Lake Monitor

**Pipeline de monitoreo hidrológico de Laguna de Aculeo sobre datos Landsat C2L2**

<p>
  <img src="https://img.shields.io/badge/version-0.1.2-green.svg" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.12-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/PostgreSQL-18.3-blue.svg" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/PostGIS-3.6-orange.svg" alt="PostGIS">
</p>

[Go to English Documentation](#english-documentation)

</div>


# Documentación en Español

**Tabla de Contenidos**

1. [Visión General](#visión-general)
2. [Arquitectura del Proyecto](#arquitectura-del-proyecto)
3. [Modelo de Datos](#modelo-de-datos)
4. [Instalación y Configuración](#instalación-y-configuración)
5. [Guía de Uso del CLI](#guía-de-uso-del-cli)
6. [Detección de Agua](#detección-de-agua)
7. [Documentación Adicional](#documentación-adicional)

## Visión General

Pipeline de monitoreo hidrológico de la **Laguna de Aculeo** (Chile, sscuenca_id=411, WRS-2 path/row 233/083). Automatiza la descarga de datos **Landsat Collection 2 Level-2** desde la API M2M del USGS e implementa una arquitectura **Medallion** (Bronze → Silver → Gold) sobre **PostgreSQL/PostGIS**, con detección estadística del espejo de agua mediante GMM.

## Arquitectura del Proyecto

```
USGS M2M API
     │
     ▼
[Bronze]  aculeo/bronze/     →  aculeo_raw.landsat_scenes
          ingestion.py           aculeo_raw.landsat_bands (particionado por año 2013–2026)
          m2m_client.py          aculeo_raw.download_log
          mtl_parser.py
     │
     ▼
[Silver]  aculeo/silver/     →  aculeo_clear.spectral_indices (particionado por año)
          processor.py           Índices MNDWI/NDWI con QA aplicado, recortados al AOI
          qa.py
     │
     ▼
[Gold]    aculeo/gold/       →  aculeo_metricas.water_metrics (particionado por año)
          pipeline.py            Área, geometría y métricas del espejo de agua detectado
          detector.py
          providers.py
          interfaces.py
```

```
.
├── aculeo/
│   ├── infra/          # SSH tunnel, conexión BD, configuración, logging
│   ├── bronze/         # Descarga e ingesta de rasters crudos Landsat
│   ├── silver/         # QA + cálculo de índices espectrales
│   ├── gold/           # Detección estadística de agua y métricas
│   ├── viz/            # Visualización de serie temporal
│   └── cli.py          # Entry point CLI
├── config/             # landsat_config.yaml + GeoJSON del AOI
├── data/               # db_backup.dump (Git LFS)
├── docs/               # Documentación adicional
├── sql/schemas/        # DDL Bronze, Silver y Gold
├── main.py             # Wrapper delgado del CLI
└── pyproject.toml      # Dependencias del proyecto
```

## Modelo de Datos

### `aculeo_raw.landsat_scenes`
| Columna | Tipo | Descripción |
|---|---|---|
| `scene_id` | SERIAL PK | ID interno |
| `entity_id` | TEXT UNIQUE | Product ID USGS |
| `display_id` | TEXT | WRS-2 Scene ID |
| `satellite` | TEXT | `LANDSAT_8` / `LANDSAT_9` |
| `acquisition_date` | DATE | Fecha de adquisición |
| `cloud_cover` | REAL | % nubosidad |
| `footprint` | GEOMETRY(POLYGON, 4326) | Huella espacial |

### `aculeo_raw.landsat_bands` (particionada por año)
| Columna | Tipo | Descripción |
|---|---|---|
| `rid` | SERIAL | ID de tile |
| `scene_id` | INTEGER FK | Referencia a escena |
| `band_name` | TEXT | `SR_B3`, `SR_B5`, `SR_B6`, `QA_PIXEL`, `QA_RADSAT`, `SR_QA_AEROSOL` |
| `rast` | RASTER | Tile 512×512 px, SRID 32619 |

### `aculeo_clear.spectral_indices` (particionada por año)
| Columna | Tipo | Descripción |
|---|---|---|
| `scene_id` | INTEGER FK | Referencia a escena |
| `index_type` | TEXT | `MNDWI` / `NDWI` |
| `rast` | RASTER | Índice recortado al AOI, SRID 32619 |
| `valid_pixel_ratio` | REAL | Fracción de píxeles válidos tras QA |

### `aculeo_metricas.water_metrics` (particionada por año)
| Columna | Tipo | Descripción |
|---|---|---|
| `acquisition_date` | DATE | Fecha de escena |
| `water_index_type` | TEXT | `MNDWI` / `NDWI` |
| `total_water_area_km2` | REAL | Área del espejo detectado |
| `mndwi_threshold_used` | REAL | Umbral GMM de Bayes (NULL si no se corrió GMM) |
| `gmm_separation` | REAL | Separación en σ entre modos GMM agua/tierra |
| `confidence_score` | REAL | Score 0–1 de confianza en la detección |
| `classification_status` | TEXT | `water_detected` / `no_water` / `low_quality` |
| `water_geom` | GEOMETRY(MULTIPOLYGON, 32619) | Geometría del espejo detectado |

### Vistas Gold
| Vista | Descripción |
|---|---|
| `aculeo_metricas.v_serie_temporal` | Serie temporal completa por índice |
| `aculeo_metricas.v_resumen_anual` | Agrega por año; cota de 12 km² |
| `aculeo_metricas.v_consenso_escenas` | Une MNDWI y NDWI por escena; `consensus_status` = `high_confidence` / `low_confidence` / `no_water`; `consensus_area_km2` = intersección geométrica MNDWI∩NDWI en km² |

## Instalación y Configuración

### Requisitos

- Python 3.12+
- PostgreSQL 18.3+ con PostGIS 3.6+ y extensión `postgis_raster`
- Acceso SSH al servidor remoto (túnel para BD)
- Credenciales [USGS M2M API](https://m2m.cr.usgs.gov/)

### Pasos

```bash
git clone https://github.com/chachr81/landsat_data.git
cd landsat_data
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # completar con credenciales reales
```

### Schemas de base de datos

```bash
psql ... -f sql/schemas/01_bronze_raster.sql
psql ... -f sql/schemas/02_silver_gold_metrics.sql
```

## Guía de Uso del CLI

```bash
# Ingesta Bronze
.venv/bin/python main.py ingest --start 2013-04-11 --end 2026-05-31

# Silver + Gold completo
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI

# Dry-run (sin procesar, solo conteos)
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI --dry-run

# Solo un año
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI --year 2015

# Gráfica de serie temporal
.venv/bin/python main.py plot --cuenca 411 --index MNDWI --out variacion.png

# Limpiar listas temporales M2M
.venv/bin/python main.py cleanup-lists --list-id temp_list_12345 --force
```

## Detección de Agua

El detector (`aculeo/gold/detector.py`) aplica una cadena de filtros calibrados para Laguna de Aculeo:

1. **Calidad mínima**: descartar escenas con menos del 15% de píxeles válidos tras QA (`_MIN_VALID_PIXEL_RATIO = 0.15`)
2. **GMM 2 componentes**: ajuste Gaussiano Mixture Model; threshold = frontera bayesiana óptima (`argmin|dens_agua − dens_tierra|`)
3. **Rango de threshold estacional**:
   - Verano austral (oct–mar): `(-0.40, 0.10)` - suelo seco
   - Invierno austral (abr–sep): `(-0.35, 0.05)` - suelos húmedos
4. **Separación GMM**: modos deben estar a ≥ 0.5σ de distancia (`_MIN_SEPARATION_STD`)
5. **Closing morfológico 3×3**: rellena huecos internos no insulares
6. **Área máxima por componente**: 12 km² (`_MAX_WATER_AREA_KM2`; histórico Aculeo ≤ 10.34 km²)
7. **Compacidad mínima**: 0.03, descarta sombras elongadas y canales
8. **Centroide de referencia**: máximo 1 km desde (322635, -3746696) en SRID 32619
9. **`confidence_score`** (0 - 1): reportado en `water_metrics` para cada detección

## Documentación Adicional

- [Guía de Bandas de Calidad QA de Landsat](./docs/LANDSAT_QA_BANDS.md)


# English Documentation

**Table of Contents**

1. [Overview](#overview)
2. [Project Architecture](#project-architecture)
3. [Data Model](#data-model)
4. [Installation and Setup](#installation-and-setup)
5. [CLI Usage Guide](#cli-usage-guide)
6. [Water Detection](#water-detection)
7. [Additional Documentation](#additional-documentation-1)

## Overview

Hydrological monitoring pipeline for **Laguna de Aculeo** (Chile, sscuenca_id=411, WRS-2 path/row 233/083). Automates download of **Landsat Collection 2 Level-2** data from the USGS M2M API and implements a **Medallion architecture** (Bronze → Silver → Gold) on **PostgreSQL/PostGIS**, with statistical water surface detection via GMM.

## Project Architecture

```
USGS M2M API
     │
     ▼
[Bronze]  aculeo/bronze/     →  aculeo_raw.landsat_scenes
          ingestion.py           aculeo_raw.landsat_bands (partitioned by year 2013–2026)
          m2m_client.py          aculeo_raw.download_log
          mtl_parser.py
     │
     ▼
[Silver]  aculeo/silver/     →  aculeo_clear.spectral_indices (partitioned by year)
          processor.py           MNDWI/NDWI indices with QA applied, clipped to AOI
          qa.py
     │
     ▼
[Gold]    aculeo/gold/       →  aculeo_metricas.water_metrics (partitioned by year)
          pipeline.py            Area, geometry and metrics of detected water surface
          detector.py
          providers.py
          interfaces.py
```

## Data Model

### `aculeo_raw.landsat_scenes`
| Column | Type | Description |
|---|---|---|
| `scene_id` | SERIAL PK | Internal ID |
| `entity_id` | TEXT UNIQUE | USGS Product ID |
| `satellite` | TEXT | `LANDSAT_8` / `LANDSAT_9` |
| `acquisition_date` | DATE | Acquisition date |
| `cloud_cover` | REAL | Cloud cover % |
| `footprint` | GEOMETRY(POLYGON, 4326) | Scene footprint |

### `aculeo_metricas.water_metrics` (partitioned by year)
| Column | Type | Description |
|---|---|---|
| `acquisition_date` | DATE | Scene date |
| `water_index_type` | TEXT | `MNDWI` / `NDWI` |
| `total_water_area_km2` | REAL | Detected water area |
| `mndwi_threshold_used` | REAL | GMM Bayes threshold (NULL if GMM not run) |
| `gmm_separation` | REAL | Separation in σ between water/land GMM modes |
| `confidence_score` | REAL | Detection confidence score 0–1 |
| `classification_status` | TEXT | `water_detected` / `no_water` / `low_quality` |
| `water_geom` | GEOMETRY(MULTIPOLYGON, 32619) | Detected water body geometry |

### Gold Views
| View | Description |
|---|---|
| `aculeo_metricas.v_serie_temporal` | Full time series by index |
| `aculeo_metricas.v_resumen_anual` | Aggregated by year; 12 km² cap |
| `aculeo_metricas.v_consenso_escenas` | Joins MNDWI and NDWI per scene; `consensus_status` = `high_confidence` / `low_confidence` / `no_water`; `consensus_area_km2` = geometric intersection MNDWI∩NDWI in km² |

## Installation and Setup

### Prerequisites

- Python 3.12+
- PostgreSQL 18.3+ with PostGIS 3.6+ and `postgis_raster`
- SSH access to remote server (tunnel for DB)
- [USGS M2M API](https://m2m.cr.usgs.gov/) credentials

### Steps

```bash
git clone https://github.com/chachr81/landsat_data.git
cd landsat_data
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in real credentials
```

## CLI Usage Guide

```bash
# Bronze ingestion
.venv/bin/python main.py ingest --start 2013-04-11 --end 2026-05-31

# Full Silver + Gold
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI

# Dry-run (counts only, no processing)
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI --dry-run

# Single year
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI --year 2015

# Time series plot
.venv/bin/python main.py plot --cuenca 411 --index MNDWI --out variacion.png
```

## Water Detection

The detector (`aculeo/gold/detector.py`) applies a calibrated filter chain for Laguna de Aculeo:

1. **Minimum quality**: discard scenes with less than 15% valid pixels after QA (`_MIN_VALID_PIXEL_RATIO = 0.15`)
2. **GMM 2-component fit**: threshold = optimal Bayesian boundary (`argmin|dens_water − dens_land|`)
3. **Seasonal threshold range**:
   - Austral summer (Oct–Mar): `(-0.40, 0.10)` - dry soil
   - Austral winter (Apr–Sep): `(-0.35, 0.05)` - moist soil
4. **GMM separation**: modes must be ≥ 0.5σ apart (`_MIN_SEPARATION_STD`)
5. **Morphological closing 3×3**: fills internal non-island gaps
6. **Maximum area per component**: 12 km² (`_MAX_WATER_AREA_KM2`; historical Aculeo ≤ 10.34 km²)
7. **Minimum compactness**: 0.03, discards elongated shadows and channels
8. **Reference centroid**: maximum 1 km from (322635, -3746696) in SRID 32619
9. **`confidence_score`** (0 - 1): stored in `water_metrics` for each detection

## Additional Documentation

- [Guide to Landsat Quality Assessment Bands](./docs/LANDSAT_QA_BANDS.md)
