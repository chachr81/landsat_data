<div align="center">

# Aculeo Monitor

**Pipeline de monitoreo hidrológico de Laguna de Aculeo sobre datos Landsat C2L2**

<p>
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
| `classification_status` | TEXT | `water_detected` / `no_water` / `low_quality` |
| `water_geom` | GEOMETRY(MULTIPOLYGON, 32619) | Geometría del espejo detectado |

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

1. **Calidad mínima**: descartar escenas con menos del 15% de píxeles válidos tras QA
2. **Bimodalidad Sarle**: coeficiente de Sarle > 0.555
3. **Rango de threshold GMM**: umbral observado entre -0.003 y -0.334, mediana -0.16 (fuera de rango → `no_water`)
4. **Media del modo agua**: rango plausible calibrado para el lago
5. **Closing morfológico 3×3**: rellena huecos internos no insulares
6. **Área máxima**: 15 km² (histórico Aculeo menor a 12 km²)
7. **Compacidad mínima**: 0.03, descarta sombras elongadas y canales
8. **Centroide de referencia**: máximo 5 km desde (322635, -3746696) en SRID 32619

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
| `classification_status` | TEXT | `water_detected` / `no_water` / `low_quality` |
| `water_geom` | GEOMETRY(MULTIPOLYGON, 32619) | Detected water body geometry |

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

1. **Minimum quality**: discard scenes with less than 15% valid pixels after QA
2. **Sarle bimodality**: Sarle coefficient > 0.555
3. **GMM threshold range**: observed range -0.003 to -0.334, median -0.16 (out-of-range yields `no_water`)
4. **Water mode mean**: plausible range calibrated for the lake
5. **Morphological closing 3x3**: fills internal non-island gaps
6. **Maximum area**: 15 km² (historical Aculeo below 12 km²)
7. **Minimum compactness**: 0.03, discards elongated shadows and channels
8. **Reference centroid**: maximum 5 km from (322635, -3746696) in SRID 32619

## Additional Documentation

- [Guide to Landsat Quality Assessment Bands](./docs/LANDSAT_QA_BANDS.md)
