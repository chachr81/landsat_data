"""
Utilidades compartidas para el sistema ETL de Landsat
Proporciona funciones para paths, logging, configuración y helpers GeoJSON
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime
from urllib.parse import quote_plus

import yaml
from dotenv import dotenv_values


# =====================================================
# PATHS Y CONFIGURACIÓN
# =====================================================

def get_project_root() -> Path:
    """
    Obtiene el directorio raíz del proyecto.
    Busca desde el archivo actual hacia arriba hasta encontrar .env
    
    Returns:
        Path: Ruta absoluta al directorio raíz del proyecto
    """
    current = Path(__file__).resolve()
    
    # Subir desde etl/ al proyecto raíz
    for parent in [current.parent.parent, current.parent.parent.parent]:
        if (parent / '.env').exists():
            return parent
    
    # Fallback: asumir estructura estándar
    return current.parent.parent


def get_config_path() -> Path:
    """Retorna la ruta al archivo de configuración YAML principal"""
    return get_project_root() / 'config' / 'landsat_config.yaml'


def get_env_path() -> Path:
    """Retorna la ruta al archivo .env para secretos"""
    return get_project_root() / '.env'


def load_config() -> Dict:
    """
    Carga la configuración principal desde etl_config.yaml
    """
    config_path = get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_env() -> Dict[str, str]:
    """
    Carga los secretos desde .env
    """
    env_path = get_env_path()
    if not env_path.exists():
        # No es un error fatal si no hay .env, podría usar variables de sistema
        return {}
    return dotenv_values(env_path)


def get_data_dirs() -> Dict[str, Path]:
    """
    Obtiene las rutas de datos desde la config, creándolos si no existen.
    """
    config = load_config().get('STORAGE_PATHS', {})
    project_root = get_project_root()
    
    # Solo usamos TEMP en este flujo, pero preparamos para otros
    dirs = {
        'temp': project_root / config.get('TEMP', 'data/temp'),
        'logs': project_root / config.get('LOGS', 'logs/etl'),
    }
    
    for dir_path in dirs.values():
        dir_path.mkdir(parents=True, exist_ok=True)
    
    return dirs

# =====================================================
# LOGGING
# =====================================================

def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: Optional[str] = None,
    console: bool = True
) -> logging.Logger:
    """
    Configura un logger profesional.
    El nivel de log se toma del argumento, o de la config, o por defecto 'INFO'.
    """
    config = load_config().get('PROCESSING_CONFIG', {})
    log_level = level or config.get('LOG_LEVEL', 'INFO')

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    logger.handlers.clear()
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    if log_file:
        log_dir = get_data_dirs()['logs']
        file_handler = logging.FileHandler(log_dir / log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

# =====================================================
# GEOJSON Y GEOMETRÍA
# =====================================================

def load_aoi_geojson(geojson_path: Optional[str] = None) -> Dict:
    """
    Carga el GeoJSON del AOI desde la ruta definida en la configuración.
    """
    if geojson_path is None:
        config = load_config().get('aoi', {}) # Changed from 'AREA_OF_INTEREST' to 'aoi'
        geojson_path = config.get('geojson_path')
    
    if not geojson_path:
        raise ValueError("Ruta a GeoJSON no definida en config/landsat_config.yaml (sección 'aoi')") # Updated error message

    project_root = get_project_root()
    full_path = project_root / geojson_path
    
    if not full_path.exists():
        raise FileNotFoundError(f"GeoJSON file not found: {full_path}")
    
    with open(full_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# ... (El resto de funciones de geojson y db helpers se mantienen igual, ya que leen del .env que ahora solo tiene secretos)
# ...
def geojson_to_m2m_spatial_filter(geojson: Dict) -> Dict:
    """
    Convierte un GeoJSON a un spatial filter para M2M API
    
    Args:
        geojson: Diccionario GeoJSON (FeatureCollection o Feature)
    
    Returns:
        Dict: Spatial filter en formato M2M API
    """
    # Extraer la geometría del primer feature
    if geojson.get('type') == 'FeatureCollection':
        geometry = geojson['features'][0]['geometry']
    elif geojson.get('type') == 'Feature':
        geometry = geojson['geometry']
    else:
        geometry = geojson
    
    return {
        'filterType': 'geojson',
        'geoJson': geometry
    }


def get_bbox_from_geojson(geojson: Dict) -> Tuple[float, float, float, float]:
    """
    Extrae el bounding box de un GeoJSON
    
    Args:
        geojson: Diccionario GeoJSON
    
    Returns:
        Tuple[float, float, float, float]: (minX, minY, maxX, maxY)
    """
    # Extraer coordenadas
    if geojson.get('type') == 'FeatureCollection':
        coords = geojson['features'][0]['geometry']['coordinates'][0]
    elif geojson.get('type') == 'Feature':
        coords = geojson['geometry']['coordinates'][0]
    else:
        coords = geojson['coordinates'][0]
    
    # Calcular bbox
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    
    return (min(lons), min(lats), max(lons), max(lats))


# =====================================================
# DATABASE HELPERS
# =====================================================

def get_db_connection_string(env: Optional[Dict] = None) -> str:
    """
    Construye la cadena de conexión PostgreSQL desde .env
    
    Args:
        env: Diccionario de secretos de .env (opcional)
    
    Returns:
        str: Connection string en formato postgresql://
    
    Raises:
        ValueError: Si faltan variables requeridas en .env
    """
    if env is None:
        env = load_env()
    
    user = env.get('POSTGRES_USER')
    password = env.get('POSTGRES_PASSWORD')
    host = env.get('POSTGRES_HOST')
    port = env.get('POSTGRES_PORT')
    database = env.get('POSTGRES_DB')
    
    if not all([user, password, host, port, database]):
        raise ValueError(
            "Faltan secretos de BD en .env. Requeridos: "
            "POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB"
        )
    
    password_encoded = quote_plus(password)
    
    return f"postgresql://{user}:{password_encoded}@{host}:{port}/{database}"

# ... (JDBC helpers también se mantienen, ya que usan .env para credenciales)
def get_jdbc_url(env: Optional[Dict] = None) -> str:
    """
    Construye la URL JDBC para Spark
    
    Args:
        env: Diccionario de variables de entorno (opcional)
    
    Returns:
        str: JDBC URL
    
    Raises:
        ValueError: Si faltan variables requeridas en .env
    """
    if env is None:
        env = load_env()
    
    host = env.get('POSTGRES_HOST')
    port = env.get('POSTGRES_PORT')
    database = env.get('POSTGRES_DB')
    
    if not all([host, port, database]):
        raise ValueError(
            "Missing database credentials in .env. Required: "
            "POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB"
        )
    
    return f"jdbc:postgresql://{host}:{port}/{database}"


def get_jdbc_properties(env: Optional[Dict] = None) -> Dict[str, str]:
    """
    Obtiene las propiedades JDBC para Spark
    
    Args:
        env: Diccionario de variables de entorno (opcional)
    
    Returns:
        Dict[str, str]: Diccionario de propiedades JDBC
    
    Raises:
        ValueError: Si faltan variables requeridas en .env
    """
    if env is None:
        env = load_env()
    
    user = env.get('POSTGRES_USER')
    password = env.get('POSTGRES_PASSWORD')
    
    if not user or not password:
        raise ValueError(
            "Missing database credentials in .env. Required: "
            "POSTGRES_USER, POSTGRES_PASSWORD"
        )
    
    return {
        'user': user,
        'password': password,
        'driver': 'org.postgresql.Driver'
    }

# ... (El resto de helpers se mantiene sin cambios)


def format_file_size(size_bytes: float) -> str:
    """
    Formatea un tamaño de archivo en bytes a formato legible
    
    Args:
        size_bytes: Tamaño en bytes
    
    Returns:
        str: Tamaño formateado (ej: "123.45 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def get_sensor_from_entity_id(entity_id: str) -> str:
    """
    Extrae el sensor desde un entity_id de Landsat
    
    Args:
        entity_id: Entity ID de USGS (ej: LC08_L2SP_005054_20240115_02_T1 o LC80040532026022LGN00)
    
    Returns:
        str: Tipo de sensor ('OLI', 'ETM+', 'TM')
    
    Examples:
        >>> get_sensor_from_entity_id('LC08_L2SP_005054_20240115_02_T1')
        'OLI'
        >>> get_sensor_from_entity_id('LC80040532026022LGN00')
        'OLI'
        >>> get_sensor_from_entity_id('LE07_L2SP_005054_20200115_02_T1')
        'ETM+'
    """
    if '_' in entity_id:
        satellite = entity_id[:4]
    else:
        satellite = entity_id[:3]
    
    sensor_map = {
        'LC08': 'OLI',
        'LC09': 'OLI',
        'LC8': 'OLI',
        'LC9': 'OLI',
        'LE07': 'ETM+',
        'LE7': 'ETM+',
        'LT05': 'TM',
        'LT04': 'TM',
        'LT5': 'TM',
        'LT4': 'TM',
    }
    
    return sensor_map.get(satellite, 'UNKNOWN')


# =====================================================
# TESTING
# =====================================================

if __name__ == '__main__':
    """Tests básicos de las funciones de utilidad"""
    
    print("=== Testing utils.py ===\n")
    
    # Test paths
    print(f"Project root: {get_project_root()}")
    print(f"Config path: {get_config_path()}")
    print(f"Env path: {get_env_path()}")
    
    # Test load config
    try:
        config = load_config()
        print(f"\nConfig loaded: {len(config)} keys")
    except Exception as e:
        print(f"\nConfig error: {e}")
    
    # Test load env
    try:
        env = load_env()
        print(f"Env loaded: {len(env)} variables")
    except Exception as e:
        print(f"Env error: {e}")
    
    # Test data dirs
    try:
        dirs = get_data_dirs()
        print(f"Data dirs: {list(dirs.keys())}")
    except Exception as e:
        print(f"Data dirs error: {e}")
    
    # Test logger
    logger = setup_logger('test_logger', 'test.log')
    logger.info("Test log message")
    print("Logger configured")
    
    # Test GeoJSON
    try:
        geojson = load_aoi_geojson()
        bbox = get_bbox_from_geojson(geojson)
        print(f"GeoJSON loaded, bbox: {bbox}")
    except Exception as e:
        print(f"GeoJSON error: {e}")
    
    # Test sensor detection
    test_ids = [
        'LC08_L2SP_005054_20240115_02_T1',
        'LE07_L2SP_005054_20200115_02_T1',
        'LT05_L2SP_005054_19900115_02_T1'
    ]
    for eid in test_ids:
        sensor = get_sensor_from_entity_id(eid)
        print(f"{eid[:4]} -> {sensor}")
    
    print("\n=== Tests completed ===")
