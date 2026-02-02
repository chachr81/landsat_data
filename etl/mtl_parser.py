"""
Parser para archivos de metadatos MTL de Landsat Collection 2
Soporta formatos .txt y .xml
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Union
from datetime import datetime


class MTLParser:
    """
    Parser para archivos de metadatos MTL de Landsat
    
    Formato TXT (Collection 2):
        GROUP = L2_METADATA_FILE
          GROUP = PRODUCT_CONTENTS
            LANDSAT_PRODUCT_ID = "LC08_L2SP_005054_20240115_20240126_02_T1"
            ...
          END_GROUP = PRODUCT_CONTENTS
        END_GROUP = L2_METADATA_FILE
    
    Formato XML:
        <LANDSAT_METADATA_FILE>
          <PRODUCT_METADATA>
            <LANDSAT_PRODUCT_ID>LC08_L2SP_005054_20240115_20240126_02_T1</LANDSAT_PRODUCT_ID>
            ...
          </PRODUCT_METADATA>
        </LANDSAT_METADATA_FILE>
    """
    
    def __init__(self, mtl_path: Union[str, Path], dry_run: bool = False):
        """
        Inicializa el parser
        
        Args:
            mtl_path: Ruta al archivo MTL (.txt o .xml)
            dry_run: Si es True, simula el parsing sin leer el archivo real.
        
        Raises:
            FileNotFoundError: Si el archivo no existe y no es dry_run
            ValueError: Si el formato no es soportado y no es dry_run
        """
        self.mtl_path = Path(mtl_path)
        self.dry_run = dry_run
        self.metadata = {}

        if self.dry_run:
            self._set_mock_metadata()
            return
        
        if not self.mtl_path.exists():
            raise FileNotFoundError(f"MTL file not found: {self.mtl_path}")
        
        self.format = self._detect_format()
        self.metadata = {}
    
    def _set_mock_metadata(self):
        """
        Establece metadatos de prueba para el modo dry_run
        """
        self.metadata = {
            'PRODUCT_CONTENTS.LANDSAT_PRODUCT_ID': 'LC90040532026030LGN00_DRYRUN',
            'PRODUCT_CONTENTS.LANDSAT_SCENE_ID': 'LC90040532026030LGN00_DRYRUN',
            'IMAGE_ATTRIBUTES.DATE_ACQUIRED': datetime(2026, 1, 15),
            'IMAGE_ATTRIBUTES.CLOUD_COVER': 95.0,
            'IMAGE_ATTRIBUTES.SUN_AZIMUTH': 150.0,
            'IMAGE_ATTRIBUTES.SUN_ELEVATION': 60.0,
            'IMAGE_ATTRIBUTES.WRS_PATH': 4,
            'IMAGE_ATTRIBUTES.WRS_ROW': 53,
            'PRODUCT_CONTENTS.PROCESSING_LEVEL': 'L2SP',
            'IMAGE_ATTRIBUTES.SPACECRAFT_ID': 'LANDSAT_9',
            'IMAGE_ATTRIBUTES.SENSOR_ID': 'OLI_TIRS',
            'PROJECTION_ATTRIBUTES.CORNER_UL_LAT_PRODUCT': 10.334375,
            'PROJECTION_ATTRIBUTES.CORNER_UL_LON_PRODUCT': -68.00742,
            'PROJECTION_ATTRIBUTES.CORNER_UR_LAT_PRODUCT': 10.332388,
            'PROJECTION_ATTRIBUTES.CORNER_UR_LON_PRODUCT': -67.494426,
            'PROJECTION_ATTRIBUTES.CORNER_LR_LAT_PRODUCT': 9.991131,
            'PROJECTION_ATTRIBUTES.CORNER_LR_LON_PRODUCT': -67.496022,
            'PROJECTION_ATTRIBUTES.CORNER_LL_LAT_PRODUCT': 9.993051,
            'PROJECTION_ATTRIBUTES.CORNER_LL_LON_PRODUCT': -68.008472
        }
    
    def _detect_format(self) -> str:
        """
        Detecta el formato del archivo MTL
        
        Returns:
            str: 'txt' o 'xml'
        
        Raises:
            ValueError: Si el formato no es reconocido
        """
        suffix = self.mtl_path.suffix.lower()
        
        if suffix == '.txt':
            return 'txt'
        elif suffix == '.xml':
            return 'xml'
        else:
            with open(self.mtl_path, 'r') as f:
                first_line = f.readline().strip()
                if first_line.startswith('GROUP') or '=' in first_line:
                    return 'txt'
                elif first_line.startswith('<?xml') or first_line.startswith('<'):
                    return 'xml'
        
        raise ValueError(f"Unknown MTL format: {self.mtl_path}")
    
    def parse(self) -> Dict:
        """
        Parsea el archivo MTL según su formato
        
        Returns:
            Dict: Diccionario con los metadatos parseados
        """
        if self.format == 'txt':
            self.metadata = self._parse_txt()
        else:
            self.metadata = self._parse_xml()
        
        return self.metadata
    
    def _parse_txt(self) -> Dict:
        """
        Parsea MTL en formato texto (key = value)
        
        Returns:
            Dict: Metadatos parseados
        """
        metadata = {}
        current_group = None
        
        with open(self.mtl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                if not line or line.startswith('END_GROUP'):
                    continue
                
                if line.startswith('GROUP'):
                    match = re.match(r'GROUP\s*=\s*(.+)', line)
                    if match:
                        current_group = match.group(1)
                    continue
                
                match = re.match(r'(\w+)\s*=\s*(.+)', line)
                if match:
                    key = match.group(1)
                    value = match.group(2).strip('"')
                    
                    if current_group:
                        key = f"{current_group}.{key}"
                    
                    metadata[key] = self._convert_value(value)
        
        return metadata
    
    def _parse_xml(self) -> Dict:
        """
        Parsea MTL en formato XML
        
        Returns:
            Dict: Metadatos parseados
        """
        tree = ET.parse(self.mtl_path)
        root = tree.getroot()
        
        metadata = {}
        
        def parse_element(element, prefix=''):
            for child in element:
                key = f"{prefix}.{child.tag}" if prefix else child.tag
                
                if len(child) > 0:
                    parse_element(child, key)
                else:
                    metadata[key] = self._convert_value(child.text)
        
        parse_element(root)
        
        return metadata
    
    def _convert_value(self, value: str):
        """
        Convierte valores string a tipos apropiados
        
        Args:
            value: Valor string del MTL
        
        Returns:
            Valor convertido (int, float, datetime, o str)
        """
        if not value:
            return None
        
        value = value.strip('"')
        
        if value.upper() in ('TRUE', 'YES'):
            return True
        if value.upper() in ('FALSE', 'NO'):
            return False
        
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            pass
        
        try:
            return datetime.strptime(value, '%Y:%j:%H:%M:%S.%f')
        except ValueError:
            pass
        
        return value
    
    def get_scene_metadata(self) -> Dict:
        """
        Extrae los metadatos clave para la tabla bronze.landsat_scenes
        
        Returns:
            Dict: Metadatos estructurados para inserción en BD
        """
        if not self.metadata:
            self.parse()
        
        def get_nested(key_variants):
            for key in key_variants:
                if key in self.metadata:
                    return self.metadata[key]
            return None
        
        entity_id = get_nested([
            'PRODUCT_CONTENTS.LANDSAT_PRODUCT_ID',
            'PRODUCT_METADATA.LANDSAT_PRODUCT_ID',
            'L1_METADATA_FILE.METADATA_FILE_INFO.LANDSAT_PRODUCT_ID'
        ])
        
        display_id = get_nested([
            'PRODUCT_CONTENTS.LANDSAT_SCENE_ID',
            'PRODUCT_METADATA.LANDSAT_SCENE_ID',
            'L1_METADATA_FILE.METADATA_FILE_INFO.LANDSAT_SCENE_ID'
        ])
        
        date_acquired = get_nested([
            'IMAGE_ATTRIBUTES.DATE_ACQUIRED',
            'PRODUCT_METADATA.DATE_ACQUIRED',
            'L1_METADATA_FILE.PRODUCT_METADATA.DATE_ACQUIRED'
        ])
        
        cloud_cover = get_nested([
            'IMAGE_ATTRIBUTES.CLOUD_COVER',
            'IMAGE_ATTRIBUTES.CLOUD_COVER_LAND',
            'PRODUCT_METADATA.CLOUD_COVER',
            'L1_METADATA_FILE.IMAGE_ATTRIBUTES.CLOUD_COVER'
        ])
        
        sun_azimuth = get_nested([
            'IMAGE_ATTRIBUTES.SUN_AZIMUTH',
            'PRODUCT_METADATA.SUN_AZIMUTH',
            'L1_METADATA_FILE.IMAGE_ATTRIBUTES.SUN_AZIMUTH'
        ])
        
        sun_elevation = get_nested([
            'IMAGE_ATTRIBUTES.SUN_ELEVATION',
            'PRODUCT_METADATA.SUN_ELEVATION',
            'L1_METADATA_FILE.IMAGE_ATTRIBUTES.SUN_ELEVATION'
        ])
        
        wrs_path = get_nested([
            'IMAGE_ATTRIBUTES.WRS_PATH',
            'PRODUCT_METADATA.WRS_PATH',
            'L1_METADATA_FILE.PRODUCT_METADATA.WRS_PATH'
        ])
        
        wrs_row = get_nested([
            'IMAGE_ATTRIBUTES.WRS_ROW',
            'PRODUCT_METADATA.WRS_ROW',
            'L1_METADATA_FILE.PRODUCT_METADATA.WRS_ROW'
        ])
        
        processing_level = get_nested([
            'PRODUCT_CONTENTS.PROCESSING_LEVEL',
            'PRODUCT_METADATA.PROCESSING_LEVEL',
            'L1_METADATA_FILE.PRODUCT_METADATA.DATA_TYPE'
        ])
        
        spacecraft_id = get_nested([
            'IMAGE_ATTRIBUTES.SPACECRAFT_ID',
            'PRODUCT_METADATA.SPACECRAFT_ID',
            'L1_METADATA_FILE.PRODUCT_METADATA.SPACECRAFT_ID'
        ])
        
        sensor_id = get_nested([
            'IMAGE_ATTRIBUTES.SENSOR_ID',
            'PRODUCT_METADATA.SENSOR_ID',
            'L1_METADATA_FILE.PRODUCT_METADATA.SENSOR_ID'
        ])
        
        corner_ul_lat = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_UL_LAT_PRODUCT',
            'PRODUCT_METADATA.CORNER_UL_LAT_PRODUCT'
        ])
        corner_ul_lon = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_UL_LON_PRODUCT',
            'PRODUCT_METADATA.CORNER_UL_LON_PRODUCT'
        ])
        corner_ur_lat = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_UR_LAT_PRODUCT',
            'PRODUCT_METADATA.CORNER_UR_LAT_PRODUCT'
        ])
        corner_ur_lon = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_UR_LON_PRODUCT',
            'PRODUCT_METADATA.CORNER_UR_LON_PRODUCT'
        ])
        corner_lr_lat = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_LR_LAT_PRODUCT',
            'PRODUCT_METADATA.CORNER_LR_LAT_PRODUCT'
        ])
        corner_lr_lon = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_LR_LON_PRODUCT',
            'PRODUCT_METADATA.CORNER_LR_LON_PRODUCT'
        ])
        corner_ll_lat = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_LL_LAT_PRODUCT',
            'PRODUCT_METADATA.CORNER_LL_LAT_PRODUCT'
        ])
        corner_ll_lon = get_nested([
            'PROJECTION_ATTRIBUTES.CORNER_LL_LON_PRODUCT',
            'PRODUCT_METADATA.CORNER_LL_LON_PRODUCT'
        ])
        
        footprint_wkt = None
        if all([corner_ul_lon, corner_ul_lat, corner_ur_lon, corner_ur_lat,
                corner_lr_lon, corner_lr_lat, corner_ll_lon, corner_ll_lat]):
            footprint_wkt = (
                f"POLYGON(("
                f"{corner_ul_lon} {corner_ul_lat}, "
                f"{corner_ur_lon} {corner_ur_lat}, "
                f"{corner_lr_lon} {corner_lr_lat}, "
                f"{corner_ll_lon} {corner_ll_lat}, "
                f"{corner_ul_lon} {corner_ul_lat}"
                f"))"
            )
        
        path_row = None
        if wrs_path and wrs_row:
            path_row = f"{wrs_path:03d}/{wrs_row:03d}"
        
        dataset_name = self._infer_dataset_name(spacecraft_id, sensor_id)
        
        return {
            'entity_id': entity_id,
            'display_id': display_id or entity_id,
            'dataset_name': dataset_name,
            'sensor': sensor_id,
            'satellite': spacecraft_id,
            'acquisition_date': date_acquired,
            'path_row': path_row,
            'cloud_cover': cloud_cover,
            'sun_azimuth': sun_azimuth,
            'sun_elevation': sun_elevation,
            'processing_level': processing_level,
            'footprint_wkt': footprint_wkt,
            'metadata_full': self.metadata
        }
    
    def _infer_dataset_name(self, spacecraft_id: Optional[str], sensor_id: Optional[str]) -> str:
        """
        Infiere el nombre del dataset M2M desde el spacecraft y sensor
        
        Args:
            spacecraft_id: ID de la nave (LANDSAT_8, LANDSAT_7, etc.)
            sensor_id: ID del sensor (OLI_TIRS, ETM, TM, etc.)
        
        Returns:
            str: Nombre del dataset M2M
        """
        if not spacecraft_id:
            return 'unknown'
        
        spacecraft_id = spacecraft_id.upper()
        
        if 'LANDSAT_8' in spacecraft_id or 'LANDSAT_9' in spacecraft_id:
            return 'landsat_ot_c2_l2'
        elif 'LANDSAT_7' in spacecraft_id:
            return 'landsat_etm_c2_l2'
        elif 'LANDSAT_4' in spacecraft_id or 'LANDSAT_5' in spacecraft_id:
            return 'landsat_tm_c2_l2'
        
        return 'unknown'


def parse_mtl_file(mtl_path: Union[str, Path]) -> Dict:
    """
    Función de conveniencia para parsear un archivo MTL
    
    Args:
        mtl_path: Ruta al archivo MTL
    
    Returns:
        Dict: Metadatos estructurados
    """
    parser = MTLParser(mtl_path)
    return parser.get_scene_metadata()


if __name__ == '__main__':
    """Test del parser MTL"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mtl_parser.py <path_to_mtl_file>")
        sys.exit(1)
    
    mtl_file = Path(sys.argv[1])
    
    if not mtl_file.exists():
        print(f"File not found: {mtl_file}")
        sys.exit(1)
    
    print(f"Parsing {mtl_file}...")
    
    try:
        metadata = parse_mtl_file(mtl_file)
        
        print("\nExtracted metadata:")
        for key, value in metadata.items():
            if key != 'metadata_full':
                print(f"  {key}: {value}")
        
        print(f"\nFull metadata keys: {len(metadata.get('metadata_full', {}))}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
