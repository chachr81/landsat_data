-- =====================================================
-- SCHEMAS: aculeo_clear y aculeo_metricas
-- PoC Monitoreo Laguna de Aculeo
-- =====================================================
-- aculeo_clear   : rasters de indices espectrales (MNDWI, NDWI)
--                  ya con QA aplicado y recortados al AOI (sscuenca_id=411)
-- aculeo_metricas: metricas de area de agua y validacion estadistica
-- =====================================================

CREATE SCHEMA IF NOT EXISTS aculeo_clear;
CREATE SCHEMA IF NOT EXISTS aculeo_metricas;

-- =====================================================
-- ACULEO_CLEAR: Rasters de indices espectrales
-- =====================================================
-- Cada fila es un indice espectral calculado para una
-- escena y una cuenca. El raster tiene valores en [-1,1]
-- con NODATA donde QA es invalido (nubes, saturacion, etc.)

CREATE TABLE IF NOT EXISTS aculeo_clear.spectral_indices (
    rid                SERIAL,
    scene_id           INTEGER NOT NULL REFERENCES aculeo_raw.landsat_scenes(scene_id) ON DELETE CASCADE,
    sscuenca_id        INTEGER NOT NULL,
    index_type         TEXT NOT NULL,        -- 'MNDWI', 'NDWI'
    year               INTEGER NOT NULL,
    rast               RASTER,               -- valores del indice con NODATA donde QA invalido
    valid_pixel_ratio  REAL,                 -- fraccion de pixeles validos tras QA
    processed_at       TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (rid, year)
) PARTITION BY RANGE (year);

CREATE INDEX IF NOT EXISTS idx_clear_scene      ON aculeo_clear.spectral_indices (scene_id);
CREATE INDEX IF NOT EXISTS idx_clear_cuenca     ON aculeo_clear.spectral_indices (sscuenca_id);
CREATE INDEX IF NOT EXISTS idx_clear_index_type ON aculeo_clear.spectral_indices (index_type);

CREATE INDEX IF NOT EXISTS idx_clear_scene_index
    ON aculeo_clear.spectral_indices (scene_id, sscuenca_id, index_type, year);

DO $$
DECLARE yr INTEGER;
BEGIN
    FOR yr IN 2013..2026 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS aculeo_clear.spectral_indices_%s
             PARTITION OF aculeo_clear.spectral_indices FOR VALUES FROM (%s) TO (%s);',
            yr, yr, yr + 1
        );
    END LOOP;
END $$;

-- =====================================================
-- ACULEO_CLEAR: Funcion de recorte de bandas crudas
-- Usada por el procesador Python (clear_processor.py)
-- =====================================================
CREATE OR REPLACE FUNCTION aculeo_clear.get_raw_bands_clipped(
    p_sscuenca_id INTEGER,
    p_scene_id    INTEGER
)
RETURNS TABLE (band_name TEXT, clipped_rast RASTER) AS $$
BEGIN
    RETURN QUERY
    WITH raster_srid AS (
        SELECT DISTINCT ST_SRID(rast) AS srid
        FROM aculeo_raw.landsat_bands
        WHERE scene_id = p_scene_id
        LIMIT 1
    ),
    cuenca_geom AS (
        SELECT ST_Transform(geometria, rs.srid) AS geom
        FROM cuencas.dga_subsub_cuenca, raster_srid rs
        WHERE sscuenca_id = p_sscuenca_id
    ),
    tiles_aoi AS (
        SELECT b.band_name, b.rast
        FROM aculeo_raw.landsat_bands b
        CROSS JOIN cuenca_geom c
        WHERE b.scene_id = p_scene_id
          AND ST_Intersects(b.rast, c.geom)
    )
    SELECT
        t.band_name,
        ST_Union(ST_Clip(t.rast, c.geom, TRUE)) AS clipped_rast
    FROM tiles_aoi t
    CROSS JOIN cuenca_geom c
    GROUP BY t.band_name, c.geom;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- ACULEO_CLEAR: Vista de estado de procesamiento
-- =====================================================
CREATE OR REPLACE VIEW aculeo_clear.v_estado_procesamiento AS
SELECT
    s.scene_id,
    s.entity_id,
    s.acquisition_date,
    s.sensor,
    s.cloud_cover,
    COUNT(DISTINCT ci.index_type)    AS indices_calculados,
    MAX(ci.valid_pixel_ratio)        AS max_valid_ratio,
    BOOL_OR(ci.rid IS NOT NULL)      AS procesado
FROM aculeo_raw.landsat_scenes s
LEFT JOIN aculeo_clear.spectral_indices ci
    ON s.scene_id = ci.scene_id AND ci.sscuenca_id = 411
GROUP BY s.scene_id, s.entity_id, s.acquisition_date, s.sensor, s.cloud_cover
ORDER BY s.acquisition_date;

-- =====================================================
-- ACULEO_METRICAS: Metricas de area de agua
-- =====================================================
CREATE TABLE IF NOT EXISTS aculeo_metricas.water_metrics (
    metric_id                    SERIAL,
    sscuenca_id                  INTEGER NOT NULL,
    scene_id                     INTEGER REFERENCES aculeo_raw.landsat_scenes(scene_id) ON DELETE CASCADE,
    acquisition_date             DATE NOT NULL,
    year                         INTEGER NOT NULL,
    sensor                       TEXT NOT NULL,
    water_index_type             TEXT NOT NULL,        -- 'MNDWI', 'NDWI'

    -- Area del espejo de agua
    total_water_area_km2         REAL,
    water_pixels_count           INTEGER,

    -- Morfologia del cuerpo de agua detectado
    n_water_components           INTEGER,              -- componentes conectados clasificados como agua
    main_component_compactness   REAL,                 -- compacidad del componente principal (0-1)

    -- Estadisticas del indice espectral
    mndwi_threshold_used         REAL,                 -- umbral GMM aplicado
    scene_index_median           REAL,                 -- mediana del indice en la escena
    mndwi_water_mean             REAL,                 -- valor medio del indice sobre pixeles de agua
    mndwi_water_std              REAL,                 -- desviacion estandar del indice en agua
    bimodality_coefficient       REAL,                 -- coeficiente de bimodalidad (>0.555 = bimodal)

    -- Control de calidad
    valid_pixels_ratio           REAL,
    scene_cloud_cover            REAL,
    classification_status        TEXT,                 -- 'water_detected','no_water','low_quality'

    -- Geometría del espejo de agua detectado (NULL si no se detectó agua)
    water_geom                   GEOMETRY(MULTIPOLYGON, 32619),

    calculated_at                TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (metric_id, year)
) PARTITION BY RANGE (year);

CREATE INDEX IF NOT EXISTS idx_metricas_scene      ON aculeo_metricas.water_metrics (scene_id);
CREATE INDEX IF NOT EXISTS idx_metricas_cuenca     ON aculeo_metricas.water_metrics (sscuenca_id);
CREATE INDEX IF NOT EXISTS idx_metricas_date       ON aculeo_metricas.water_metrics (acquisition_date);
CREATE INDEX IF NOT EXISTS idx_metricas_tipo       ON aculeo_metricas.water_metrics (water_index_type);
CREATE INDEX IF NOT EXISTS idx_metricas_status     ON aculeo_metricas.water_metrics (classification_status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_metricas_unique
    ON aculeo_metricas.water_metrics (sscuenca_id, scene_id, water_index_type, year);

DO $$
DECLARE yr INTEGER;
BEGIN
    FOR yr IN 2013..2026 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS aculeo_metricas.water_metrics_%s
             PARTITION OF aculeo_metricas.water_metrics FOR VALUES FROM (%s) TO (%s);',
            yr, yr, yr + 1
        );
    END LOOP;
END $$;

-- =====================================================
-- ACULEO_METRICAS: Vista de serie temporal completa
-- =====================================================
CREATE OR REPLACE VIEW aculeo_metricas.v_serie_temporal AS
SELECT
    wm.acquisition_date,
    wm.year,
    EXTRACT(MONTH FROM wm.acquisition_date)::int  AS month,
    wm.sensor,
    wm.water_index_type,
    wm.total_water_area_km2,
    wm.water_pixels_count,
    wm.n_water_components,
    wm.main_component_compactness,
    wm.mndwi_threshold_used,
    wm.scene_index_median,
    wm.mndwi_water_mean,
    wm.bimodality_coefficient,
    wm.valid_pixels_ratio,
    wm.scene_cloud_cover,
    wm.classification_status,
    c.cod_sscuenca,
    c.nombre AS nombre_cuenca
FROM aculeo_metricas.water_metrics wm
JOIN cuencas.dga_subsub_cuenca c ON wm.sscuenca_id = c.sscuenca_id
ORDER BY wm.acquisition_date, wm.water_index_type;

-- =====================================================
-- ACULEO_METRICAS: Vista de resumen anual
-- =====================================================
CREATE OR REPLACE VIEW aculeo_metricas.v_resumen_anual AS
SELECT
    year,
    water_index_type,
    COUNT(*)                                            AS escenas_procesadas,
    COUNT(*) FILTER (WHERE classification_status = 'water_detected')
                                                        AS escenas_con_agua,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY total_water_area_km2
    ) FILTER (WHERE classification_status = 'water_detected')
                                                        AS mediana_area_km2,
    MAX(total_water_area_km2)                           AS max_area_km2,
    MIN(total_water_area_km2) FILTER (
        WHERE classification_status = 'water_detected') AS min_area_km2,
    AVG(valid_pixels_ratio)                             AS avg_valid_ratio,
    AVG(scene_cloud_cover)                              AS avg_cloud_cover
FROM aculeo_metricas.water_metrics
GROUP BY year, water_index_type
ORDER BY year, water_index_type;

COMMENT ON SCHEMA aculeo_clear    IS 'Indices espectrales MNDWI/NDWI con QA aplicado, recortados al AOI — PoC Aculeo';
COMMENT ON SCHEMA aculeo_metricas IS 'Metricas de area de agua y validacion estadistica por escena — PoC Aculeo';
