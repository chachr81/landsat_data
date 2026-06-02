# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos principales

```bash
# Entorno virtual — usar siempre .venv/bin/python
.venv/bin/python main.py ingest --start 2013-04-11 --end 2026-05-31
.venv/bin/python main.py ingest --start 2024-01-01 --end 2024-12-31 --dry-run  # simula sin descargar

# Solo Silver (Bronze → aculeo_clear), sin correr Gold
.venv/bin/python main.py process-clear --cuenca 411 --indices MNDWI NDWI

# Análisis completo Silver + Gold (ClearProcessor → MetricsPipeline)
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI
.venv/bin/python main.py analyze --cuenca 411 --year 2024 --indices MNDWI NDWI
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI --dry-run  # muestra conteos, no procesa

# Generar gráficas de series de tiempo
.venv/bin/python main.py plot --cuenca 411 --index MNDWI --out water_variation.png

# Limpiar listas temporales en la API M2M
.venv/bin/python main.py cleanup-lists --list-id temp_list_12345 --force

# Crear schemas de base de datos desde cero
psql ... -f sql/schemas/01_bronze_raster.sql    # aculeo_raw
psql ... -f sql/schemas/02_silver_gold_metrics.sql  # aculeo_clear + aculeo_metricas

# Truncar Gold con reinicio de secuencia (vía SSH al contenedor)
ssh -i ~/.ssh/negentropy.pem christian@5.78.201.244 \
  "docker exec negentropy_postgres psql -U postgres -d maps_negentropy -c \
  'TRUNCATE aculeo_metricas.water_metrics RESTART IDENTITY CASCADE;'"
```

## Arquitectura: pipeline medallion

El sistema implementa una arquitectura **Medallion** (Bronze → Silver → Gold) para datos Landsat Collection 2 Level-2 del USGS, enfocado en el monitoreo de la **Laguna de Aculeo** (Chile, sscuenca_id=411, WRS-2 path/row 233/083).

```
USGS M2M API
     │
     ▼
[Bronze Layer]  aculeo/bronze/ingestion.py  →  PostGIS: aculeo_raw.landsat_scenes
                aculeo/bronze/m2m_client.py              aculeo_raw.landsat_bands
                aculeo/bronze/mtl_parser.py   (rasters teselados 512×512, particionados por año)
                                              raster2pgsql para inserción
     │
     ▼
[Silver Layer]  aculeo/silver/processor.py  →  PostGIS: aculeo_clear.spectral_indices
                aculeo/silver/qa.py             (índices MNDWI/NDWI con QA aplicado,
                                                 recortados al AOI, particionados por año)
     │
     ▼
[Gold Layer]    aculeo/gold/pipeline.py     →  PostGIS: aculeo_metricas.water_metrics
                aculeo/gold/detector.py        (detección GMM + componentes conectados)
                aculeo/gold/providers.py        (métricas de área de agua por escena)
```

El comando `analyze` ejecuta **Silver + Gold en secuencia**: primero `ClearProcessor.process_pending()` y luego `MetricsPipeline` sobre las escenas ya procesadas en `aculeo_clear`.

### Módulos clave

- **`main.py`** + **`aculeo/cli.py`**: Punto de entrada CLI con subcomandos `ingest`, `process-clear`, `analyze`, `plot`, `cleanup-lists`.
- **`aculeo/viz/time_series.py`**: Script de visualización llamado por el subcomando `plot`. Lee desde `aculeo_metricas.water_metrics` vía SSH tunnel.
- **`aculeo/bronze/ingestion.py`**: Orquestador Bronze. Deduplicación por `displayId` contra `entity_id` en BD. Re-procesa escenas con 0 bandas.
- **`aculeo/bronze/m2m_client.py`**: Cliente HTTP M2M USGS. `entityId` = WRS-2 Scene ID; `displayId` = Product ID.
- **`aculeo/bronze/mtl_parser.py`**: Parsea MTL de C2L2. `entity_id` ← `LANDSAT_PRODUCT_ID`; `display_id` ← `LANDSAT_SCENE_ID`.
- **`aculeo/silver/processor.py`**: Capa Silver. Lee bandas crudas, aplica QA, calcula índices y guarda en `aculeo_clear.spectral_indices`.
- **`aculeo/silver/qa.py`**: `LandsatC2L2QAProcessor` — máscaras QA_PIXEL + QA_RADSAT + QA_AEROSOL para C2L2 OLI_TIRS.
- **`aculeo/gold/detector.py`**: `WaterBodyDetector` — GMM → componentes conectados → DecisionTree. Ver sección de calibración abajo.
- **`aculeo/gold/pipeline.py`**: `MetricsPipeline` — orquesta lectura, detección y escritura. Centroide de referencia del lago: `(322635.0, -3746696.0)` SRID 32619.
- **`aculeo/gold/interfaces.py`**: ABCs: `ISpectralIndexReader`, `IMetricsWriter`, `IWaterIndexStrategy`, `IQAMaskProcessor`.
- **`aculeo/infra/config.py`**: `load_config()`, `load_env()`, `setup_logger()`.
- **`aculeo/infra/db.py`**: `get_db_connection_string()`, `get_ssh_tunnel()`.

### Conexión a base de datos

PostgreSQL corre en servidor remoto (Hetzner, contenedor `negentropy_postgres`). Toda conexión pasa por túnel SSH. Requiere en `.env`: `DB_URL`, `SSH_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `M2M_USERNAME`, `M2M_PASSWORD`.

`get_db_connection_string()` fuerza la BD `maps_negentropy` independientemente del `DB_URL`.

### Configuración

- `config/landsat_config.yaml`: fuente de verdad para parámetros no sensibles (datasets, bandas, QA masks, AOI, cloud cover máximo = 70%).
- Dataset activo: solo `landsat_ot_c2_l2` (Landsat 8/9 OLI_TIRS). Landsat 7 ETM+ excluido (SLC-off).

### Base de datos

- `aculeo_raw.landsat_bands`, `aculeo_clear.spectral_indices` y `aculeo_metricas.water_metrics` están **particionadas por año** (2013–2026).
- `raster2pgsql` debe estar instalado (viene con PostGIS). `BronzeIngestion._check_dependencies()` lo verifica.
- Volumen Hetzner montado en `/mnt/HC_Volume_105395115` en el servidor remoto.
- Vistas Gold: `aculeo_metricas.v_serie_temporal` (serie completa) y `aculeo_metricas.v_resumen_anual` (agrega por año con cota de 12 km²).
- `aculeo_metricas.v_consenso_escenas`: une MNDWI y NDWI por escena; `consensus_status` = `high_confidence` / `low_confidence` / `no_water`; `consensus_area_km2` = intersección geométrica MNDWI∩NDWI en km².

---

## Calibración del detector (`aculeo/gold/detector.py`)

### Parámetros activos (2026-06-01)

| Constante | Valor | Notas |
|---|---|---|
| `_THRESHOLD_RANGE_SUMMER` | `(-0.40, 0.10)` | oct–mar (verano austral, suelo seco) |
| `_THRESHOLD_RANGE_WINTER` | `(-0.35, 0.05)` | abr–sep (invierno austral, suelos húmedos) |
| `_MAX_CENTROID_DIST_M` | `1000.0` m | Radio desde centroide del lago |
| `_MAX_WATER_AREA_KM2` | `12.0` km² | Filtro por componente individual |
| `_MIN_COMPACTNESS` | `0.03` | Descarta sombras y canales |
| `_MIN_SEPARATION_STD` | `0.5` | Separación mínima entre modos GMM |
| `_MIN_VALID_PIXEL_RATIO` | `0.15` | Escenas con <15% píxeles válidos → `low_quality` |

### Flujo de detección (filtros activos)

```
valid_ratio ≥ 15%  →  GMM 2 componentes  →  F1: threshold en rango estacional
→  F3: is_separated (sep ≥ 0.5σ)  →  water_mask = index > threshold
→  binary_closing(3×3)  →  F4: componentes ≥ 9px
→  F5: área ≤ 15 km², compacidad ≥ 0.03, centroide ≤ 1000 m del lago
→  DecisionTree sobre componentes → water_detected
```

**Nota:** El prefiltro de bimodalidad Sarle (v0.1.x) y el filtro F2 (rango de media del modo agua) fueron **eliminados**. El threshold usa frontera bayesiana óptima (`argmin|dens_water − dens_land|`). Se agregó `confidence_score` (0–1) en `DetectionResult` y `water_metrics`, y calibración estacional del rango de threshold.

### Referencia histórica del lago

- Área máxima validada: **~10.34 km²** (métrica histórica confirmada)
- Centroide de referencia (SRID 32619): `X=322635.0, Y=-3746696.0`
- Serie temporal en BD: vaciado 2013→2020, recuperación 2023→2026

---

## Tareas pendientes

### Detector Gold

- **Aplicar Sarle con umbral 0.45** (en lugar de eliminarlo por completo): reduce falsos positivos NDWI en años secos (2019: 1 detección espuria de 5.12 km², 2022: 2 detecciones con `water_index_mean` negativo). Umbral 0.45 es permisivo con bimodalidad débil (lago pequeño) pero rechaza distribuciones unimodales puras.

- **Agregar R²/RMSE del ajuste GMM** como métricas de calidad en `DetectionResult` y `water_metrics`: miden qué tan bien el modelo bimodal se ajusta al histograma real de píxeles. Reemplazarían a Sarle de forma más rigurosa. Calcular en `_gmm_threshold()` comparando densidad GMM vs histograma empírico (bins=50).

- **Investigar scene_id=160** (MNDWI threshold=−392, raster corrupto). Verificar bandas en BD y considerar re-ingesta.

- **Commitear cambios del detector** (rama `develop`): los cambios de esta sesión aún no están en git.

### Ingesta Bronze

- **`scene_id=6`** (`LC09_L2SP_233083_20260526`, 2026-05-26): bandas eliminadas, será re-ingestada en el próximo run.
- **`scene_id=224`** (`LC08_L2SP_233083_20200314`, 2020-03-14): solo 5/6 bandas (falta `SR_B5`). Pendiente decidir re-ingesta manual.
- Relanzar ingesta para cubrir escenas 2013–2020 restantes, luego re-ejecutar `analyze`.

### Análisis Silver + Gold

```bash
.venv/bin/python main.py analyze --cuenca 411 --indices MNDWI NDWI
```

Tras completar la ingesta, re-ejecutar para cubrir las escenas nuevas. Consultar resultados vía `aculeo_metricas.v_serie_temporal` y `aculeo_metricas.v_resumen_anual`.