"""
ClearProcessor: Bronze → aculeo_clear

Lee bandas de aculeo_raw via funcion SQL de recorte, aplica enmascaramiento
QA completo, calcula indices espectrales (MNDWI, NDWI) y almacena los
rasters de indices en aculeo_clear.spectral_indices.
"""

import uuid
import logging
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import psycopg2
import rasterio
from rasterio.transform import Affine

from ..infra.config import load_env, setup_logger
from ..infra.db import get_db_connection_string
from .qa import LandsatC2L2QAProcessor


# Bandas necesarias para los dos indices
_REQUIRED_BANDS = ('SR_B3', 'SR_B5', 'SR_B6', 'QA_PIXEL', 'QA_RADSAT', 'QA_AEROSOL')
_INDEX_BAND_REQUIREMENTS = {
    'MNDWI': ('SR_B3', 'SR_B6'),
    'NDWI':  ('SR_B3', 'SR_B5'),
}
_NODATA = -9999.0


class ClearProcessor:
    """
    Orquestador para la capa aculeo_clear.

    Responsabilidad unica: tomar escenas de aculeo_raw que aun no tienen
    su indice calculado, procesarlas y almacenar los rasters de indice.
    """

    def __init__(
        self,
        sscuenca_id: int,
        local_port:  Optional[int] = None,
        logger:      Optional[logging.Logger] = None,
    ):
        self._sscuenca_id = sscuenca_id
        env = load_env()
        self._conn_str  = get_db_connection_string(env, local_port=local_port)
        self._qa        = LandsatC2L2QAProcessor()
        self._logger    = logger or setup_logger('ClearProcessor')
        self._temp_dir  = Path(tempfile.gettempdir()) / 'aculeo_clear'
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    def process_pending(self, index_types: list[str] = None) -> dict:
        """
        Procesa todas las escenas en aculeo_raw que aun no tienen indice
        calculado para self._sscuenca_id.

        Returns:
            dict con 'processed', 'skipped', 'failed'
        """
        if index_types is None:
            index_types = list(_INDEX_BAND_REQUIREMENTS.keys())

        pending = self._get_pending_scenes(index_types)
        self._logger.info(f"{len(pending)} escenas pendientes para sscuenca_id={self._sscuenca_id}")

        stats = {'processed': 0, 'skipped': 0, 'failed': 0}

        for scene_id, year in pending:
            for idx_type in index_types:
                try:
                    self._process_scene(scene_id, year, idx_type)
                    stats['processed'] += 1
                except Exception as exc:
                    self._logger.error(f"scene_id={scene_id} idx={idx_type}: {exc}")
                    stats['failed'] += 1

        return stats

    # -------------------------------------------------------
    # Flujo por escena
    # -------------------------------------------------------

    def _process_scene(self, scene_id: int, year: int, index_type: str):
        self._logger.info(f"Procesando scene_id={scene_id} indice={index_type}")

        bands, geo = self._read_clipped_bands(scene_id)
        if not bands:
            self._logger.warning(f"scene_id={scene_id}: sin bandas en aculeo_raw")
            return

        valid_mask = self._build_valid_mask(bands, index_type)
        index_arr  = self._calculate_index(bands, valid_mask, index_type)
        valid_ratio = float(np.sum(~np.isnan(index_arr))) / index_arr.size

        self._store_index_raster(index_arr, geo, scene_id, index_type, year, valid_ratio)
        self._logger.info(
            f"scene_id={scene_id} {index_type} guardado "
            f"(valid_ratio={valid_ratio:.2%})"
        )

    def _read_clipped_bands(self, scene_id: int) -> tuple[dict, dict]:
        """
        Llama a aculeo_clear.get_raw_bands_clipped y retorna:
          - bands: {band_name: np.ndarray (raw uint16)}
          - geo  : {srid, scale_x, scale_y, upper_left_x, upper_left_y}
        """
        bands = {}
        geo   = {}

        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        band_name,
                        ST_SRID(clipped_rast)          AS srid,
                        ST_ScaleX(clipped_rast)        AS scale_x,
                        ST_ScaleY(clipped_rast)        AS scale_y,
                        ST_UpperLeftX(clipped_rast)    AS ul_x,
                        ST_UpperLeftY(clipped_rast)    AS ul_y,
                        ST_DumpValues(clipped_rast, 1) AS vals
                    FROM aculeo_clear.get_raw_bands_clipped(%s, %s)
                    WHERE band_name = ANY(%s)
                """, (self._sscuenca_id, scene_id, list(_REQUIRED_BANDS)))

                for row in cur.fetchall():
                    band_name, srid, sx, sy, ul_x, ul_y, valarray = row
                    if valarray is None:
                        continue
                    arr = np.array(valarray, dtype=np.float32)
                    bands[band_name] = arr
                    if not geo:
                        geo = {
                            'srid':    srid,
                            'scale_x': sx,
                            'scale_y': sy,
                            'ul_x':    ul_x,
                            'ul_y':    ul_y,
                        }

        return bands, geo

    def _build_valid_mask(self, bands: dict, index_type: str) -> np.ndarray:
        """Construye mascara QA combinada para el indice solicitado."""
        qa_pixel  = bands.get('QA_PIXEL',  np.zeros((1, 1), dtype=np.float32))
        qa_radsat = bands.get('QA_RADSAT', np.zeros((1, 1), dtype=np.float32))
        qa_aerosol = bands.get('QA_AEROSOL')

        valid = self._qa.get_valid_mask(
            qa_pixel.astype(np.uint16),
            qa_radsat.astype(np.uint16),
            qa_aerosol.astype(np.uint8) if qa_aerosol is not None else None,
            index_type=index_type,
        )

        spectral = [bands[b] for b in _INDEX_BAND_REQUIREMENTS[index_type] if b in bands]
        valid = self._qa.exclude_sr_fill(valid, *spectral)
        return valid

    def _calculate_index(
        self,
        bands:      dict,
        valid_mask: np.ndarray,
        index_type: str,
    ) -> np.ndarray:
        """
        Calcula el indice espectral aplicando factor de escala USGS.
        Pixeles invalidos quedan como NaN.
        """
        b1_name, b2_name = _INDEX_BAND_REQUIREMENTS[index_type]
        b1_raw = bands.get(b1_name)
        b2_raw = bands.get(b2_name)

        if b1_raw is None or b2_raw is None:
            raise ValueError(f"Faltan bandas para {index_type}: {b1_name}, {b2_name}")

        b1 = self._qa.apply_scale_factor(b1_raw)
        b2 = self._qa.apply_scale_factor(b2_raw)

        denom = b1 + b2
        with np.errstate(divide='ignore', invalid='ignore'):
            idx = np.where(denom == 0, np.nan, (b1 - b2) / denom)

        idx = np.where(valid_mask, idx, np.nan)
        return idx.astype(np.float32)

    # -------------------------------------------------------
    # Almacenamiento en aculeo_clear
    # -------------------------------------------------------

    def _store_index_raster(
        self,
        index_arr:   np.ndarray,
        geo:         dict,
        scene_id:    int,
        index_type:  str,
        year:        int,
        valid_ratio: float,
    ):
        """Escribe el array numpy como raster PostGIS via ST_FromGDALRaster."""
        tmp_path = self._temp_dir / f"idx_{index_type}_{scene_id}_{uuid.uuid4().hex[:8]}.tif"

        try:
            transform = Affine(
                geo['scale_x'], 0.0, geo['ul_x'],
                0.0, geo['scale_y'], geo['ul_y'],
            )
            arr_out = np.where(np.isnan(index_arr), _NODATA, index_arr).astype(np.float32)

            with rasterio.open(
                tmp_path, 'w', driver='GTiff',
                height=arr_out.shape[0], width=arr_out.shape[1],
                count=1, dtype='float32',
                crs=rasterio.CRS.from_epsg(geo['srid']),
                transform=transform,
                nodata=_NODATA,
            ) as dst:
                dst.write(arr_out, 1)

            self._insert_index_raster(tmp_path, scene_id, index_type, year, valid_ratio)

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _insert_index_raster(
        self,
        tif_path:    Path,
        scene_id:    int,
        index_type:  str,
        year:        int,
        valid_ratio: float,
    ):
        tif_bytes = tif_path.read_bytes()

        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SET postgis.gdal_enabled_drivers = 'ENABLE_ALL';")

                cur.execute("""
                    DELETE FROM aculeo_clear.spectral_indices
                    WHERE scene_id    = %s
                      AND sscuenca_id = %s
                      AND index_type  = %s
                      AND year        = %s
                """, (scene_id, self._sscuenca_id, index_type, year))

                cur.execute("""
                    INSERT INTO aculeo_clear.spectral_indices
                        (scene_id, sscuenca_id, index_type, year, rast, valid_pixel_ratio)
                    SELECT %s, %s, %s, %s, tile.rast, %s
                    FROM ST_Tile(
                        ST_FromGDALRaster(%s::bytea),
                        256, 256, TRUE
                    ) AS tile(rast)
                """, (scene_id, self._sscuenca_id, index_type, year,
                      valid_ratio, psycopg2.Binary(tif_bytes)))

            conn.commit()

    # -------------------------------------------------------
    # Consultas auxiliares
    # -------------------------------------------------------

    def _get_pending_scenes(self, index_types: list[str]) -> list[tuple[int, int]]:
        """Devuelve (scene_id, year) de escenas sin indices en aculeo_clear."""
        with psycopg2.connect(self._conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT s.scene_id,
                           EXTRACT(YEAR FROM s.acquisition_date)::int AS year
                    FROM aculeo_raw.landsat_scenes s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM aculeo_clear.spectral_indices ci
                        WHERE ci.scene_id    = s.scene_id
                          AND ci.sscuenca_id = %s
                          AND ci.index_type  = ANY(%s)
                    )
                    ORDER BY year, s.scene_id;
                """, (self._sscuenca_id, index_types))
                return cur.fetchall()
