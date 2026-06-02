import numpy as np
import pytest
from aculeo.gold.detector import WaterBodyDetector, DetectionResult


def _bimodal_array(n=5000, water_mean=0.2, land_mean=-0.25, water_frac=0.3, seed=42):
    """Array sintético bimodal: fracción water_frac de píxeles en el modo agua."""
    rng = np.random.default_rng(seed)
    n_water = int(n * water_frac)
    n_land  = n - n_water
    water = rng.normal(water_mean, 0.05, n_water)
    land  = rng.normal(land_mean,  0.08, n_land)
    arr = np.concatenate([water, land])
    rng.shuffle(arr)
    return arr.reshape(100, 50).astype(np.float32)


def _unimodal_array(n=5000, mean=-0.3, seed=99):
    rng = np.random.default_rng(seed)
    arr = rng.normal(mean, 0.07, n).reshape(100, 50).astype(np.float32)
    return arr


def test_gmm_threshold_bayes_boundary():
    detector = WaterBodyDetector()
    values = _bimodal_array().flatten()
    values = values[~np.isnan(values)]
    threshold, is_separated, separation = detector._gmm_threshold(values)
    assert -0.35 < threshold < 0.15, f"threshold={threshold} fuera del rango esperado"
    assert is_separated is True
    assert separation > 2.5


def test_gmm_threshold_not_separated():
    detector = WaterBodyDetector()
    values = _unimodal_array().flatten()
    _, is_separated, separation = detector._gmm_threshold(values)
    assert is_separated is False


def test_detect_low_quality_threshold_is_none():
    detector = WaterBodyDetector()
    empty = np.full((50, 50), np.nan, dtype=np.float32)
    result = detector.detect(empty)
    assert result.classification_status == 'low_quality'
    assert result.threshold is None


def test_detect_result_threshold_type():
    detector = WaterBodyDetector()
    arr = _bimodal_array()
    result = detector.detect(arr, pixel_area_km2=0.0009)
    if result.classification_status == 'water_detected':
        assert isinstance(result.threshold, float)


def test_seasonal_threshold_range_summer():
    detector = WaterBodyDetector()
    lo, hi = detector._get_threshold_range(month=1)
    assert lo == -0.40
    assert hi == 0.10


def test_seasonal_threshold_range_winter():
    detector = WaterBodyDetector()
    lo, hi = detector._get_threshold_range(month=7)
    assert lo == -0.35
    assert hi == 0.05


def test_seasonal_threshold_range_no_month():
    detector = WaterBodyDetector()
    lo, hi = detector._get_threshold_range(month=None)
    assert lo == -0.40
    assert hi == 0.10


def test_confidence_score_range():
    detector = WaterBodyDetector()
    arr = _bimodal_array()
    result = detector.detect(arr, pixel_area_km2=0.0009)
    assert 0.0 <= result.confidence_score <= 1.0


def test_confidence_score_no_water_is_zero():
    detector = WaterBodyDetector()
    empty = np.full((50, 50), np.nan, dtype=np.float32)
    result = detector.detect(empty)
    assert result.confidence_score == 0.0
