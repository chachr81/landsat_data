from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple
import numpy as np


class IWaterIndexStrategy(ABC):
    """Estrategia de cálculo de un índice espectral de agua (MNDWI, NDWI, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def required_bands(self) -> list[str]:
        pass

    @abstractmethod
    def calculate(self, bands_data: Dict[str, np.ndarray]) -> np.ndarray:
        pass


class IQAMaskProcessor(ABC):
    """Genera máscaras de validez de píxeles a partir de bandas QA."""

    @abstractmethod
    def generate_valid_mask(self, qa_band: np.ndarray) -> np.ndarray:
        """True = píxel válido (sin nubes/sombras/fill)."""
        pass


class ISpectralIndexReader(ABC):
    """
    Lee índices espectrales pre-calculados desde la capa Silver (aculeo_clear).
    Dependency Inversion: MetricsPipeline depende de esta abstracción,
    no de SpectralIndexExtractor directamente.
    """

    @abstractmethod
    def get_scene_metadata(self, scene_id: int) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_index_as_numpy(
        self,
        scene_id:    int,
        sscuenca_id: int,
        index_type:  str,
    ) -> tuple[Optional[np.ndarray], float, float, Optional[dict]]:
        """
        Retorna (array, resolution_m, valid_pixel_ratio, transform_info).
        array: float32 con NaN donde el píxel es inválido; None si no existe.
        transform_info: {'ul_x', 'ul_y', 'scale_x', 'scale_y', 'srid'} para
                        reconstruir el georreferenciamiento del raster.
        """
        pass


class IMetricsWriter(ABC):
    """Persiste métricas de agua en la capa Gold (aculeo_metricas)."""

    @abstractmethod
    def save_water_metric(self, metric_data: Dict[str, Any]) -> None:
        pass
