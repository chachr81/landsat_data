import sys
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from dotenv import dotenv_values


def get_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in [current.parent.parent.parent, current.parent.parent.parent.parent]:
        if (parent / '.env').exists():
            return parent
    return current.parent.parent.parent


def load_config() -> Dict:
    config_path = get_project_root() / 'config' / 'landsat_config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_env() -> Dict[str, Optional[str]]:
    env_path = get_project_root() / '.env'
    if not env_path.exists():
        return {}
    return dotenv_values(env_path)


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: Optional[str] = None,
    console: bool = True,
) -> logging.Logger:
    config = load_config().get('PROCESSING_CONFIG', {})
    log_level = level or config.get('LOG_LEVEL', 'INFO')

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    logger.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    if log_file:
        storage = load_config().get('STORAGE_PATHS', {})
        log_dir = get_project_root() / storage.get('LOGS', 'logs/etl')
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if console:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def get_temp_dir() -> Path:
    storage = load_config().get('STORAGE_PATHS', {})
    path = get_project_root() / storage.get('TEMP', 'data/temp')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_aoi_geojson(geojson_path: Optional[str] = None) -> Dict:
    if geojson_path is None:
        geojson_path = load_config().get('aoi', {}).get('geojson_path')
    if not geojson_path:
        raise ValueError("Ruta a GeoJSON no definida en config (sección 'aoi')")
    full_path = get_project_root() / geojson_path
    if not full_path.exists():
        raise FileNotFoundError(f"GeoJSON not found: {full_path}")
    with open(full_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def geojson_to_m2m_spatial_filter(geojson: Dict) -> Dict:
    if geojson.get('type') == 'FeatureCollection':
        geometry = geojson['features'][0]['geometry']
    elif geojson.get('type') == 'Feature':
        geometry = geojson['geometry']
    else:
        geometry = geojson
    return {'filterType': 'geojson', 'geoJson': geometry}


def get_bbox_from_geojson(geojson: Dict) -> Tuple[float, float, float, float]:
    if geojson.get('type') == 'FeatureCollection':
        coords = geojson['features'][0]['geometry']['coordinates'][0]
    elif geojson.get('type') == 'Feature':
        coords = geojson['geometry']['coordinates'][0]
    else:
        coords = geojson['coordinates'][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def format_file_size(size_bytes: float) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"