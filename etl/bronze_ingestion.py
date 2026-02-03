"""
Orquestador de ingesta Bronze Layer
Coordina descarga M2M, parsing MTL e inserción en PostGIS Raster
"""

import shutil
import subprocess
import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

from .m2m_client import M2MClient
from .mtl_parser import MTLParser
from .utils import (
    load_config,
    load_env,
    load_aoi_geojson,
    geojson_to_m2m_spatial_filter,
    setup_logger,
    get_db_connection_string,
    get_sensor_from_entity_id
)


class BronzeIngestion:
    """
    Orquestador para la ingesta de datos Landsat en la capa Bronze
    
    Pipeline:
        1. Buscar escenas en M2M API
        2. Descargar bandas + MTL a /tmp
        3. Parsear MTL y registrar en bronze.landsat_scenes
        4. Cargar bandas como raster con raster2pgsql
        5. Actualizar bronze.download_log
        6. Limpiar archivos temporales
    """
    
    def __init__(
        self,
        start_date: str,
        end_date: str,
        max_cloud_cover: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        dry_run: bool = False
    ):
        """
        Inicializa el orquestador de ingesta
        
        Args:
            start_date: Fecha inicio (YYYY-MM-DD)
            end_date: Fecha fin (YYYY-MM-DD)
            max_cloud_cover: Cobertura máxima de nubes (%)
            logger: Logger personalizado
            dry_run: Si es True, simula la ejecución sin realizar cambios persistentes.
        """
        self.start_date = start_date
        self.end_date = end_date
        self.dry_run = dry_run
        
        self.env = load_env()
        self.config = load_config()
        
        self.max_cloud_cover = max_cloud_cover or int(self.env.get('MAX_CLOUD_COVER', 40))
        
        self.logger = logger or setup_logger(
            'BronzeIngestion',
            log_file=f'bronze_ingestion_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
            level=self.env.get('LOG_LEVEL', 'INFO')
        )
        
        self.temp_dir = self._get_temp_dir()
        self.db_conn_str = get_db_connection_string(self.env)
        
        self._check_dependencies()
    
    def _get_temp_dir(self) -> Path:
        """Obtiene el directorio temporal"""
        from .utils import get_project_root
        project_root = get_project_root()
        temp_path = project_root / self.env.get('DATA_TEMP_DIR', 'data/temp')
        
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Temporary directory '{temp_path}' would be used but not created.")
            return temp_path # Return path, but don't create it
        
        temp_path.mkdir(parents=True, exist_ok=True)
        return temp_path
    
    def _check_dependencies(self):
        """Verifica que existan las herramientas necesarias"""
        if self.dry_run:
            self.logger.info("DRY-RUN: Skipping dependency checks as no external tools will be invoked.")
            return

        if not shutil.which('raster2pgsql'):
            raise RuntimeError("raster2pgsql not found. Install PostGIS tools.")
        
        if not shutil.which('psql'):
            raise RuntimeError("psql not found. Install PostgreSQL client.")
        
        self.logger.info("Dependencies check passed")
    
    def run(self, datasets: Optional[List[str]] = None) -> Dict:
        """
        Ejecuta el pipeline completo de ingesta
        
        Args:
            datasets: Lista de datasets a procesar. Si None, procesa todos.
                     Opciones: ['landsat_ot_c2_l2', 'landsat_etm_c2_l2', 'landsat_tm_c2_l2']
        
        Returns:
            Dict: Estadísticas de la ingesta
        """
        stats = {
            'total_scenes': 0,
            'total_bands': 0,
            'successful_scenes': 0,
            'failed_scenes': 0,
            'errors': []
        }
        
        if datasets is None:
            datasets = [
                'landsat_ot_c2_l2',
                'landsat_etm_c2_l2',
                'landsat_tm_c2_l2'
            ]
        
        self.logger.info(f"Starting ingestion from {self.start_date} to {self.end_date}")
        self.logger.info(f"Datasets: {datasets}")
        
        aoi_geojson = load_aoi_geojson()
        spatial_filter = geojson_to_m2m_spatial_filter(aoi_geojson)
        
        temporal_filter = {
            'start': self.start_date,
            'end': self.end_date
        }
        
        cloud_filter = {
            'min': 0,
            'max': self.max_cloud_cover
        }
        
        with M2MClient(logger=self.logger, dry_run=self.dry_run) as client:
            for dataset_name in datasets:
                try:
                    dataset_stats = self._process_dataset(
                        client,
                        dataset_name,
                        spatial_filter,
                        temporal_filter,
                        cloud_filter
                    )
                    
                    stats['total_scenes'] += dataset_stats['total_scenes']
                    stats['total_bands'] += dataset_stats['total_bands']
                    stats['successful_scenes'] += dataset_stats['successful_scenes']
                    stats['failed_scenes'] += dataset_stats['failed_scenes']
                    stats['errors'].extend(dataset_stats['errors'])
                    
                except Exception as e:
                    error_msg = f"Error processing dataset {dataset_name}: {e}"
                    self.logger.error(error_msg)
                    stats['errors'].append(error_msg)
        
        self.logger.info(f"Ingestion completed: {stats}")
        return stats
    
    def _process_dataset(
        self,
        client: M2MClient,
        dataset_name: str,
        spatial_filter: Dict,
        temporal_filter: Dict,
        cloud_filter: Dict
    ) -> Dict:
        """
        Procesa un dataset específico
        
        Args:
            client: Cliente M2M autenticado
            dataset_name: Nombre del dataset M2M
            spatial_filter: Filtro espacial
            temporal_filter: Filtro temporal
            cloud_filter: Filtro de nubes
        
        Returns:
            Dict: Estadísticas del dataset
        """
        stats = {
            'total_scenes': 0,
            'total_bands': 0,
            'successful_scenes': 0,
            'failed_scenes': 0,
            'errors': []
        }
        
        self.logger.info(f"Searching scenes in {dataset_name}")
        
        scenes = client.search_scenes(
            dataset_name,
            spatial_filter,
            temporal_filter,
            cloud_filter,
            max_results=1000
        )
        
        if not scenes:
            self.logger.warning(f"No scenes found in {dataset_name}")
            return stats
        
        stats['total_scenes'] = len(scenes)
        entity_ids = [scene['entityId'] for scene in scenes]
        
        existing_ids = self._get_existing_entity_ids(entity_ids)
        new_entity_ids = [eid for eid in entity_ids if eid not in existing_ids]
        
        if not new_entity_ids:
            self.logger.info(f"All {len(entity_ids)} scenes already in database")
            return stats
        
        self.logger.info(f"Processing {len(new_entity_ids)} new scenes (skipping {len(existing_ids)} existing)")
        
        list_id = f"temp_{dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        client.add_scenes_to_list(list_id, new_entity_ids, dataset_name)
        
        products = client.get_download_options(list_id, dataset_name, file_type='band')
        
        try: # Ensure list cleanup even if scene processing fails
            for entity_id in new_entity_ids:
                try:
                    scene_stats = self._process_scene(client, entity_id, products, dataset_name)
                    stats['total_bands'] += scene_stats['bands_processed']
                    stats['successful_scenes'] += 1
                    
                except Exception as e:
                    error_msg = f"Failed to process scene {entity_id}: {e}"
                    self.logger.error(error_msg)
                    stats['failed_scenes'] += 1
                    stats['errors'].append(error_msg)
                    # Call _log_download_failure with appropriate placeholders for a scene-level error
                    self._log_download_failure(entity_id, "SCENE_PROCESSING_ERROR", "N/A", None, str(e))
        finally:
            # Clean up the M2M list
            if self.dry_run:
                self.logger.info(f"DRY-RUN: Would delete temporary M2M list: {list_id}")
            else:
                try:
                    client.delete_list(list_id)
                    self.logger.info(f"Deleted temporary M2M list: {list_id}")
                except Exception as e:
                    self.logger.error(f"Failed to delete M2M list {list_id}: {e}")
        
        return stats
    
    def _process_scene(
        self,
        client: M2MClient,
        entity_id: str,
        products: List[Dict],
        dataset_name: str
    ) -> Dict:
        """
        Procesa una escena individual
        
        Args:
            client: Cliente M2M
            entity_id: Entity ID de la escena
            products: Lista de productos disponibles
            dataset_name: Nombre del dataset
        
        Returns:
            Dict: Estadísticas de la escena
        """
        self.logger.info(f"Processing scene: {entity_id}")
        
        sensor = get_sensor_from_entity_id(entity_id)
        required_bands = self._get_required_bands(sensor)
        
        scene_products = [p for p in products if p.get('entityId') == entity_id]
        downloads = client.filter_bands(scene_products, required_bands)
        
        if not downloads:
            raise ValueError(f"No bands available for {entity_id}")
        
        download_results = client.request_downloads(downloads, label=entity_id)
        
        available = download_results.get('availableDownloads', [])
        
        if not available:
            raise ValueError(f"No downloads available for {entity_id}")
        
        scene_dir = self.temp_dir / entity_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        
        
        download_urls = [{'url': item['url'], 'entityId': entity_id} for item in available]
        
        # results: List[Tuple[str, bool, Optional[Path], Optional[str], str, Optional[float]]]
        # (entity_id_dl, success_dl, filepath_dl, error_dl, url_dl, duration_dl)
        download_results = client.download_files_parallel(
            download_urls,
            scene_dir,
            max_workers=int(self.env.get('MAX_CONCURRENT_DOWNLOADS', 5))
        )
        
        successfully_downloaded_tifs: List[Tuple[Path, str]] = []
        bands_processed_count = 0
        
        for entity_id_dl, success_dl, filepath_dl, error_dl, url_dl, duration_dl in download_results:
            band_name_dl: Optional[str] = None
            if filepath_dl:
                band_name_dl = self._extract_band_name(filepath_dl.name)
            
            if success_dl and filepath_dl and band_name_dl:
                self._log_download_success(entity_id_dl, band_name_dl, filepath_dl, url_dl, duration_dl)
                successfully_downloaded_tifs.append((filepath_dl, band_name_dl))
                bands_processed_count += 1
            else:
                log_band_name = band_name_dl if band_name_dl else "UNKNOWN_BAND"
                log_error_msg = error_dl if error_dl else "Unknown download error"
                self._log_download_failure(entity_id_dl, log_band_name, url_dl, duration_dl, log_error_msg)

        if not successfully_downloaded_tifs:
            raise ValueError(f"No TIF files were successfully downloaded for {entity_id}. Cannot proceed with ingestion.")
        
        mtl_file = self._find_mtl_file(scene_dir, entity_id) # Pass entity_id here
        
        if not mtl_file:
            raise ValueError(f"MTL file not found for {entity_id}")
        
        scene_id = self._insert_scene_metadata(mtl_file, dataset_name)
        
        bands_ingested = self._ingest_bands_to_postgis(successfully_downloaded_tifs, scene_id, entity_id)
        
        shutil.rmtree(scene_dir, ignore_errors=True)
        
        self.logger.info(f"Scene {entity_id} processed successfully: {bands_ingested} bands ingested")
        
        return {'bands_processed': bands_ingested}
    
    def _get_required_bands(self, sensor: str) -> List[str]:
        """
        Obtiene las bandas requeridas para un sensor
        
        Args:
            sensor: Tipo de sensor
        
        Returns:
            List[str]: Lista de nombres de bandas
        """
        sensor_to_dataset = {
            'OLI': 'landsat_8_9',
            'ETM+': 'landsat_7',
            'TM': 'landsat_4_5'
        }
        
        dataset_key = sensor_to_dataset.get(sensor, 'landsat_8_9')
        dataset_config = self.config['datasets'][dataset_key]
        bands = dataset_config['bands']
        
        required = [
            bands.get('green'),
            bands.get('swir1'),
            bands.get('qa_pixel'),
            bands.get('qa_radsat'),
            bands.get('metadata')
        ]
        
        if bands.get('qa_aerosol'):
            required.append(bands['qa_aerosol'])
        
        return [b for b in required if b]
    
    def _find_mtl_file(self, scene_dir: Path, entity_id: str) -> Optional[Path]: # Added entity_id
        """
        Busca el archivo MTL en el directorio de la escena
        
        Args:
            scene_dir: Directorio de la escena
            entity_id: ID de la entidad (para crear nombre de MTL dummy en dry-run)
        
        Returns:
            Optional[Path]: Ruta al archivo MTL o None
        """
        if self.dry_run:
            dummy_mtl_path = scene_dir / f"{entity_id}_MTL.txt"
            self.logger.info(f"DRY-RUN: Simulating finding MTL file for {entity_id}. Would use dummy path: {dummy_mtl_path}")
            return dummy_mtl_path

        for pattern in ['*MTL.txt', '*MTL.xml', '*_MTL*']:
            files = list(scene_dir.glob(pattern))
            if files:
                return files[0]
        return None
    
    def _insert_scene_metadata(self, mtl_file: Path, dataset_name: str) -> int:
        """
        Parsea el MTL e inserta metadatos en bronze.landsat_scenes
        
        Args:
            mtl_file: Ruta al archivo MTL
            dataset_name: Nombre del dataset M2M
        
        Returns:
            int: scene_id de la escena insertada
        """
        parser = MTLParser(mtl_file, dry_run=self.dry_run) # Pass dry_run flag
        metadata = parser.get_scene_metadata()
        
        metadata['dataset_name'] = dataset_name
        
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Would insert scene metadata for {metadata['entity_id']}.")
            self.logger.debug(f"DRY-RUN: Scene metadata: {metadata}")
            return 0 # Return a dummy scene_id for dry-run
        
        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            sql = """
                INSERT INTO bronze.landsat_scenes (
                    entity_id, display_id, dataset_name, sensor, satellite,
                    acquisition_date, path_row, cloud_cover, sun_azimuth, sun_elevation,
                    processing_level, footprint
                ) VALUES (
                    %(entity_id)s, %(display_id)s, %(dataset_name)s, %(sensor)s, %(satellite)s,
                    %(acquisition_date)s, %(path_row)s, %(cloud_cover)s, %(sun_azimuth)s,
                    %(sun_elevation)s, %(processing_level)s, 
                    ST_Transform(ST_GeomFromText(%(footprint_wkt)s, 4326), 32619)
                )
                ON CONFLICT (entity_id) DO UPDATE SET
                    cloud_cover = EXCLUDED.cloud_cover,
                    sun_azimuth = EXCLUDED.sun_azimuth,
                    sun_elevation = EXCLUDED.sun_elevation
                RETURNING scene_id
            """
            
            cursor.execute(sql, metadata)
            scene_id = cursor.fetchone()[0]
            
            conn.commit()
            
            self.logger.info(f"Inserted scene metadata: {metadata['entity_id']} (scene_id={scene_id})")
            
            return scene_id
            
        finally:
            cursor.close()
            conn.close()
    
    def _ingest_bands_to_postgis(self, successfully_downloaded_tifs: List[Tuple[Path, str]], scene_id: int, entity_id: str) -> int:
        """
        Ingesta bandas raster usando raster2pgsql
        
        Args:
            successfully_downloaded_tifs: Lista de tuplas (Path al TIF, nombre de la banda)
            scene_id: ID de la escena en bronze.landsat_scenes
            entity_id: Entity ID de la escena
        
        Returns:
            int: Número de bandas procesadas
        """
        if not successfully_downloaded_tifs:
            self.logger.warning(f"No TIF files to ingest for {entity_id}")
            return 0
        
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Skipping PostGIS ingestion for {len(successfully_downloaded_tifs)} bands from scene {entity_id}")
            return len(successfully_downloaded_tifs) # Simulate success
        
        acquisition_year = self._get_scene_year(scene_id)
        
        bands_processed = 0
        
        for tif_file, band_name in successfully_downloaded_tifs:
            # Skip metadata files ingestion into raster table
            if tif_file.suffix.lower() in ['.txt', '.xml'] or band_name == 'MTL':
                continue

            try:
                self._ingest_single_band(
                    tif_file,
                    scene_id,
                    band_name,
                    acquisition_year,
                    entity_id
                )
                
                bands_processed += 1
                
            except Exception as e:
                error_msg = f"Failed to ingest band {tif_file.name}: {e}"
                self.logger.error(error_msg)
                # Note: Download logging for this band already occurred in _process_scene.
                # This error is specifically for ingestion into PostGIS.
        
        return bands_processed
    
    def _get_scene_year(self, scene_id: int) -> int:
        """Obtiene el año de adquisición de una escena"""
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Simulating scene year retrieval for scene_id {scene_id}")
            # Retornar un año por defecto para dry_run si scene_id no es 0
            if scene_id != 0:
                return datetime.now().year
            else:
                return 2026 # For dry-run test
        
        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT EXTRACT(YEAR FROM acquisition_date)::int FROM bronze.landsat_scenes WHERE scene_id = %s",
                (scene_id,)
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            conn.close()
    
    def _extract_band_name(self, filename: str) -> Optional[str]:
        """
        Extrae el nombre de la banda desde el nombre del archivo
        
        Args:
            filename: Nombre del archivo (ej: LC08_..._SR_B3.TIF)
        
        Returns:
            Optional[str]: Nombre de la banda o None
        """
        parts = filename.split('_')
        
        for i, part in enumerate(parts):
            if part in ['SR', 'ST', 'QA']:
                if i + 1 < len(parts):
                    band = f"{part}_{parts[i+1].split('.')[0]}"
                    return band
        
        if 'MTL' in filename.upper():
            return 'MTL'
        
        return None
    
    def _ingest_single_band(
        self,
        tif_file: Path,
        scene_id: int,
        band_name: str,
        year: int,
        entity_id: str
    ):
        """
        Ingesta una banda usando raster2pgsql | psql
        
        Args:
            tif_file: Archivo TIF
            scene_id: ID de la escena
            band_name: Nombre de la banda
            year: Año de adquisición
            entity_id: Entity ID
        """
        target_table = f"bronze.landsat_bands_{year}"
        temp_table = f"bronze.temp_ingest_{uuid.uuid4().hex}"
        
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Would ingest band '{band_name}' for scene {entity_id} (file: {tif_file.name}) into {target_table}.")
            self.logger.debug(f"DRY-RUN: raster2pgsql command would be: raster2pgsql -d -t 512x512 -s 32618 -F {tif_file} {temp_table}")
            self.logger.debug(f"DRY-RUN: SQL INSERT/UPDATE would be: INSERT INTO {target_table} ...; UPDATE {target_table} ...")
            return

        # Usamos -d para DROP/CREATE de la tabla temporal
        # -Y conserva COPY statement en lugar de INSERT, mas rapido
        # Eliminado -s 32618 para permitir autodetección del SRID del GeoTIFF
        raster2pgsql_cmd = [
            'raster2pgsql',
            '-d',  # Drop and recreate (create mode)
            '-t', '512x512',
            '-F',
            str(tif_file),
            temp_table
        ]
        
        psql_cmd = [
            'psql',
            self.db_conn_str
        ]
        
        self.logger.debug(f"Ingesting {band_name} for {entity_id} via {temp_table}")
        
        raster2pgsql_proc = subprocess.Popen(
            raster2pgsql_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        psql_proc = subprocess.Popen(
            psql_cmd,
            stdin=raster2pgsql_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        raster2pgsql_proc.stdout.close()
        
        stdout, stderr = psql_proc.communicate()
        
        # stdout contiene el output de psql, que puede ser verboso si hay mensajes informativos
        # Lo omitimos para no llenar el log con geometrías si por error se logueara el input
        # if stdout:
        #    self.logger.debug(f"psql stdout: {stdout.decode().strip()}")
        if stderr:
            stderr_text = stderr.decode().strip()
            # PostgreSQL envía NOTICES a stderr. Si es un aviso de que la tabla no existe (normal al usar -d), lo logueamos como INFO.
            if "NOTICE" in stderr_text and "does not exist, skipping" in stderr_text:
                self.logger.info(f"psql notice: {stderr_text}")
            else:
                self.logger.error(f"psql stderr: {stderr_text}")
        
        if psql_proc.returncode != 0:
            raise RuntimeError(f"raster2pgsql | psql failed: {stderr.decode()}")
        
        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            # Insertar en tabla destino desde temporal
            cursor.execute(
                f"""
                INSERT INTO {target_table} (scene_id, band_name, year, rast, filename)
                SELECT %s, %s, %s, rast, filename 
                FROM {temp_table};
                """,
                (scene_id, band_name, year)
            )
            
            # Borrar tabla temporal
            cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
            
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            try:
                with psycopg2.connect(self.db_conn_str) as clean_conn:
                    with clean_conn.cursor() as clean_cursor:
                         clean_cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
                         clean_conn.commit()
            except:
                pass
            raise e
            
        finally:
            cursor.close()
            conn.close()
        
        
        
    def _get_existing_entity_ids(self, entity_ids: List[str]) -> List[str]:
        """Consulta qué entity_ids ya existen en la BD"""
        if not entity_ids:
            return []
        
        if self.dry_run:
            self.logger.info("DRY-RUN: Simulating check for existing entity_ids. Returning empty list.")
            return [] # In dry-run, assume no existing to simulate full ingestion
        
        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT entity_id FROM bronze.landsat_scenes WHERE entity_id = ANY(%s)",
                (entity_ids,)
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()
    
    def _log_download_success(self, entity_id: str, band_name: str, tif_file: Path, download_url: str, download_duration_seconds: float):
        """Registra una descarga exitosa en bronze.download_log"""
        if self.dry_run:
            simulated_file_size_mb = 100.0 # Use a dummy size for dry-run logging
            self.logger.info(f"DRY-RUN: Would log successful download for {entity_id}/{band_name} (URL: {download_url}, Duration: {download_duration_seconds:.2f}s, Size: {simulated_file_size_mb:.2f}MB)")
            return

        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            file_size_mb = tif_file.stat().st_size / (1024 * 1024)
            
            cursor.execute(
                """
                INSERT INTO bronze.download_log (
                    entity_id, band_name, download_status, file_size_mb,
                    download_url, download_duration_seconds
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (entity_id, band_name, 'success', file_size_mb,
                 download_url, download_duration_seconds)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def _log_download_failure(self, entity_id: str, band_name: str, download_url: str, download_duration_seconds: Optional[float], error_message: str):
        """Registra un fallo de descarga en bronze.download_log"""
        if self.dry_run:
            self.logger.warning(f"DRY-RUN: Would log FAILED download for {entity_id}/{band_name} (URL: {download_url}, Duration: {download_duration_seconds:.2f}s, Error: {error_message})")
            return
            
        conn = psycopg2.connect(self.db_conn_str)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                """
                INSERT INTO bronze.download_log (
                    entity_id, band_name, download_status, error_message,
                    download_url, download_duration_seconds
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (entity_id, band_name, 'failed', error_message,
                 download_url, download_duration_seconds)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()


if __name__ == '__main__':
    """Test básico del orquestador"""
    
    ingestion = BronzeIngestion(
        start_date='2024-01-01',
        end_date='2024-01-31',
        max_cloud_cover=30
    )
    
    stats = ingestion.run(datasets=['landsat_ot_c2_l2'])
    
    print(f"\nIngestion completed:")
    print(f"  Total scenes: {stats['total_scenes']}")
    print(f"  Successful: {stats['successful_scenes']}")
    print(f"  Failed: {stats['failed_scenes']}")
    print(f"  Total bands: {stats['total_bands']}")
