<div align="center">

# Motor de Ingesta y Procesamiento de Datos Landsat

**Un pipeline ETL robusto y escalable para la ingesta de datos de la Colección 2 Nivel 2 de Landsat en una base de datos PostGIS.**

<p>
  <img src="https://img.shields.io/badge/Python-3.12-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/PostgreSQL-16.4-blue.svg" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/PostGIS-3.4-orange.svg" alt="PostGIS">
  <img src="https://img.shields.io/badge/Docker-Ready-blue.svg" alt="Docker">
  <img src="https://img.shields.io/badge/Orchestration-Airflow%20Ready-lightblue.svg" alt="Airflow Ready">
</p>

[Go to English Documentation](#english-documentation)

</div>

---

# Documentación en Español

**Tabla de Contenidos**

1.  [Visión General](#visión-general)
2.  [Arquitectura del Proyecto](#arquitectura-del-proyecto)
3.  [Modelo de Datos](#modelo-de-datos)
4.  [Flujo de Trabajo del ETL](#flujo-de-trabajo-del-etl)
5.  [Instalación y Configuración](#instalación-y-configuración)
6.  [Guía de Uso del CLI](#guía-de-uso-del-cli)
7.  [Documentación Adicional](#documentación-adicional)

## Visión General

Este proyecto implementa un pipeline ETL (Extracción, Transformación y Carga) diseñado para automatizar la descarga y el almacenamiento de datos de la **Colección 2, Nivel 2 de Landsat** desde la API M2M del USGS. Los datos de las bandas espectrales se ingieren como rasters en una base de datos **PostgreSQL** con la extensión **PostGIS**, utilizando un diseño de base de datos particionado para un rendimiento óptimo en consultas temporales.

El sistema es modular, configurable y está preparado para ser orquestado por herramientas como Apache Airflow, gracias a su punto de entrada CLI.

## Arquitectura del Proyecto

La estructura del proyecto está diseñada para separar responsabilidades (SoC), garantizando un código limpio, mantenible y escalable.

```
.
├── config/               # Configuración (YAML, GeoJSON)
├── data/                 # Datos temporales y logs (ignorados por git)
├── docs/                 # Documentación adicional
├── etl/                  # Código fuente del pipeline ETL
│   ├── bronze_ingestion.py
│   ├── m2m_client.py
│   ├── mtl_parser.py
│   └── utils.py
├── scripts/              # Scripts de utilidad
├── sql/                  # Scripts SQL (schemas)
├── .env                  # Variables de entorno (secretos)
├── .gitignore
├── main.py               # Punto de entrada CLI
├── README.md
└── requirements.txt      # Dependencias Python
```

-   **`/main.py`**: **Punto de Entrada Principal (Entry Point)**. Es la única interfaz de línea de comandos (CLI) que orquesta todo el proceso. Centraliza la ejecución y es ideal para ser llamado desde flujos de trabajo automatizados.

-   **`/etl/`**: **Paquete Core del ETL**. Contiene la lógica de negocio reutilizable del pipeline, funcionando como una librería interna.
    -   `bronze_ingestion.py`: El orquestador principal que gestiona el flujo de búsqueda, descarga e inserción.
    -   `m2m_client.py`: Un cliente dedicado para interactuar con la API M2M del USGS, manejando la autenticación, reintentos y peticiones.
    -   `mtl_parser.py`: Utilidad para parsear los archivos de metadatos (`_MTL.txt`) de las escenas Landsat.
    -   `utils.py`: Funciones de utilidad para cargar configuración, gestionar secretos, configurar logging y otras operaciones comunes.

-   **`/config/`**: **Configuración del Proyecto**.
    -   `landsat_config.yaml`: Archivo de configuración principal y única fuente de verdad para todos los parámetros no sensibles (bandas a descargar, URLs, umbrales, etc.).
    -   `roi_valencialake.geojson`: Un archivo GeoJSON que define el Área de Interés (AOI) para la búsqueda de escenas.

-   **`/.env`**: **Secretos y Variables de Entorno**. Este archivo, ignorado por Git, contiene **exclusivamente** información sensible: credenciales de la base de datos y tokens de API.

-   **`/sql/`**: **Infraestructura de Base de Datos**.
    -   `schemas/01_bronze_raster.sql`: Script DDL (Data Definition Language) que define y crea todo el esquema (`bronze`), las tablas, particiones por año, índices y vistas. Es la base para la reproducibilidad de la base de datos.

-   **`/scripts/`**: Contiene scripts de utilidad para diagnóstico o pruebas (`debug_m2m_download.py`).

-   **`/data/`**, **`/logs/`**: Carpetas de trabajo (rastreadas por Git a través de `.gitkeep` pero con su contenido ignorado) donde se guardan los archivos temporales descargados y los logs de ejecución, respectivamente.

## Modelo de Datos

La base de datos está diseñada para el almacenamiento eficiente de grandes volúmenes de datos geoespaciales-temporales.

-   **Esquema `bronze`**: Alberga los datos crudos o mínimamente procesados, siguiendo la primera etapa de una arquitectura Medallion.

### Tabla `landsat_scenes`
Almacena los metadatos de cada escena única.

| Columna            | Tipo                | Descripción                                                              |
|--------------------|---------------------|--------------------------------------------------------------------------|
| `scene_id`         | `SERIAL` (PK)       | Identificador único interno para cada escena.                            |
| `entity_id`        | `TEXT` (UNIQUE)     | ID de la entidad de USGS (ej. `LC09_L2SP_004053_20240125_20240126_02_T1`). |
| `acquisition_date` | `DATE`              | Fecha de captura de la escena.                                           |
| `sensor`           | `TEXT`              | Sensor que capturó la escena (ej. 'OLI', 'ETM+').                         |
| `cloud_cover`      | `REAL`              | Porcentaje de cobertura de nubes (0-100).                                |
| `footprint`        | `GEOMETRY(POLYGON)` | La huella geoespacial de la escena en coordenadas geográficas.            |

### Tabla `landsat_bands`
Almacena los datos raster de cada banda, teselados (en tiles) para optimizar el acceso.

| Columna     | Tipo                | Descripción                                                                 |
|-------------|---------------------|-----------------------------------------------------------------------------|
| `rid`       | `SERIAL` (PK part)  | Identificador único para cada tile de raster.                               |
| `scene_id`  | `INTEGER` (FK)      | Referencia a la escena a la que pertenece esta banda (`landsat_scenes`).    |
| `band_name` | `TEXT`              | Nombre de la banda (ej. 'SR_B3', 'QA_PIXEL').                               |
| `year`      | `INTEGER` (PK part) | Año de adquisición. **Clave de particionamiento**.                          |
| `rast`      | `RASTER`            | Los datos del píxel en formato PostGIS Raster.                              |
| `filename`  | `TEXT`              | Nombre del archivo original del cual se ingirió el raster.                  |

### Tabla `download_log`
Registro de auditoría para cada descarga realizada de archivos Landsat.

| Columna                      | Tipo         | Descripción                                                                       |
|------------------------------|--------------|-----------------------------------------------------------------------------------|
| `log_id`                     | `SERIAL` (PK) | Identificador único del registro de descarga.                                     |
| `entity_id`                  | `TEXT`       | ID de la entidad de USGS de la escena descargada.                                 |
| `band_name`                  | `TEXT`       | Nombre de la banda o producto descargado.                                         |
| `download_url`               | `TEXT`       | URL de descarga del archivo.                                                      |
| `download_status`            | `TEXT`       | Estado de la descarga ('pending', 'success', 'failed', 'skipped').                |
| `attempt_count`              | `INTEGER`    | Número de intentos de descarga.                                                   |
| `error_message`              | `TEXT`       | Mensaje de error si la descarga falló.                                            |
| `file_size_mb`               | `REAL`       | Tamaño del archivo descargado en MB.                                              |
| `download_duration_seconds`  | `REAL`       | Duración de la descarga en segundos.                                              |
| `created_at`                 | `TIMESTAMP`  | Marca de tiempo de creación del registro.                                         |
| `updated_at`                 | `TIMESTAMP`  | Marca de tiempo de la última actualización del registro.                          |

### Tabla `sensor_bands_config`
Configuración de las bandas requeridas para cada tipo de sensor, utilizada en la lógica del ETL y para funciones.

| Columna             | Tipo        | Descripción                                                                 |
|---------------------|-------------|-----------------------------------------------------------------------------|
| `sensor`            | `TEXT` (PK) | Nombre del sensor (ej. 'OLI', 'ETM+', 'TM').                                |
| `green_band`        | `TEXT`      | Nombre de la banda verde para este sensor.                                  |
| `swir_band`         | `TEXT`      | Nombre de la banda SWIR para este sensor.                                   |
| `qa_bands`          | `TEXT[]`    | Array de nombres de bandas QA requeridas para este sensor.                  |
| `date_range_start`  | `DATE`      | Fecha de inicio de validez de esta configuración (opcional).                |
| `date_range_end`    | `DATE`      | Fecha de fin de validez de esta configuración (opcional).                   |

### ¿Por qué una Tabla Particionada?
La tabla `landsat_bands` está **particionada por año**. Esta decisión de diseño es **fundamental para la escalabilidad** del sistema por varias razones:

1.  **Rendimiento de Consultas (Query Performance):** Cuando se realiza una consulta que filtra por un rango de fechas (ej. "analizar todas las bandas de 2023"), el planificador de consultas de PostgreSQL (Query Planner) es lo suficientemente inteligente como para escanear **únicamente** la partición `landsat_bands_2023`, ignorando por completo los datos de otros años. Esto se conoce como *Partition Pruning* y reduce drásticamente los tiempos de lectura.

2.  **Mantenimiento Eficiente:** La gestión del ciclo de vida de los datos se vuelve trivial. Si en el futuro se necesita borrar datos de hace 10 años, en lugar de ejecutar un costoso `DELETE FROM ... WHERE year = ...`, simplemente se puede ejecutar `DROP TABLE landsat_bands_2014;`. Esta operación es casi instantánea, no genera sobrecarga transaccional y no fragmenta los índices.

3.  **Carga de Datos Optimizada:** Permite estrategias de carga de datos más eficientes y la creación de índices por partición, lo que acelera tanto la escritura como la lectura.

## Flujo de Trabajo del ETL

El proceso se ejecuta de la siguiente manera al invocar `python main.py ingest`:

1.  **Inicio y Configuración**: `main.py` recibe los parámetros y llama a la lógica de ingesta. Se carga la configuración desde `config/landsat_config.yaml` y los secretos desde `.env`.
2.  **Búsqueda de Escenas**: `M2MClient` se autentica en la API del USGS y busca escenas que se intersecten con el AOI y cumplan con los filtros de fecha y nubes.
3.  **Descarga**: Las bandas de las escenas nuevas se descargan como archivos `.TIF` en la carpeta `data/temp/`.
4.  **Ingesta en BD (por cada banda)**:
    a.  **Tabla Temporal**: Se utiliza `raster2pgsql` para crear una tabla temporal con los datos del raster del archivo `.TIF`.
    b.  **Inserción**: Se ejecuta un `INSERT INTO ... SELECT FROM ...` para copiar los datos a la partición anual correcta (ej. `landsat_bands_2024`), inyectando metadatos.
    c.  **Limpieza**: La tabla temporal se elimina.
5.  **Finalización**: El proceso registra un resumen de la operación y limpia listas temporales.

## Instalación y Configuración

### Requisitos Previos

-   Python 3.12+
-   PostgreSQL 16.4+ con PostGIS 3.4+ (incluyendo `postgis_raster`).
-   Credenciales para la [API M2M de USGS](https://m2m.cr.usgs.gov/).
-   Opcional: Docker para una configuración de base de datos más sencilla.

### Pasos de Configuración

1.  **Clonar el Repositorio:**
    ```bash
    git clone https://github.com/chachr81/landsat_data.git
    cd landsat_data
    ```

2.  **Instalar Dependencias:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configurar la Base de Datos:**
    -   En tu base de datos, habilita las extensiones:
        ```sql
        CREATE EXTENSION postgis;
        CREATE EXTENSION postgis_raster;
        ```
    -   Ejecuta el script DDL para crear la estructura:
        ```bash
        psql -U tu_usuario -d tu_base_de_datos -h tu_host -f sql/schemas/01_bronze_raster.sql
        ```

4.  **Configurar Secretos (`.env`):**
    -   Crea un archivo `.env` en la raíz del proyecto y añade tus credenciales:
        ```dotenv
        POSTGRES_HOST=localhost
        POSTGRES_USER=postgres
        POSTGRES_PASSWORD=secret
        POSTGRES_DB=gis_engine
        POSTGRES_PORT=5432
        M2M_USERNAME=tu_usuario_m2m
        M2M_PASSWORD=tu_contraseña_m2m
        # M2M_API_KEY=your_api_key_if_used_instead_of_password
        ```

## Guía de Uso del CLI

El único punto de entrada es `main.py`.

### Comando: `ingest`

| Argumento     | Requerido | Descripción                                                                    |
|---------------|-----------|--------------------------------------------------------------------------------|
| `--start`     | Sí        | Fecha de inicio de la búsqueda (`YYYY-MM-DD`).                                 |
| `--end`       | Sí        | Fecha de fin de la búsqueda (`YYYY-MM-DD`).                                    |
| `--datasets`  | No        | Colecciones a procesar. Opciones leídas de `config.yaml`.                      |
| `--clouds`    | No        | Porcentaje máximo de nubes (0-100).                                            |
| `--dry-run`   | No        | Simula la ejecución sin descargar ni escribir en la BD.                        |
| `--log-level` | No        | Nivel de logging (`DEBUG`, `INFO`, `WARNING`).                                 |

**Ejemplos de Ejecución:**

```bash
# Ingestar datos para el primer trimestre de 2024
python main.py ingest --start 2024-01-01 --end 2024-04-01

# Ingestar solo datos de Landsat 7 con un máximo de 10% de nubes
python main.py ingest --start 2022-01-01 --end 2023-01-01 --datasets landsat_7 --clouds 10
```

### Comando: `cleanup-lists`

Mantiene la API M2M limpiando listas de escenas temporales.

| Argumento     | Requerido | Descripción                                                                    |
|---------------|-----------|--------------------------------------------------------------------------------|
| `--list-id`   | Sí        | ID(s) de las listas a borrar.                                                  |
| `--dry-run`   | No        | Simula el borrado.                                                             |
| `--force`     | No        | Omite la confirmación.                                                         |

**Ejemplo:**
```bash
python main.py cleanup-lists --list-id temp_list_12345 temp_list_67890
```

## Documentación Adicional

-   Para una descripción detallada de las bandas de calidad (QA) y cómo interpretarlas, consulta el siguiente documento:
    -   [**Guía de Bandas de Calidad de Landsat**](./docs/LANDSAT_QA_BANDS.md)

---

# English Documentation

**Table of Contents**

1.  [Overview](#overview)
2.  [Project Architecture](#project-architecture)
3.  [Data Model](#data-model)
4.  [ETL Workflow](#etl-workflow)
5.  [Installation and Setup](#installation-and-setup)
6.  [CLI Usage Guide](#cli-usage-guide)
7.  [Additional Documentation](#additional-documentation)

## Overview

This project implements an ETL (Extract, Transform, Load) pipeline designed to automate the download and storage of **Landsat Collection 2, Level 2** data from the USGS M2M API. The spectral band data is ingested as rasters into a **PostgreSQL** database with the **PostGIS** extension, using a partitioned database design for optimal performance on temporal queries.

The system is modular, configurable, and ready to be orchestrated by tools like Apache Airflow, thanks to its CLI entry point.

## Project Architecture

The project structure is designed to separate concerns (SoC), ensuring clean, maintainable, and scalable code.

```
.
├── config/               # Configuration (YAML, GeoJSON)
├── data/                 # Temporary data and logs (ignored by git)
├── docs/                 # Additional documentation
├── etl/                  # ETL pipeline source code
│   ├── bronze_ingestion.py
│   ├── m2m_client.py
│   ├── mtl_parser.py
│   └── utils.py
├── scripts/              # Utility scripts
├── sql/                  # SQL scripts (schemas)
├── .env                  # Environment variables (secrets)
├── .gitignore
├── main.py               # CLI Entry Point
├── README.md
└── requirements.txt      # Python dependencies
```

-   **`/main.py`**: **Main Entry Point**. A CLI that orchestrates the entire process. All operations, such as data ingestion, are initiated from here. It is the only script that needs to be run.

-   **`/etl/`**: **Core ETL Package**. Contains the reusable business logic of the pipeline, functioning as an internal library.
    -   `bronze_ingestion.py`: The main orchestrator that manages the search, download, and insertion flow.
    -   `m2m_client.py`: A dedicated client for interacting with the USGS M2M API, handling authentication, retries, and requests.
    -   `mtl_parser.py`: A utility for parsing metadata files (`_MTL.txt`) from Landsat scenes.
    -   `utils.py`: Utility functions for loading configuration, managing secrets, setting up logging, and other common operations.

-   **`/config/`**: **Project Configuration**.
    -   `landsat_config.yaml`: The main configuration file and single source of truth for all non-sensitive parameters (bands to download, URLs, thresholds, etc.).
    -   `roi_valencialake.geojson`: A GeoJSON file defining the Area of Interest (AOI) for scene searches.

-   **`/.env`**: **Secrets and Environment Variables**. This file, ignored by Git, **exclusively** contains sensitive information: database credentials and API tokens.

-   **`/sql/`**: **Database Infrastructure**.
    -   `schemas/01_bronze_raster.sql`: DDL (Data Definition Language) script that defines and creates the entire schema (`bronze`), tables, year-based partitions, indexes, and views. It is the foundation for database reproducibility.

-   **`/scripts/`**: Contains utility scripts for diagnostics or testing (`debug_m2m_download.py`).

-   **`/data/`**, **`/logs/`**: Working directories (tracked by Git via `.gitkeep` but their content is ignored) where temporary downloaded files and execution logs are stored, respectively.

## Data Model

The database is designed for the efficient storage of large volumes of geospatial-temporal data.

-   **`bronze` Schema**: Houses the "raw" or minimally processed data, following the first stage of a Medallion architecture.

### `landsat_scenes` Table
Stores metadata for each unique scene.

| Column             | Type                | Description                                                                 |
|--------------------|---------------------|-----------------------------------------------------------------------------|
| `scene_id`         | `SERIAL` (PK)       | Internal unique identifier for each scene.                                  |
| `entity_id`        | `TEXT` (UNIQUE)     | USGS entity ID (e.g., `LC09_L2SP_004053_20240125_20240126_02_T1`).          |
| `acquisition_date` | `DATE`              | Date the scene was captured.                                                |
| `sensor`           | `TEXT`              | Sensor that captured the scene (e.g., 'OLI', 'ETM+').                        |
| `cloud_cover`      | `REAL`              | Cloud cover percentage (0-100).                                             |
| `footprint`        | `GEOMETRY(POLYGON)` | The geospatial footprint of the scene in geographic coordinates.            |

### `landsat_bands` Table
Stores raster data for each band, tiled for optimized access.

| Column      | Type                | Description                                                                 |
|-------------|---------------------|-----------------------------------------------------------------------------|
| `rid`       | `SERIAL` (PK part)  | Unique identifier for each raster tile.                                     |
| `scene_id`  | `INTEGER` (FK)      | Reference to the scene this band belongs to (`landsat_scenes`).             |
| `band_name` | `TEXT`              | Name of the band (e.g., 'SR_B3', 'QA_PIXEL').                               |
| `year`      | `INTEGER` (PK part) | Acquisition year. **Partitioning Key**.                                     |
| `rast`      | `RASTER`            | The pixel data in PostGIS Raster format.                                    |
| `filename`  | `TEXT`              | Original filename from which the raster was ingested.                       |

### `download_log` Table
Audit log for each Landsat file download performed.

| Column                      | Type         | Description                                                                       |
|-----------------------------|--------------|-----------------------------------------------------------------------------------|
| `log_id`                    | `SERIAL` (PK) | Unique identifier for the download record.                                        |
| `entity_id`                 | `TEXT`       | USGS entity ID of the downloaded scene.                                           |
| `band_name`                 | `TEXT`       | Name of the band or product downloaded.                                           |
| `download_url`              | `TEXT`       | URL of the file download.                                                         |
| `download_status`           | `TEXT`       | Status of the download ('pending', 'success', 'failed', 'skipped').               |
| `attempt_count`             | `INTEGER`    | Number of download attempts.                                                      |
| `error_message`             | `TEXT`       | Error message if the download failed.                                             |
| `file_size_mb`              | `REAL`       | Size of the downloaded file in MB.                                                |
| `download_duration_seconds` | `REAL`       | Duration of the download in seconds.                                              |
| `created_at`                | `TIMESTAMP`  | Timestamp of record creation.                                                     |
| `updated_at`                | `TIMESTAMP`  | Timestamp of last record update.                                                  |

### `sensor_bands_config` Table
Configuration of required bands for each sensor type, used in ETL logic and functions.

| Column             | Type        | Description                                                                 |
|--------------------|-------------|-----------------------------------------------------------------------------|
| `sensor`           | `TEXT` (PK) | Sensor name (e.g., 'OLI', 'ETM+', 'TM').                                    |
| `green_band`       | `TEXT`      | Name of the green band for this sensor.                                     |
| `swir_band`        | `TEXT`      | Name of the SWIR band for this sensor.                                      |
| `qa_bands`         | `TEXT[]`    | Array of required QA band names for this sensor.                            |
| `date_range_start` | `DATE`      | Start date of validity for this configuration (optional).                   |
| `date_range_end`   | `DATE`      | End date of validity for this configuration (optional).                     |

### Why a Partitioned Table?
The `landsat_bands` table is **partitioned by year**. This design decision is **fundamental for the system's scalability** for several reasons:

1.  **Query Performance:** When a query filters by a date range (e.g., "analyze all bands from 2023"), the PostgreSQL Query Planner is smart enough to scan **only** the `landsat_bands_2023` partition, completely ignoring data from other years. This is known as *Partition Pruning* and dramatically reduces read times.

2.  **Efficient Maintenance:** Data lifecycle management becomes trivial. If, in the future, you need to delete data from 10 years ago, instead of running a costly `DELETE FROM ... WHERE year = ...`, you can simply execute `DROP TABLE landsat_bands_2014;`. This operation is nearly instantaneous, generates no transactional overhead, and does not fragment indexes.

3.  **Optimized Data Loading:** It allows for more efficient data loading strategies and the creation of per-partition indexes, speeding up both writes and reads.

## ETL Workflow

The process runs as follows when `python main.py ingest` is invoked:

1.  **Start and Configuration**: `main.py` receives parameters and calls the ingestion logic. Configuration is loaded from `config/landsat_config.yaml` and secrets from `.env`.
2.  **Scene Search**: `M2MClient` authenticates with the USGS API and searches for scenes intersecting the AOI and meeting the date/cloud filters.
3.  **Download**: Bands from new scenes are downloaded as `.TIF` files into the `data/temp/` folder.
4.  **DB Ingestion (for each band)**:
    a.  **Temporary Table**: `raster2pgsql` is used to create a unique temporary table with the raster data from the `.TIF` file.
    b.  **Insertion**: An `INSERT INTO ... SELECT FROM ...` command copies data from the temp table to the correct annual partition (e.g., `landsat_bands_2024`), injecting key metadata.
    c.  **Cleanup**: The temporary table is deleted.
5.  **Completion**: The process logs a summary of the operation and cleans up temporary files and M2M lists.

## Installation and Setup

### Prerequisites

-   Python 3.12+
-   PostgreSQL 16.4+ with PostGIS 3.4+ (including `postgis_raster`).
-   Credentials for the [USGS M2M API](https://m2m.cr.usgs.gov/).
-   Optional: Docker for a simpler database setup.

### Setup Steps

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/chachr81/landsat_data.git
    cd landsat_data
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set Up the Database:**
    -   In your database, enable the extensions:
        ```sql
        CREATE EXTENSION postgis;
        CREATE EXTENSION postgis_raster;
        ```
    -   Run the DDL script to create the structure:
        ```bash
        psql -U your_user -d your_database -h your_host -f sql/schemas/01_bronze_raster.sql
        ```

4.  **Configure Secrets (`.env`):**
    -   Create a `.env` file in the project root and add your credentials:
        ```dotenv
        POSTGRES_HOST=localhost
        POSTGRES_USER=postgres
        POSTGRES_PASSWORD=secret
        POSTGRES_DB=gis_engine
        POSTGRES_PORT=5432
        M2M_USERNAME=your_m2m_username
        M2M_PASSWORD=your_m2m_password
        # M2M_API_KEY=your_api_key_if_used_instead_of_password
        ```

## CLI Usage Guide

The single entry point is `main.py`.

### Command: `ingest`

| Argument      | Required | Description                                                    |
|---------------|----------|----------------------------------------------------------------|
| `--start`     | Yes      | Search start date (`YYYY-MM-DD`).                              |
| `--end`       | Yes      | Search end date (`YYYY-MM-DD`).                                |
| `--datasets`  | No       | Collections to process. Options are read from `config.yaml`.   |
| `--clouds`    | No       | Maximum cloud cover percentage (0-100).                        |
| `--dry-run`   | No       | Simulates the run without downloading or writing to the DB.    |
| `--log-level` | No       | Logging level (`DEBUG`, `INFO`, `WARNING`).                    |

**Example Executions:**

```bash
# Ingest data for the first quarter of 2024
python main.py ingest --start 2024-01-01 --end 2024-04-01

# Ingest only Landsat 7 data with a max of 10% cloud cover
python main.py ingest --start 2022-01-01 --end 2023-01-01 --datasets landsat_7 --clouds 10
```

### Command: `cleanup-lists`

Maintains the M2M API by removing old temporary scene lists.

| Argument      | Required | Description                                                    |
|---------------|----------|----------------------------------------------------------------|
| `--list-id`   | Yes      | One or more M2M List IDs to delete.                            |
| `--dry-run`   | No       | Simulates deletion.                                            |
| `--force`     | No       | Skips confirmation prompt.                                     |

**Example:**
```bash
python main.py cleanup-lists --list-id temp_list_12345 temp_list_67890
```

## Additional Documentation



-   For a detailed description of the Quality Assessment (QA) bands and how to interpret them, refer to the following document:

    -   [**Guide to Landsat Quality Assessment Bands**](./docs/LANDSAT_QA_BANDS.md)
