-- =====================================================
-- Migración 001: añadir columnas de calidad GMM
-- Aplica sobre: aculeo_metricas.water_metrics y todas sus particiones.
-- PostgreSQL propaga ALTER TABLE a las particiones automáticamente.
-- =====================================================
-- Ejecutar conectado a maps_negentropy con permisos de escritura:
--   psql $DB_URL -f sql/migrations/001_add_confidence_score.sql

ALTER TABLE aculeo_metricas.water_metrics
    ADD COLUMN IF NOT EXISTS gmm_separation   REAL,
    ADD COLUMN IF NOT EXISTS confidence_score REAL;
