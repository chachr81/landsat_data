# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos principales

```bash
# Entorno virtual — usar siempre .venv/bin/python
.venv/bin/python main.py ingest --start 2013-04-11 --end 2026-05-31

# Análisis completo Silver + Gold (ClearProcessor → MetricsPipeline)
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI
.venv/bin/python main.py analyze --cuenca 411 --year 2024 --indices MNDWI NDWI

# Generar gráficas de series de tiempo
.venv/bin/python main.py plot --cuenca 411 --index MNDWI --out water_variation.png

# Limpiar listas temporales en la API M2M
.venv/bin/python main.py cleanup-lists --list-id temp_list_12345 --force

# Crear schemas de base de datos desde cero
psql ... -f sql/schemas/01_bronze_raster.sql    # aculeo_raw
psql ... -f sql/schemas/02_silver_gold_metrics.sql  # aculeo_clear + aculeo_metricas
```

## Arquitectura: pipeline medallion

El sistema implementa una arquitectura **Medallion** (Bronze → Silver → Gold) para datos Landsat Collection 2 Level-2 del USGS, enfocado en el monitoreo de la **Laguna de Aculeo** (Chile, sscuenca_id=411, WRS-2 path/row 233/083).

```
USGS M2M API
     │
     ▼
[Bronze Layer]  etl/bronze_ingestion.py  →  PostGIS: aculeo_raw.landsat_scenes
                etl/m2m_client.py                     aculeo_raw.landsat_bands
                etl/mtl_parser.py            (rasters teselados 512×512, particionados por año)
                                             raster2pgsql para inserción
     │
     ▼
[Silver Layer]  etl/clear_processor.py   →  PostGIS: aculeo_clear.spectral_indices
                etl/analysis/qa_processor.py  (índices MNDWI/NDWI con QA aplicado,
                                               recortados al AOI, particionados por año)
     │
     ▼
[Gold Layer]    etl/analysis/pipeline.py  →  PostGIS: aculeo_metricas.water_metrics
                etl/analysis/water_detector.py  (detección GMM + componentes conectados)
                etl/analysis/data_providers.py  (métricas de área de agua por escena)
```

El comando `analyze` ejecuta **Silver + Gold en secuencia**: primero `ClearProcessor.process_pending()` y luego `MetricsPipeline` sobre las escenas ya procesadas en `aculeo_clear`.

### Módulos clave

- **`main.py`**: Único punto de entrada CLI con subcomandos `ingest`, `analyze`, `plot`, `cleanup-lists`.
- **`etl/bronze_ingestion.py`**: Orquestador Bronze. Deduplicación por `displayId` (Product ID) de M2M contra columna `entity_id` de la BD. Solo salta escenas que existen en BD **y tienen al menos 1 banda** (escenas con 0 bandas son re-procesadas).
- **`etl/m2m_client.py`**: Cliente HTTP M2M USGS. `entityId` = WRS-2 Scene ID; `displayId` = Product ID (lo que se guarda en BD como `entity_id`).
- **`etl/mtl_parser.py`**: Parsea MTL de C2L2. `entity_id` ← `LANDSAT_PRODUCT_ID`; `display_id` ← `LANDSAT_SCENE_ID` (bajo `IMAGE_ATTRIBUTES`, no `PRODUCT_CONTENTS`).
- **`etl/clear_processor.py`**: Capa Silver. Lee bandas crudas vía `aculeo_clear.get_raw_bands_clipped()`, aplica QA, calcula índices y guarda rasters en `aculeo_clear.spectral_indices`.
- **`etl/analysis/qa_processor.py`**: `LandsatC2L2QAProcessor` — máscaras QA_PIXEL + QA_RADSAT + QA_AEROSOL para C2L2 OLI_TIRS.
- **`etl/analysis/water_detector.py`**: `WaterBodyDetector` — detección estadística pura (bimodalidad Sarle → GMM → componentes conectados → DecisionTree). Sin polígono de referencia.
- **`etl/analysis/pipeline.py`**: `MetricsPipeline` — coordina lectura de índices, detección y escritura de métricas. Depende de `ISpectralIndexReader` e `IMetricsWriter` (no de concretos).
- **`etl/analysis/interfaces.py`**: ABCs: `ISpectralIndexReader`, `IMetricsWriter`, `IWaterIndexStrategy`, `IQAMaskProcessor`.
- **`etl/utils.py`**: Helpers: `load_config()`, `load_env()`, `setup_logger()`, `get_db_connection_string()`, `get_ssh_tunnel()`.

### Conexión a base de datos

PostgreSQL corre en servidor remoto (Hetzner). Toda conexión pasa por túnel SSH (`get_ssh_tunnel()` en `etl/utils.py`). Requiere en `.env`: `DB_URL`, `SSH_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `M2M_USERNAME`, `M2M_PASSWORD`.

`get_db_connection_string()` fuerza la BD `maps_negentropy` independientemente del `DB_URL`.

### Configuración

- `config/landsat_config.yaml`: fuente de verdad para parámetros no sensibles (datasets, bandas, QA masks, AOI, cloud cover máximo = 70%).
- Dataset activo: solo `landsat_ot_c2_l2` (Landsat 8/9 OLI_TIRS). Landsat 7 ETM+ excluido (SLC-off).

### Base de datos

- `aculeo_raw.landsat_bands` y `aculeo_clear.spectral_indices` y `aculeo_metricas.water_metrics` están **particionadas por año** (2013–2026). Las particiones ya existen en el schema SQL.
- `raster2pgsql` debe estar instalado (viene con PostGIS). `BronzeIngestion._check_dependencies()` lo verifica.
- Volumen Hetzner montado en `/mnt/HC_Volume_105395115` en el servidor remoto. Si se añade capacidad, ejecutar `resize2fs /dev/sdb` en el servidor.

---

## Tareas pendientes

### Ingesta Bronze (en curso al 2026-05-31)

- **Run activo**: procesando ~134 escenas desde 2020-02-27 hacia atrás hasta 2013-04-11.
- **`scene_id=6`** (`LC09_L2SP_233083_20260526`, 2026-05-26): bandas eliminadas por duplicación (3× procesada en runs abortados). Quedó sin bandas → será re-ingestada automáticamente en el próximo run.
- **`scene_id=224`** (`LC08_L2SP_233083_20200314`, 2020-03-14): solo 5/6 bandas (falta `SR_B5`, fallo de disco). Tiene bandas → el pipeline la saltará. Pendiente decidir si re-ingestar manualmente.
- Relanzar al terminar con el rango completo para cubrir `scene_id=6` y las escenas 2013–2020 restantes.

### Análisis Silver + Gold (pendiente)

Una vez completa la ingesta:

```bash
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI
```

Esto correrá:
1. `ClearProcessor.process_pending()` → calcula índices espectrales para todas las escenas nuevas en `aculeo_clear`
2. `MetricsPipeline` → detección GMM y métricas en `aculeo_metricas.water_metrics`
3. Consultar resultados vía vista `aculeo_metricas.v_serie_temporal`
