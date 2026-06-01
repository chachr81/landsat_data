"""
Detector estadistico de espejo de agua usando GMM y analisis de componentes.

Flujo por escena:
  1. Test de bimodalidad (coeficiente de Sarle)
     - Unimodal → sin agua detectada
     - Bimodal  → continuar
  2. GMM (2 componentes) sobre valores validos del indice
     - Threshold = frontera optima de Bayes entre componentes
  3. Mascara binaria → scipy.ndimage.label → componentes conectados
  4. Features por componente: area, compacidad, elongacion
  5. Clasificacion: lago vs. canal/sombra via sklearn DecisionTreeClassifier
     con pseudo-etiquetas derivadas del GMM sobre features de componentes

El threshold puede ser negativo (p.ej. -0.14). Esto es correcto
cuando la transicion agua/tierra ocurre en esa zona del histograma.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from scipy import ndimage, stats
from scipy.stats import norm
from sklearn.mixture import GaussianMixture
from sklearn.tree import DecisionTreeClassifier


# -------------------------------------------------------
# Estructuras de datos
# -------------------------------------------------------

@dataclass
class WaterComponent:
    label:       int
    area_px:     int
    area_km2:    float
    compactness: float    # 4*pi*area / perimeter^2  (1=circulo, 0=linea)
    elongation:  float    # eigenvalue ratio de la matriz de inercia
    mean_index:  float    # valor medio del indice en el componente
    centroid_r:  float
    centroid_c:  float
    is_lake:     bool = False


@dataclass
class DetectionResult:
    classification_status:      str     # 'water_detected','no_water','low_quality'
    threshold:                  float
    bimodality_coefficient:     float
    total_water_area_km2:       float
    water_pixels_count:         int
    n_water_components:         int
    main_component_compactness: float
    scene_index_median:         float
    mndwi_water_mean:           float
    mndwi_water_std:            float
    water_mask:                 Optional[np.ndarray] = field(default=None, repr=False)


# -------------------------------------------------------
# Detector principal
# -------------------------------------------------------

class WaterBodyDetector:
    """
    Detecta el espejo de agua en un raster de indice espectral (MNDWI/NDWI)
    usando inferencia estadistica pura, sin poligono de referencia.
    """

    _BIMODALITY_THRESHOLD   = 0.555   # Sarle: >0.555 indica bimodalidad
    _MIN_SEPARATION_STD     = 0.5     # separacion minima entre medias GMM (en sigmas)
    _MIN_VALID_PIXEL_RATIO  = 0.15    # descartar escenas con <15% pixeles validos
    _MIN_WATER_PIXELS       = 9       # minimo de pixeles para considerar cuerpo de agua

    # Filtros calibrados para Laguna de Aculeo
    _THRESHOLD_RANGE        = (-0.40, 0.10)   # rango valido del umbral GMM
    _WATER_MODE_MEAN_RANGE  = (-0.50, 0.15)   # media del modo agua en el GMM
    _MAX_WATER_AREA_KM2     = 15.0            # area maxima historica del lago + margen
    _MIN_COMPACTNESS        = 0.03            # filtrar componentes muy elongados
    _MAX_CENTROID_DIST_M    = 5000.0          # distancia maxima al centroide de referencia

    def __init__(self, pixel_area_km2: float = 0.0009):
        self._pixel_area_km2 = pixel_area_km2

    def detect(
        self,
        index_array:      np.ndarray,
        pixel_area_km2:   Optional[float] = None,
        ref_centroid_px:  Optional[tuple]  = None,
        res_m:            float             = 30.0,
    ) -> DetectionResult:
        """
        Args:
            pixel_area_km2:  area de un pixel en km2.
            ref_centroid_px: (row, col) del centroide de referencia del lago en
                             coordenadas de pixel. Si se provee, filtra componentes
                             demasiado alejados del lago conocido.
            res_m:           resolución espacial en metros (para cálculo de distancia).
        """
        effective_area = pixel_area_km2 if pixel_area_km2 is not None else self._pixel_area_km2
        valid_values = index_array[~np.isnan(index_array)]

        if valid_values.size == 0:
            return self._no_water_result('low_quality', 0.0, 0.0, None)

        valid_ratio = valid_values.size / index_array.size
        if valid_ratio < self._MIN_VALID_PIXEL_RATIO:
            return self._no_water_result('low_quality', 0.0, float(np.median(valid_values)), None)

        scene_median = float(np.median(valid_values))
        bim_coef = self._sarle_bimodality(valid_values)

        if bim_coef < self._BIMODALITY_THRESHOLD:
            return self._no_water_result('no_water', bim_coef, scene_median, None)

        threshold, is_separated, water_mode_mean = self._gmm_threshold(valid_values)

        # Filtro 1: rango de threshold calibrado para el lago
        if not (self._THRESHOLD_RANGE[0] <= threshold <= self._THRESHOLD_RANGE[1]):
            return self._no_water_result('no_water', bim_coef, scene_median, threshold)

        # Filtro 2: media del modo agua debe ser físicamente plausible
        if not (self._WATER_MODE_MEAN_RANGE[0] <= water_mode_mean <= self._WATER_MODE_MEAN_RANGE[1]):
            return self._no_water_result('no_water', bim_coef, scene_median, threshold)

        if not is_separated:
            return self._no_water_result('no_water', bim_coef, scene_median, threshold)

        water_mask = (~np.isnan(index_array)) & (index_array > threshold)

        # Filtro 3: closing morfológico para rellenar gaps internos no insulares
        water_mask = ndimage.binary_closing(water_mask, structure=np.ones((3, 3)))

        components, labeled = self._extract_components(water_mask, index_array, effective_area)

        if not components:
            return self._no_water_result('no_water', bim_coef, scene_median, threshold)

        lake_components = self._classify_components(
            components, ref_centroid_px=ref_centroid_px, res_m=res_m
        )

        if not lake_components:
            return self._no_water_result('no_water', bim_coef, scene_median, threshold)

        lake_mask = np.zeros_like(water_mask)
        for comp in lake_components:
            lake_mask |= (labeled == comp.label)

        lake_values = index_array[lake_mask & ~np.isnan(index_array)]
        total_area  = sum(c.area_km2 for c in lake_components)
        main_comp   = max(lake_components, key=lambda c: c.area_km2)

        return DetectionResult(
            classification_status      = 'water_detected',
            threshold                  = threshold,
            bimodality_coefficient     = bim_coef,
            total_water_area_km2       = total_area,
            water_pixels_count         = int(lake_mask.sum()),
            n_water_components         = len(lake_components),
            main_component_compactness = main_comp.compactness,
            scene_index_median         = scene_median,
            mndwi_water_mean           = float(np.nanmean(lake_values)) if lake_values.size else 0.0,
            mndwi_water_std            = float(np.nanstd(lake_values))  if lake_values.size else 0.0,
            water_mask                 = final_mask,
        )

    # -------------------------------------------------------
    # Bimodalidad
    # -------------------------------------------------------

    def _sarle_bimodality(self, values: np.ndarray) -> float:
        """
        Coeficiente de bimodalidad de Sarle:
            b = (skewness^2 + 1) / kurtosis
        Distribucion bimodal si b > 0.555 (umbral empirico).
        Distribucion normal tiene b = 5/9 ≈ 0.556.
        """
        n = values.size
        if n < 4:
            return 0.0
        skew = float(stats.skew(values))
        kurt = float(stats.kurtosis(values, fisher=True))
        # Correccion por tamano de muestra para kurtosis
        excess_kurt = kurt + 3.0
        denom = excess_kurt if abs(excess_kurt) > 1e-9 else 1e-9
        return (skew ** 2 + 1.0) / denom

    # -------------------------------------------------------
    # Threshold via GMM
    # -------------------------------------------------------

    def _gmm_threshold(self, values: np.ndarray) -> tuple[float, bool, float]:
        """
        Ajusta GMM de 2 componentes.
        Retorna (threshold, is_well_separated, water_mode_mean).
        """
        sample = values if values.size <= 50_000 else np.random.choice(values, 50_000, replace=False)
        X = sample.reshape(-1, 1)

        gmm = GaussianMixture(n_components=2, covariance_type='full',
                              max_iter=200, random_state=42, n_init=3)
        gmm.fit(X)

        means   = gmm.means_.flatten()
        stds    = np.sqrt(gmm.covariances_.flatten())
        weights = gmm.weights_.flatten()

        mean_sep   = abs(means[1] - means[0])
        pooled_std = float(np.sqrt(np.average(stds**2, weights=weights)))
        separation = mean_sep / (pooled_std + 1e-9)

        water_idx = int(np.argmax(means))
        land_idx  = 1 - water_idx

        if separation < self._MIN_SEPARATION_STD:
            return float(np.mean(means)), False, float(means[water_idx])

        x_scan     = np.linspace(min(means), max(means), 1000)
        dens_water = weights[water_idx] * norm.pdf(x_scan, means[water_idx], stds[water_idx])
        dens_land  = weights[land_idx]  * norm.pdf(x_scan, means[land_idx],  stds[land_idx])
        threshold  = float(x_scan[np.argmin(dens_water + dens_land)])

        return threshold, True, float(means[water_idx])

    # -------------------------------------------------------
    # Componentes conectados y features
    # -------------------------------------------------------

    def _extract_components(
        self,
        water_mask:     np.ndarray,
        index_array:    np.ndarray,
        pixel_area_km2: float,
    ) -> tuple[list[WaterComponent], np.ndarray]:
        """Retorna (componentes, array etiquetado) sin almacenar estado de instancia."""
        labeled, n = ndimage.label(water_mask)

        components = []
        for lbl in range(1, n + 1):
            mask    = labeled == lbl
            area_px = int(mask.sum())

            if area_px < self._MIN_WATER_PIXELS:
                continue

            eroded    = ndimage.binary_erosion(mask)
            border_px = int(mask.sum() - eroded.sum())
            denom     = border_px ** 2 if border_px > 0 else 1
            compactness = float(4.0 * np.pi * area_px / denom)

            elongation = self._compute_elongation(mask)

            values_in = index_array[mask & ~np.isnan(index_array)]
            mean_idx  = float(np.nanmean(values_in)) if values_in.size else 0.0

            cy, cx = ndimage.center_of_mass(mask)

            components.append(WaterComponent(
                label       = lbl,
                area_px     = area_px,
                area_km2    = area_px * pixel_area_km2,
                compactness = compactness,
                elongation  = elongation,
                mean_index  = mean_idx,
                centroid_r  = float(cy),
                centroid_c  = float(cx),
            ))

        return components, labeled

    def _compute_elongation(self, mask: np.ndarray) -> float:
        """Ratio mayor/menor eigenvalue de la matriz de inercia del componente."""
        coords = np.argwhere(mask).astype(float)
        if coords.shape[0] < 3:
            return 1.0
        centered = coords - coords.mean(axis=0)
        cov = np.cov(centered.T)
        if cov.ndim < 2:
            return 1.0
        eigvals = np.linalg.eigvalsh(cov)
        eigvals = np.sort(np.abs(eigvals))[::-1]
        return float(eigvals[0] / (eigvals[1] + 1e-9))

    # -------------------------------------------------------
    # Clasificacion lago vs. canal/sombra
    # -------------------------------------------------------

    def _classify_components(
        self,
        components:      list[WaterComponent],
        ref_centroid_px: Optional[tuple] = None,
        res_m:           float = 30.0,
    ) -> list[WaterComponent]:
        """
        Clasifica componentes aplicando filtros geométricos y espaciales,
        luego GMM + DecisionTree sobre los que pasan.
        """
        if not components:
            return []

        # Filtro 4: área máxima histórica del lago
        components = [c for c in components if c.area_km2 <= self._MAX_WATER_AREA_KM2]
        if not components:
            return []

        # Filtro 5: compacidad mínima — descartar sombras elongadas y canales
        components = [c for c in components if c.compactness >= self._MIN_COMPACTNESS]
        if not components:
            return []

        # Filtro 6: proximidad al centroide de referencia del lago
        if ref_centroid_px is not None:
            ref_row, ref_col = ref_centroid_px
            def _dist_m(c: WaterComponent) -> float:
                dy = (c.centroid_r - ref_row) * res_m
                dx = (c.centroid_c - ref_col) * res_m
                return float(np.sqrt(dx**2 + dy**2))
            components = [c for c in components if _dist_m(c) <= self._MAX_CENTROID_DIST_M]
        if not components:
            return []

        if len(components) == 1:
            components[0].is_lake = components[0].area_km2 >= 0.05
            return [c for c in components if c.is_lake]

        feat = np.array([
            [c.area_km2, c.compactness, 1.0 / (c.elongation + 1e-9)]
            for c in components
        ])

        n_clust = min(2, len(components))
        gmm_comp = GaussianMixture(n_components=n_clust, random_state=42, n_init=3)
        gmm_comp.fit(feat)
        pseudo_labels = gmm_comp.predict(feat)

        cluster_area = [
            feat[pseudo_labels == k, 0].mean() if (pseudo_labels == k).any() else 0.0
            for k in range(n_clust)
        ]
        lake_cluster = int(np.argmax(cluster_area))

        tree = DecisionTreeClassifier(max_depth=3, random_state=42)
        y = (pseudo_labels == lake_cluster).astype(int)
        tree.fit(feat, y)

        predictions = tree.predict(feat)
        for i, comp in enumerate(components):
            comp.is_lake = bool(predictions[i]) and comp.area_km2 >= 0.05

        return [c for c in components if c.is_lake]

    # -------------------------------------------------------
    # Helpers
    # -------------------------------------------------------

    def _no_water_result(
        self,
        status: str,
        bim_coef: float,
        scene_median: float,
        threshold: float
    ) -> DetectionResult:
        return DetectionResult(
            classification_status      = status,
            threshold                  = threshold,
            bimodality_coefficient     = bim_coef,
            total_water_area_km2       = 0.0,
            water_pixels_count         = 0,
            n_water_components         = 0,
            main_component_compactness = 0.0,
            scene_index_median         = scene_median,
            mndwi_water_mean           = 0.0,
            mndwi_water_std            = 0.0,
            water_mask                 = None,
        )
