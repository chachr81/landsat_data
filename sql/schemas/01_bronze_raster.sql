-- =====================================================
-- SCHEMA: aculeo_raw
-- PoC Monitoreo Laguna de Aculeo — Capa de Ingesta
-- Landsat 8/9 OLI_TIRS | Collection 2 Level-2
-- Path/Row: 233/083 | SRID rasters: 32719 (UTM 19S)
-- Rango: 2013-2026
-- =====================================================

CREATE SCHEMA IF NOT EXISTS aculeo_raw;

-- =====================================================
-- METADATOS DE ESCENAS
-- =====================================================
CREATE TABLE IF NOT EXISTS aculeo_raw.landsat_scenes (
    scene_id         SERIAL PRIMARY KEY,
    entity_id        TEXT UNIQUE NOT NULL,
    display_id       TEXT NOT NULL,
    dataset_name     TEXT NOT NULL,
    sensor           TEXT NOT NULL,        -- 'OLI_TIRS'
    satellite        TEXT NOT NULL,        -- 'LANDSAT_8', 'LANDSAT_9'
    acquisition_date DATE NOT NULL,
    path_row         TEXT NOT NULL,        -- '233/083'
    cloud_cover      REAL CHECK (cloud_cover BETWEEN 0 AND 100),
    sun_azimuth      REAL,
    sun_elevation    REAL,
    processing_level TEXT,
    download_date    TIMESTAMP DEFAULT NOW(),
    footprint        GEOMETRY(POLYGON, 4326)
);

CREATE INDEX IF NOT EXISTS idx_raw_scenes_date     ON aculeo_raw.landsat_scenes (acquisition_date);
CREATE INDEX IF NOT EXISTS idx_raw_scenes_sensor   ON aculeo_raw.landsat_scenes (sensor);
CREATE INDEX IF NOT EXISTS idx_raw_scenes_path_row ON aculeo_raw.landsat_scenes (path_row);
CREATE INDEX IF NOT EXISTS idx_raw_scenes_footprint ON aculeo_raw.landsat_scenes USING GIST (footprint);

-- =====================================================
-- BANDAS RASTER (PARTICIONADA POR AÑO)
-- =====================================================
CREATE TABLE IF NOT EXISTS aculeo_raw.landsat_bands (
    rid         SERIAL,
    scene_id    INTEGER NOT NULL REFERENCES aculeo_raw.landsat_scenes(scene_id) ON DELETE CASCADE,
    band_name   TEXT NOT NULL,
    year        INTEGER NOT NULL,
    rast        RASTER,
    filename    TEXT,
    loaded_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (rid, year)
) PARTITION BY RANGE (year);

CREATE INDEX IF NOT EXISTS idx_raw_bands_scene ON aculeo_raw.landsat_bands (scene_id);
CREATE INDEX IF NOT EXISTS idx_raw_bands_name  ON aculeo_raw.landsat_bands (band_name);
CREATE INDEX IF NOT EXISTS idx_raw_bands_rast  ON aculeo_raw.landsat_bands USING GIST (ST_ConvexHull(rast));

DO $$
DECLARE yr INTEGER;
BEGIN
    FOR yr IN 2013..2026 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS aculeo_raw.landsat_bands_%s
             PARTITION OF aculeo_raw.landsat_bands FOR VALUES FROM (%s) TO (%s);',
            yr, yr, yr + 1
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS idx_raw_bands_%s_rast
             ON aculeo_raw.landsat_bands_%s USING GIST (ST_ConvexHull(rast));',
            yr, yr
        );
    END LOOP;
END $$;

-- =====================================================
-- LOG DE DESCARGAS
-- =====================================================
CREATE TABLE IF NOT EXISTS aculeo_raw.download_log (
    log_id                    SERIAL PRIMARY KEY,
    entity_id                 TEXT NOT NULL,
    band_name                 TEXT NOT NULL,
    download_url              TEXT,
    download_status           TEXT CHECK (download_status IN ('pending','success','failed','skipped')),
    attempt_count             INTEGER DEFAULT 1,
    error_message             TEXT,
    file_size_mb              REAL,
    download_duration_seconds REAL,
    created_at                TIMESTAMP DEFAULT NOW(),
    updated_at                TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_log_entity ON aculeo_raw.download_log (entity_id, band_name);
CREATE INDEX IF NOT EXISTS idx_raw_log_status ON aculeo_raw.download_log (download_status);

-- =====================================================
-- CONFIGURACION DE BANDAS POR SENSOR
-- =====================================================
CREATE TABLE IF NOT EXISTS aculeo_raw.sensor_bands_config (
    sensor           TEXT PRIMARY KEY,
    green_band       TEXT NOT NULL,
    nir_band         TEXT NOT NULL,
    swir_band        TEXT NOT NULL,
    qa_bands         TEXT[] NOT NULL,
    date_range_start DATE,
    date_range_end   DATE
);

INSERT INTO aculeo_raw.sensor_bands_config
    (sensor,      green_band, nir_band, swir_band, qa_bands,                                      date_range_start, date_range_end)
VALUES
    ('OLI_TIRS',  'SR_B3',    'SR_B5',  'SR_B6',   ARRAY['QA_PIXEL','QA_RADSAT','QA_AEROSOL'],   '2013-04-11',    NULL)
ON CONFLICT (sensor) DO UPDATE SET
    green_band       = EXCLUDED.green_band,
    nir_band         = EXCLUDED.nir_band,
    swir_band        = EXCLUDED.swir_band,
    qa_bands         = EXCLUDED.qa_bands,
    date_range_start = EXCLUDED.date_range_start,
    date_range_end   = EXCLUDED.date_range_end;

-- =====================================================
-- VISTA: Inventario de bandas disponibles
-- =====================================================
CREATE OR REPLACE VIEW aculeo_raw.v_inventario_bandas AS
SELECT
    s.scene_id,
    s.entity_id,
    s.satellite,
    s.acquisition_date,
    s.cloud_cover,
    COUNT(DISTINCT b.band_name)                          AS n_bandas,
    ARRAY_AGG(DISTINCT b.band_name ORDER BY b.band_name) AS bandas,
    COUNT(b.rid)                                         AS tiles_total,
    MIN(b.loaded_at)                                     AS primera_carga,
    MAX(b.loaded_at)                                     AS ultima_carga
FROM aculeo_raw.landsat_scenes s
LEFT JOIN aculeo_raw.landsat_bands b ON s.scene_id = b.scene_id
GROUP BY s.scene_id, s.entity_id, s.satellite, s.acquisition_date, s.cloud_cover
ORDER BY s.acquisition_date DESC;

COMMENT ON SCHEMA aculeo_raw IS 'Ingesta de rasters crudos Landsat C2L2 — PoC Monitoreo Laguna de Aculeo';