-- =====================================================
-- BRONZE LAYER SCHEMA - LANDSAT COLLECTION 2 RASTERS
-- =====================================================
-- Propósito: Almacenar bandas espectrales y QA como rasters en PostGIS
-- Estrategia: Tablas particionadas por año para optimizar queries temporales
-- Tiling: 512x512 píxeles para balance entre I/O y memoria
-- SRID: 32618 (UTM Zone 18N - Venezuela) para reducir distorsión

-- Crear schema si no existe
CREATE SCHEMA IF NOT EXISTS bronze;

-- =====================================================
-- TABLA MAESTRA: Metadatos de Escenas Landsat
-- =====================================================
-- Almacena información de cada escena descargada
CREATE TABLE IF NOT EXISTS bronze.landsat_scenes (
    scene_id SERIAL PRIMARY KEY,
    entity_id TEXT UNIQUE NOT NULL,  -- ID único de USGS (ej: LC08_L2SP_005054_20240115_02_T1)
    display_id TEXT NOT NULL,         -- DisplayID humano legible
    dataset_name TEXT NOT NULL,       -- 'landsat_ot_c2_l2', 'landsat_tm_c2_l2', etc.
    sensor TEXT NOT NULL,             -- 'OLI', 'ETM+', 'TM'
    satellite TEXT NOT NULL,          -- 'LANDSAT_8', 'LANDSAT_7', etc.
    acquisition_date DATE NOT NULL,   -- Fecha de captura
    path_row TEXT NOT NULL,           -- Path/Row de Landsat (ej: '005/054')
    cloud_cover REAL,                 -- Porcentaje de cobertura de nubes
    sun_azimuth REAL,                 -- Azimut solar (para correcciones)
    sun_elevation REAL,               -- Elevación solar
    processing_level TEXT,            -- 'L2SP', 'L2SR'
    download_date TIMESTAMP DEFAULT NOW(),
    footprint GEOMETRY(POLYGON, 4326), -- Huella espacial de la escena
    
    -- Índices
    CONSTRAINT check_cloud_cover CHECK (cloud_cover >= 0 AND cloud_cover <= 100)
);

-- Índices espaciales y temporales
CREATE INDEX IF NOT EXISTS idx_scenes_acquisition ON bronze.landsat_scenes (acquisition_date);
CREATE INDEX IF NOT EXISTS idx_scenes_sensor ON bronze.landsat_scenes (sensor);
CREATE INDEX IF NOT EXISTS idx_scenes_footprint ON bronze.landsat_scenes USING GIST (footprint);
CREATE INDEX IF NOT EXISTS idx_scenes_path_row ON bronze.landsat_scenes (path_row);

-- =====================================================
-- TABLA PARTICIONADA: Bandas Ráster
-- =====================================================
-- Almacena las bandas espectrales como RASTER de PostGIS
-- Particionada por año para optimizar queries temporales

-- Tabla padre (no almacena datos directamente)
CREATE TABLE IF NOT EXISTS bronze.landsat_bands (
    rid SERIAL,
    scene_id INTEGER REFERENCES bronze.landsat_scenes(scene_id) ON DELETE CASCADE,
    band_name TEXT,          -- 'SR_B3', 'SR_B6', 'QA_PIXEL', etc.
    year INTEGER NOT NULL,            -- Año de adquisición (para particionamiento)
    rast RASTER,                      -- Datos ráster (tiled 512x512)
    filename TEXT,                    -- Nombre del archivo original
    loaded_at TIMESTAMP DEFAULT NOW(),
    
    -- Constraint de partición
    PRIMARY KEY (rid, year)
) PARTITION BY RANGE (year);

-- Crear índices en la tabla padre (se heredarán en particiones)
CREATE INDEX IF NOT EXISTS idx_bands_scene ON bronze.landsat_bands (scene_id);
CREATE INDEX IF NOT EXISTS idx_bands_name ON bronze.landsat_bands (band_name);
CREATE INDEX IF NOT EXISTS idx_bands_rast ON bronze.landsat_bands USING GIST (ST_ConvexHull(rast));

-- =====================================================
-- PARTICIONES POR AÑO (1986-2026)
-- =====================================================
-- Generar particiones para cada año del período de análisis
-- Nota: En producción, estas se pueden crear dinámicamente

DO $$
DECLARE
    year_val INTEGER;
BEGIN
    FOR year_val IN 1986..2026 LOOP
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS bronze.landsat_bands_%s 
            PARTITION OF bronze.landsat_bands
            FOR VALUES FROM (%s) TO (%s);
        ', year_val, year_val, year_val + 1);
        
        -- Índice espacial por partición (mejora rendimiento)
        EXECUTE format('
            CREATE INDEX IF NOT EXISTS idx_bands_%s_rast 
            ON bronze.landsat_bands_%s USING GIST (ST_ConvexHull(rast));
        ', year_val, year_val);
    END LOOP;
END $$;

-- =====================================================
-- TABLA: Registro de Descargas (Log)
-- =====================================================
-- Auditoría de descargas para evitar duplicados y tracking
CREATE TABLE IF NOT EXISTS bronze.download_log (
    log_id SERIAL PRIMARY KEY,
    entity_id TEXT NOT NULL,
    band_name TEXT NOT NULL,
    download_url TEXT,
    download_status TEXT CHECK (download_status IN ('pending', 'success', 'failed', 'skipped')),
    attempt_count INTEGER DEFAULT 1,
    error_message TEXT,
    file_size_mb REAL,
    download_duration_seconds REAL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_download_log_entity ON bronze.download_log (entity_id, band_name);
CREATE INDEX IF NOT EXISTS idx_download_log_status ON bronze.download_log (download_status);

-- =====================================================
-- TABLA: Configuración de Bandas por Sensor
-- =====================================================
-- Define qué bandas descargar para cada sensor
CREATE TABLE IF NOT EXISTS bronze.sensor_bands_config (
    sensor TEXT PRIMARY KEY,
    green_band TEXT NOT NULL,        -- Banda verde
    swir_band TEXT NOT NULL,         -- Banda SWIR
    qa_bands TEXT[] NOT NULL,        -- Bandas QA requeridas
    date_range_start DATE,
    date_range_end DATE
);

-- Insertar configuración estándar
INSERT INTO bronze.sensor_bands_config (sensor, green_band, swir_band, qa_bands, date_range_start, date_range_end)
VALUES 
    ('OLI', 'SR_B3', 'SR_B6', ARRAY['QA_PIXEL', 'QA_RADSAT', 'QA_AEROSOL'], '2013-04-11', NULL),
    ('ETM+', 'SR_B2', 'SR_B5', ARRAY['QA_PIXEL', 'QA_RADSAT'], '1999-05-28', NULL),
    ('TM', 'SR_B2', 'SR_B5', ARRAY['QA_PIXEL', 'QA_RADSAT'], '1984-03-01', '2013-06-05')
ON CONFLICT (sensor) DO NOTHING;

-- =====================================================
-- VISTA: Inventario de Bandas Disponibles
-- =====================================================
-- Vista para consultar rápidamente qué bandas están disponibles por escena
CREATE OR REPLACE VIEW bronze.v_bands_inventory AS
SELECT 
    s.entity_id,
    s.display_id,
    s.sensor,
    s.acquisition_date,
    s.cloud_cover,
    b.band_name,
    COUNT(b.rid) AS tile_count,
    ST_Union(ST_ConvexHull(b.rast)) AS band_extent,
    SUM(ST_MemSize(b.rast)) / 1024.0 / 1024.0 AS total_size_mb
FROM bronze.landsat_scenes s
JOIN bronze.landsat_bands b ON s.scene_id = b.scene_id
GROUP BY s.entity_id, s.display_id, s.sensor, s.acquisition_date, s.cloud_cover, b.band_name
ORDER BY s.acquisition_date DESC, b.band_name;

-- =====================================================
-- FUNCIÓN: Obtener Bandas para MNDWI
-- =====================================================
-- Función auxiliar para obtener las bandas Green y SWIR de una escena
-- considerando el tipo de sensor
CREATE OR REPLACE FUNCTION bronze.get_mndwi_bands(p_entity_id TEXT)
RETURNS TABLE (
    green_band_rast RASTER,
    swir_band_rast RASTER,
    qa_pixel_rast RASTER,
    sensor TEXT
) AS $$
DECLARE
    v_sensor TEXT;
    v_green_name TEXT;
    v_swir_name TEXT;
BEGIN
    -- Obtener sensor de la escena
    SELECT s.sensor INTO v_sensor
    FROM bronze.landsat_scenes s
    WHERE s.entity_id = p_entity_id;
    
    -- Obtener nombres de bandas según sensor
    SELECT c.green_band, c.swir_band INTO v_green_name, v_swir_name
    FROM bronze.sensor_bands_config c
    WHERE c.sensor = v_sensor;
    
    -- Retornar las bandas
    RETURN QUERY
    SELECT 
        MAX(CASE WHEN b.band_name = v_green_name THEN b.rast END) AS green_band_rast,
        MAX(CASE WHEN b.band_name = v_swir_name THEN b.rast END) AS swir_band_rast,
        MAX(CASE WHEN b.band_name = 'QA_PIXEL' THEN b.rast END) AS qa_pixel_rast,
        v_sensor AS sensor
    FROM bronze.landsat_bands b
    JOIN bronze.landsat_scenes s ON b.scene_id = s.scene_id
    WHERE s.entity_id = p_entity_id
    GROUP BY s.scene_id;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- COMENTARIOS Y DOCUMENTACIÓN
-- =====================================================
COMMENT ON SCHEMA bronze IS 'Capa Bronze: Datos crudos de Landsat Collection 2 Level-2';
COMMENT ON TABLE bronze.landsat_scenes IS 'Metadatos de escenas Landsat descargadas';
COMMENT ON TABLE bronze.landsat_bands IS 'Bandas espectrales almacenadas como PostGIS Raster (particionado por año)';
COMMENT ON TABLE bronze.download_log IS 'Registro de auditoría de descargas M2M';
COMMENT ON TABLE bronze.sensor_bands_config IS 'Configuración de mapeo de bandas por tipo de sensor';
COMMENT ON VIEW bronze.v_bands_inventory IS 'Inventario de bandas disponibles por escena';

-- =====================================================
-- PERMISOS (ajustar según tu configuración)
-- =====================================================
-- GRANT USAGE ON SCHEMA bronze TO your_read_user;
-- GRANT SELECT ON ALL TABLES IN SCHEMA bronze TO your_read_user;
-- GRANT SELECT ON ALL SEQUENCES IN SCHEMA bronze TO your_read_user;

-- =====================================================
-- FINALIZADO
-- =====================================================
-- Verificar creación
\echo 'Bronze schema creado exitosamente'
\echo 'Verificando particiones creadas...'
SELECT 
    schemaname, 
    tablename 
FROM pg_tables 
WHERE schemaname = 'bronze' 
    AND tablename LIKE 'landsat_bands_%'
ORDER BY tablename;
