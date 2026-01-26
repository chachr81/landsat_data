# Análisis de Paisaje con Spark y Sedona

Este repositorio contiene un cuaderno de Jupyter que demuestra un pipeline completo para calcular métricas de paisaje a partir de datos de cobertura del suelo, utilizando Apache Spark, Apache Sedona y PostGIS.

El objetivo es servir como un proyecto de portafolio, mostrando un flujo de trabajo de datos geoespaciales distribuido, desde la ingesta y el procesamiento hasta el análisis.

## Agradecimientos y Fuentes de Datos

Este proyecto utiliza datos del **Proyecto MapBiomas Chile**, una iniciativa de la Red Colaborativa de Mapeo del Territorio (Red Map) para generar mapas anuales de cobertura y uso del suelo para todo Chile.

- **Sitio Web Oficial:** [chile.mapbiomas.org](https://chile.mapbiomas.org/)
- **Plataforma de Datos:** [plataforma.chile.mapbiomas.org](https://plataforma.chile.mapbiomas.org/)

La documentación oficial de la leyenda de clases utilizada en este análisis se encuentra en `docs/leyenda_description.pdf`.

## Ventajas de la Arquitectura Utilizada

La elección de una pila tecnológica basada en **Apache Spark, Sedona y PostGIS** no es casual. Este enfoque ofrece ventajas significativas para el análisis geoespacial a gran escala en comparación con métodos más tradicionales.

### Contra Software GIS de Escritorio (ej. QGIS, ArcGIS) + PostGIS

- **Escalabilidad y Rendimiento:** El software de escritorio está limitado por la memoria RAM y la CPU de una sola máquina. Spark, en cambio, está diseñado para la computación distribuida. Puede escalar horizontalmente, dividiendo la carga de trabajo entre múltiples núcleos o incluso un clúster de máquinas, lo que permite procesar datasets de escala nacional o continental que serían díficiles en un entorno de escritorio sin un buen equipo (en ocasiones GPU).
- **Automatización y Reproducibilidad:** Un flujo de trabajo basado en código, como este notebook, es 100% programable, automatizable y reproducible. Los análisis realizados en un GIS de escritorio a menudo dependen de pasos manuales que son difíciles de documentar y propensos a errores humanos.

### Contra un Stack de Python Tradicional (PostGIS + Rasterio + GeoPandas)

- **Procesamiento Distribuido vs. Mononúcleo:** Aunque librerías como Rasterio y GeoPandas son excelentes, operan en un solo nodo y, a menudo, en un solo núcleo. Al enfrentarse a miles de teselas ráster, un script de Python tradicional se convierte en un cuello de botella, procesando los datos de forma secuencial. Spark y Sedona paralelizan esta carga de forma nativa.
- **Optimización de I/O y Memoria:** Sedona introduce `SpatialRDDs` (Resilient Distributed Datasets), que son particionados espacialmente. Esto significa que los datos que están geográficamente cerca se mantienen juntos, optimizando los joins y operaciones espaciales al minimizar el movimiento de datos (`shuffle`) entre los nodos de Spark. Un script de Python manual tendría que gestionar la carga, procesamiento y liberación de memoria de cada archivo de forma individual, un proceso mucho menos eficiente.
- **Flexibilidad y Potencia Combinadas:** Este enfoque combina lo mejor de dos mundos:
    - La **potencia de Spark** para la distribución y el manejo de grandes volúmenes de datos.
    - La **riqueza del ecosistema científico de Python** a través de las UDFs de Pandas. En este notebook, por ejemplo, se utiliza `scipy.ndimage.label` para un análisis de fragmentación complejo. Implementar algoritmos de este tipo directamente en SQL de PostGIS sería extremadamente difícil, mientras que ejecutarlos en un script de Python mononúcleo sería muy lento.

En resumen, esta arquitectura permite realizar análisis complejos sobre datasets masivos de una manera que es escalable, eficiente y reproducible.

## Estructura del Repositorio

```
.
├── .env.example              # Ejemplo de variables de entorno para la conexión a la BD
├── .gitignore                # Configurado para incluir solo los archivos del showcase
├── README_NOTEBOOK.md        # Este archivo
├── data/
│   └── db_backup.dump        # Backup comprimido de la base de datos (manejado por Git LFS)
├── docs/
│   └── leyenda_description.pdf # Documentación oficial de la leyenda de MapBiomas
├── environment.yml             # Archivo de dependencias para Conda
├── libs/
│   └── postgresql-42.6.0.jar # Driver JDBC de PostgreSQL (manejado por Git LFS)
├── notebook/
│   └── sedona_testing_landcover_metrics.ipynb # El cuaderno principal del análisis
└── requirements.txt          # Archivo de dependencias para Pip
```

## Guía de Instalación y Ejecución

Para replicar este análisis, sigue los pasos a continuación según tu entorno preferido (Conda o Pip).

### Prerrequisitos

1.  **Git LFS:** Este repositorio utiliza Git Large File Storage (LFS). Debes tenerlo instalado. Puedes descargarlo desde [git-lfs.github.com](https://git-lfs.github.com/).
2.  **PostgreSQL y PostGIS:** Necesitas una instancia de PostgreSQL con la extensión PostGIS habilitada y segpun tú versión de Postgis seguir los pasos de https://postgis.net/docs/manual-3.5/es/postgis_gdal_enabled_drivers.html. Se recomienda usar Docker para una configuración rápida se puede usar la imagen oficial de PostGIS o pudes usar mi stack https://github.com/chachr81/gis-engine.
3.  **Clonar el Repositorio:**
    ```bash
    # Clona el repositorio y descarga los archivos de LFS
    git lfs clone https://github.com/chachr81/spark-sedona-landscape-analysis.git
    cd spark-sedona-landscape-analysis
    ```

### 1. Configuración de la Base de Datos

1.  **Crea un archivo `.env`** a partir del `.env.example` y rellena las credenciales de tu base de datos.
2.  **Restaura el backup:** El siguiente comando utiliza `pg_restore` para cargar los datos y la estructura de las tablas. Asegúrate de que las credenciales en tu `.env` sean correctas.

    ```bash
    # (Opcional) Lee las variables del archivo .env si no las has exportado
    export $(cat .env | xargs)

    # Restaura el backup en formato custom de Postgres
    pg_restore -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -p $POSTGRES_PORT -Fc --clean --if-exists data/db_backup.dump
    ```

### 2. Configuración del Entorno (Opción A: Conda - Recomendado)

Esta es la forma más robusta de asegurar que todas las dependencias, incluidas las geoespaciales complejas, funcionen correctamente.

1.  **Crea el entorno de Conda:**
    ```bash
    conda env create -f environment.yml
    ```
2.  **Activa el entorno:**
    ```bash
    conda activate gis-notebook
    ```
3.  **Inicia Jupyter Lab:**
    ```bash
    jupyter lab
    ```

### 3. Configuración del Entorno (Opción B: Pip)

Si prefieres usar Pip, puedes seguir estos pasos. **Nota:** podrías necesitar instalar dependencias del sistema operativo manualmente (como `libgdal-dev`).

1.  **Crea y activa un entorno virtual:**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```
2.  **Instala las dependencias:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Inicia Jupyter Lab:**
    ```bash
    jupyter lab
    ```

### 4. Nota sobre Google Colab

Ejecutar este pipeline en Google Colab **no es un proceso directo y no se recomienda** para este proyecto. La razón es la complejidad de configurar el entorno de Java, Apache Spark, y las dependencias de Sedona, además de la necesidad de conectar de forma estable a una base de datos PostGIS externa. El entorno de Colab está más orientado a flujos de trabajo de Machine Learning que no requieren una pila de software tan específica.

### 5. Ejecución del Notebook

Una vez que tu entorno y base de datos estén configurados, abre el archivo `notebook/sedona_testing_landcover_metrics.ipynb` desde Jupyter Lab y ejecuta las celdas en orden.
