"""
Procesador de mascaras QA para Landsat Collection 2 Level-2 OLI_TIRS.

Fuente oficial:
  https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands

Estrategia de enmascaramiento (orden de aplicacion):
  1. QA_PIXEL  : fill, nubes dilatadas, cirrus, nubes, sombras, nieve
  2. QA_RADSAT : saturacion radiometrica en bandas del indice
  3. QA_AEROSOL: aerosol alto (bits 6-7 = 11) - opcional pero recomendado
  4. SR fill   : bandas con valor 0 (fill nativo antes de escala USGS)

NOTA: Bit 7 de QA_PIXEL (Water flag interno LaSRC) NO se usa como fuente
primaria. El algoritmo interno subestima cuerpos de agua en escenas con
poca agua o bordes de lago. La deteccion se hace mediante inferencia
estadistica con MNDWI/NDWI (ver water_detector.py).
"""

import numpy as np
from typing import Optional


# -------------------------------------------------------
# Constantes de bits QA_PIXEL (uint16, C2L2)
# -------------------------------------------------------
class _QAPixelBit:
    FILL          = 0   # pixel sin datos
    DILATED_CLOUD = 1   # buffer de seguridad entorno a nube
    CIRRUS        = 2   # cirrus — solo L8/9
    CLOUD         = 3   # nube confirmada
    CLOUD_SHADOW  = 4   # sombra de nube
    SNOW          = 5   # nieve/hielo


# -------------------------------------------------------
# Constantes de bits QA_RADSAT (uint16, L8/9 OLI_TIRS)
# -------------------------------------------------------
class _QARadSatBit:
    BAND3_GREEN  = 2   # SR_B3 saturado
    BAND5_NIR    = 4   # SR_B5 saturado
    BAND6_SWIR1  = 5   # SR_B6 saturado


class LandsatC2L2QAProcessor:
    """
    Genera mascaras de validez de pixeles para Landsat C2L2 OLI_TIRS.

    Uso:
        processor = LandsatC2L2QAProcessor()
        valid = processor.get_valid_mask(qa_pixel, qa_radsat, qa_aerosol, 'MNDWI')
        valid = processor.exclude_sr_fill(valid, green, swir1)
    """

    # Mascara QA_PIXEL: bits 0,1,2,3,4,5 → 0x003F
    _QA_PIXEL_MASK: int = (
        (1 << _QAPixelBit.FILL)          |
        (1 << _QAPixelBit.DILATED_CLOUD) |
        (1 << _QAPixelBit.CIRRUS)        |
        (1 << _QAPixelBit.CLOUD)         |
        (1 << _QAPixelBit.CLOUD_SHADOW)  |
        (1 << _QAPixelBit.SNOW)
    )

    # QA_RADSAT para MNDWI: B3 (bit2) + B6 (bit5) → 0x0024
    _RADSAT_MNDWI_MASK: int = (
        (1 << _QARadSatBit.BAND3_GREEN) |
        (1 << _QARadSatBit.BAND6_SWIR1)
    )

    # QA_RADSAT para NDWI: B3 (bit2) + B5 (bit4) → 0x0014
    _RADSAT_NDWI_MASK: int = (
        (1 << _QARadSatBit.BAND3_GREEN) |
        (1 << _QARadSatBit.BAND5_NIR)
    )

    def get_valid_mask(
        self,
        qa_pixel:   np.ndarray,
        qa_radsat:  np.ndarray,
        qa_aerosol: Optional[np.ndarray] = None,
        index_type: str = 'MNDWI'
    ) -> np.ndarray:
        """
        Retorna mascara booleana: True = pixel valido para el calculo del indice.

        Args:
            qa_pixel  : array uint16 QA_PIXEL
            qa_radsat : array uint16 QA_RADSAT
            qa_aerosol: array uint8  QA_AEROSOL (None si no disponible)
            index_type: 'MNDWI' o 'NDWI'
        """
        qa_px = qa_pixel.astype(np.uint16)
        qa_rs = qa_radsat.astype(np.uint16)

        valid = (qa_px & self._QA_PIXEL_MASK) == 0

        radsat_mask = (
            self._RADSAT_MNDWI_MASK if index_type == 'MNDWI'
            else self._RADSAT_NDWI_MASK
        )
        valid &= (qa_rs & radsat_mask) == 0

        if qa_aerosol is not None:
            aerosol_level = (qa_aerosol.astype(np.uint8) >> 6) & 0x03
            valid &= aerosol_level < 3

        return valid

    def exclude_sr_fill(
        self,
        valid_mask: np.ndarray,
        *spectral_bands: np.ndarray
    ) -> np.ndarray:
        """
        Excluye pixeles donde cualquier banda espectral tenga valor raw == 0
        (fill nativo de USGS antes de aplicar factor de escala 0.0000275 - 0.2).

        Llamar ANTES de aplicar el factor de escala.
        """
        result = valid_mask.copy()
        for band in spectral_bands:
            result &= band != 0
        return result

    def apply_scale_factor(self, band_raw: np.ndarray) -> np.ndarray:
        """
        Convierte valor DN a reflectancia de superficie:
          SR = DN * 0.0000275 - 0.2
        Rango valido: [-0.2, 1.6]
        """
        return band_raw.astype(np.float32) * 0.0000275 - 0.2
