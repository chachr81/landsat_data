"""
Proveedores de datos para la capa aculeo_metricas.

Lee indices espectrales desde aculeo_clear.spectral_indices
y escribe metricas en aculeo_metricas.water_metrics.
"""

from typing import Any, Dict, Optional

import numpy as np
import psycopg2

from ..utils import get_db_connection_string, load_env
from .interfaces import ISpectralIndexReader, IMetricsWriter


class SpectralIndexExtractor(ISpectralIndexReader):
    """
    Extrae rasters de indices espectrales desde aculeo_clear
    como arrays numpy listos para analisis.
    """

    def __init__(self, local_port: Optional[int] = None):
        env = load_env()
        self._conn_str = get_db_connection_string(env, local_port=local_port)

    def get_scene_metadata(self, scene_id: int) -> Dict[str, Any]:
        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sensor, acquisition_date, cloud_cover,
                           EXTRACT(YEAR FROM acquisition_date)::int AS year
                    FROM aculeo_raw.landsat_scenes
                    WHERE scene_id = %s
                """, (scene_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Scene {scene_id} no encontrada en aculeo_raw")
                return {
                    'sensor':           row[0],
                    'acquisition_date': row[1],
                    'cloud_cover':      row[2],
                    'year':             row[3],
                }

    def get_index_as_numpy(
        self,
        scene_id:    int,
        sscuenca_id: int,
        index_type:  str,
    ) -> tuple[Optional[np.ndarray], float, float, Optional[dict]]:
        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH unified AS (
                        SELECT
                            ST_Union(rast)         AS u,
                            MAX(valid_pixel_ratio) AS valid_ratio
                        FROM aculeo_clear.spectral_indices
                        WHERE scene_id    = %s
                          AND sscuenca_id = %s
                          AND index_type  = %s
                    )
                    SELECT
                        ABS(ST_ScaleX(u))    AS res_m,
                        ST_DumpValues(u, 1)  AS vals,
                        valid_ratio,
                        ST_ScaleX(u)         AS scale_x,
                        ST_ScaleY(u)         AS scale_y,
                        ST_UpperLeftX(u)     AS ul_x,
                        ST_UpperLeftY(u)     AS ul_y,
                        ST_SRID(u)           AS srid
                    FROM unified
                """, (scene_id, sscuenca_id, index_type))

                row = cur.fetchone()

        if row is None or row[1] is None:
            return None, 30.0, 0.0, None

        res_m, valarray, valid_ratio, scale_x, scale_y, ul_x, ul_y, srid = row
        arr = np.array(valarray, dtype=np.float32)
        arr[arr == -9999.0] = np.nan

        transform_info = {
            'scale_x': float(scale_x),
            'scale_y': float(scale_y),
            'ul_x':    float(ul_x),
            'ul_y':    float(ul_y),
            'srid':    int(srid),
        }
        return arr, float(res_m), float(valid_ratio or 0.0), transform_info


class WaterMetricsWriter(IMetricsWriter):
    """
    Persiste las metricas de deteccion de agua en aculeo_metricas.water_metrics.
    Usa DELETE + INSERT para evitar duplicados en tablas particionadas.
    """

    def __init__(self, local_port: Optional[int] = None):
        env = load_env()
        self._conn_str = get_db_connection_string(env, local_port=local_port)

    @staticmethod
    def _round_metric(metric_data: Dict[str, Any]) -> Dict[str, Any]:
        fields = [
            'total_water_area_km2', 'valid_pixels_ratio', 'scene_cloud_cover',
            'mndwi_threshold_used', 'scene_index_median', 'mndwi_water_mean',
            'mndwi_water_std', 'bimodality_coefficient', 'main_component_compactness',
        ]
        result = dict(metric_data)
        for f in fields:
            if result.get(f) is not None:
                result[f] = round(float(result[f]), 2)
        return result

    def save_water_metric(self, metric_data: Dict[str, Any]) -> None:
        metric_data = self._round_metric(metric_data)
        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM aculeo_metricas.water_metrics
                    WHERE sscuenca_id      = %(sscuenca_id)s
                      AND scene_id         = %(scene_id)s
                      AND water_index_type = %(water_index_type)s
                      AND year             = %(year)s
                """, metric_data)

                water_geom_wkt = metric_data.get('water_geom')
                cur.execute("""
                    INSERT INTO aculeo_metricas.water_metrics (
                        sscuenca_id,          scene_id,
                        acquisition_date,     year,
                        sensor,               water_index_type,
                        total_water_area_km2, water_pixels_count,
                        n_water_components,   main_component_compactness,
                        mndwi_threshold_used, scene_index_median,
                        mndwi_water_mean,     mndwi_water_std,
                        bimodality_coefficient,
                        valid_pixels_ratio,   scene_cloud_cover,
                        classification_status,
                        water_geom
                    ) VALUES (
                        %(sscuenca_id)s,          %(scene_id)s,
                        %(acquisition_date)s,     %(year)s,
                        %(sensor)s,               %(water_index_type)s,
                        %(total_water_area_km2)s, %(water_pixels_count)s,
                        %(n_water_components)s,   %(main_component_compactness)s,
                        %(mndwi_threshold_used)s, %(scene_index_median)s,
                        %(mndwi_water_mean)s,     %(mndwi_water_std)s,
                        %(bimodality_coefficient)s,
                        %(valid_pixels_ratio)s,   %(scene_cloud_cover)s,
                        %(classification_status)s,
                        CASE WHEN %(water_geom)s IS NULL THEN NULL
                             ELSE ST_GeomFromText(%(water_geom)s, 32619)
                        END
                    )
                """, {**metric_data, 'water_geom': water_geom_wkt})
            conn.commit()
