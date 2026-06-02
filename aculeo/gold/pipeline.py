"""
MetricsPipeline: aculeo_clear → aculeo_metricas

Lee indices espectrales de aculeo_clear, aplica deteccion estadistica
de agua (GMM + componentes conectados) y persiste metricas.
"""

import logging
from typing import Optional

import numpy as np
from rasterio.transform import Affine
from rasterio.features import shapes
from shapely.geometry import shape, MultiPolygon
from shapely.ops import unary_union

from .interfaces import ISpectralIndexReader, IMetricsWriter
from .detector import WaterBodyDetector

# Centroide de referencia de Laguna de Aculeo en SRID 32619
# Derivado de metric_id=67 (2013-09-03, 10.36 km², detección validada)
_REF_CENTROID_X = 322635.0
_REF_CENTROID_Y = -3746696.0


class MetricsPipeline:
    """
    Orquestador Silver→Gold: coordina lectura de índices, detección
    estadística y escritura de métricas. Toda la lógica de detección
    vive en WaterBodyDetector.
    """

    def __init__(
        self,
        extractor:   ISpectralIndexReader,
        writer:      IMetricsWriter,
        detector:    WaterBodyDetector,
        logger:      Optional[logging.Logger] = None,
        sscuenca_id: int = 411,
    ):
        self._extractor      = extractor
        self._writer         = writer
        self._detector       = detector
        self._logger         = logger or logging.getLogger(__name__)
        self._sscuenca_id    = sscuenca_id

    def process_scene(self, scene_id: int, index_types: list[str]) -> dict:
        """
        Calcula metricas para una escena en todos los tipos de indice solicitados.

        Returns:
            dict con resultados por index_type
        """
        metadata = self._extractor.get_scene_metadata(scene_id)
        results  = {}

        for idx_type in index_types:
            try:
                result = self._process_index(scene_id, metadata, idx_type)
                results[idx_type] = result
            except Exception as exc:
                self._logger.error(
                    f"scene_id={scene_id} idx={idx_type}: {exc}", exc_info=True
                )
                results[idx_type] = None

        return results

    def _process_index(
        self,
        scene_id:  int,
        metadata:  dict,
        idx_type:  str,
    ) -> dict:
        index_arr, res_m, valid_ratio, transform_info = self._extractor.get_index_as_numpy(
            scene_id, self._sscuenca_id, idx_type
        )

        if index_arr is None:
            self._logger.warning(
                f"scene_id={scene_id}: sin raster {idx_type} en aculeo_clear"
            )
            return {}

        # Centroide de referencia en coordenadas de pixel
        ref_centroid_px = None
        if transform_info:
            ref_col = (_REF_CENTROID_X - transform_info['ul_x']) / transform_info['scale_x']
            ref_row = (_REF_CENTROID_Y - transform_info['ul_y']) / transform_info['scale_y']
            ref_centroid_px = (ref_row, ref_col)

        pixel_area_km2 = (res_m ** 2) / 1_000_000.0
        month = metadata['acquisition_date'].month  # int 1-12 para calibración estacional

        detection = self._detector.detect(
            index_arr,
            pixel_area_km2=pixel_area_km2,
            ref_centroid_px=ref_centroid_px,
            res_m=res_m,
            month=month,
        )
        self._logger.info(
            f"scene_id={scene_id} {idx_type}: "
            f"status={detection.classification_status} "
            f"area={detection.total_water_area_km2:.2f}km2 "
            f"threshold={detection.threshold}"
        )

        water_geom_wkt = self._vectorize_water_mask(detection.water_mask, transform_info)

        metric = {
            'sscuenca_id':               self._sscuenca_id,
            'scene_id':                  scene_id,
            'acquisition_date':          metadata['acquisition_date'],
            'year':                      metadata['year'],
            'sensor':                    metadata['sensor'],
            'water_index_type':          idx_type,
            'total_water_area_km2':      detection.total_water_area_km2,
            'water_pixels_count':        detection.water_pixels_count,
            'n_water_components':        detection.n_water_components,
            'main_component_compactness':detection.main_component_compactness,
            'mndwi_threshold_used':      detection.threshold,
            'scene_index_median':        detection.scene_index_median,
            'mndwi_water_mean':          detection.mndwi_water_mean,
            'mndwi_water_std':           detection.mndwi_water_std,
            'bimodality_coefficient':    detection.bimodality_coefficient,
            'valid_pixels_ratio':        valid_ratio,
            'scene_cloud_cover':         metadata['cloud_cover'],
            'classification_status':     detection.classification_status,
            'water_geom':                water_geom_wkt,
        }

        self._writer.save_water_metric(metric)
        return metric

    def _vectorize_water_mask(
        self,
        water_mask:     Optional[np.ndarray],
        transform_info: Optional[dict],
    ) -> Optional[str]:
        """Convierte la máscara binaria numpy a WKT MultiPolygon en el CRS nativo."""
        if water_mask is None or not water_mask.any() or transform_info is None:
            return None

        transform = Affine(
            transform_info['scale_x'], 0.0, transform_info['ul_x'],
            0.0, transform_info['scale_y'], transform_info['ul_y'],
        )
        mask_u8 = water_mask.astype(np.uint8)
        geoms = [
            shape(geom)
            for geom, val in shapes(mask_u8, mask=mask_u8, transform=transform)
            if val == 1
        ]
        if not geoms:
            return None
        merged = unary_union(geoms)
        if merged.is_empty:
            return None
        if merged.geom_type == 'Polygon':
            merged = MultiPolygon([merged])
        return merged.wkt
