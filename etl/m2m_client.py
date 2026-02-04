"""
Cliente para USGS Machine-to-Machine (M2M) API
Maneja autenticación, búsqueda de escenas y descarga de bandas Landsat
"""

import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import (
    load_config,
    load_env,
    setup_logger,
    geojson_to_m2m_spatial_filter,
    format_file_size,
    get_sensor_from_entity_id
)


class M2MClient:
    """
    Cliente para la API Machine-to-Machine de USGS Earth Explorer
    
    Attributes:
        username: Usuario de USGS
        token: Token de aplicación de USGS
        api_key: API key obtenido tras login (sesión)
        base_url: URL base del servicio M2M
        session: Sesión de requests con retry automático
        logger: Logger para auditoría
    """
    
    BASE_URL = "https://m2m.cr.usgs.gov/api/api/json/stable/"
    
    def __init__(
        self,
        username: Optional[str] = None,
        token: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 300,
        logger: Optional[logging.Logger] = None,
        dry_run: bool = False
    ):
        """
        Inicializa el cliente M2M
        
        Args:
            username: Username de USGS (usa .env si es None)
            token: Application token (usa .env si es None)
            max_retries: Número de reintentos para requests
            timeout: Timeout en segundos para requests
            logger: Logger personalizado (crea uno si es None)
            dry_run: Si es True, simula la ejecución sin realizar descargas reales.
        """
        # Cargar credenciales
        env = load_env()
        self.username = username or env.get('M2M_USERNAME')
        self.token = token or env.get('M2M_TOKEN')
        self.dry_run = dry_run # Store dry_run flag
        
        if not self.username or not self.token:
            raise ValueError("M2M credentials not found. Set M2M_USERNAME and M2M_TOKEN in .env")
        
        # Configuración
        self.base_url = self.BASE_URL
        self.timeout = timeout
        self.api_key = None
        
        # Logger
        self.logger = logger or setup_logger(
            'M2MClient',
            log_file='m2m_client.log',
            level='INFO'
        )
        
        # Configurar sesión con retry
        self.session = self._create_session(max_retries)
        
        # Cargar configuración de datasets
        self.config = load_config()
    
    def _create_session(self, max_retries: int) -> requests.Session:
        """
        Crea una sesión de requests con retry automático
        
        Args:
            max_retries: Número de reintentos
        
        Returns:
            requests.Session: Sesión configurada
        """
        session = requests.Session()
        
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def login(self) -> bool:
        """
        Autentica en M2M API y obtiene API key
        
        Returns:
            bool: True si login exitoso, False en caso contrario
        
        Raises:
            requests.RequestException: Si hay error de red
        """
        login_url = f"{self.base_url}login-token"
        payload = {
            "username": self.username,
            "token": self.token
        }
        
        self.logger.info(f"Logging in to M2M API as {self.username}")
        
        try:
            response = self.session.post(
                login_url,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('errorCode'):
                self.logger.error(f"Login failed: {data['errorCode']} - {data['errorMessage']}")
                return False
            
            self.api_key = data.get('data')
            self.logger.info("Login successful, API key obtained")
            return True
            
        except requests.RequestException as e:
            self.logger.error(f"Login request failed: {e}")
            raise
    
    def logout(self):
        """Cierra la sesión en M2M API"""
        if not self.api_key:
            return
        
        logout_url = f"{self.base_url}logout"
        headers = {'X-Auth-Token': self.api_key}
        
        try:
            self.session.post(logout_url, headers=headers, timeout=self.timeout)
            self.logger.info("Logged out successfully")
        except Exception as e:
            self.logger.warning(f"Logout failed: {e}")
        finally:
            self.api_key = None
    
    def _send_request(
        self,
        endpoint: str,
        payload: Dict,
        exit_on_error: bool = False,
        return_full_response: bool = False
    ) -> Optional[Dict]:
        """
        Envía un request a la API M2M
        
        Args:
            endpoint: Endpoint de la API (sin base_url)
            payload: Datos JSON a enviar
            exit_on_error: Si True, termina el programa en caso de error
            return_full_response: Si True, retorna la respuesta JSON completa. Si False, solo 'data'.
        
        Returns:
            Optional[Dict]: Respuesta de la API o None si hay error
        """
        if not self.api_key:
            raise RuntimeError("Not logged in. Call login() first.")
        
        url = f"{self.base_url}{endpoint}"
        headers = {'X-Auth-Token': self.api_key}
        
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('errorCode'):
                self.logger.error(f"API error on {endpoint}: {data['errorCode']} - {data['errorMessage']}")
                if exit_on_error:
                    sys.exit(1)
                return None
            
            if return_full_response:
                return data
            return data.get('data')
            
        except requests.RequestException as e:
            response_text = "N/A"
            if hasattr(e, 'response') and e.response is not None:
                response_text = e.response.text
            
            self.logger.error(f"Request failed on {endpoint}: {e}. Response: {response_text}")
            if exit_on_error:
                sys.exit(1)
            return None
    
    def search_scenes(
        self,
        dataset_name: str,
        spatial_filter: Dict,
        temporal_filter: Dict,
        cloud_cover_filter: Optional[Dict] = None,
        max_results: int = 100
    ) -> List[Dict]:
        """
        Busca escenas Landsat según filtros
        
        Args:
            dataset_name: Nombre del dataset M2M (ej: 'landsat_ot_c2_l2')
            spatial_filter: Filtro espacial (usar geojson_to_m2m_spatial_filter)
            temporal_filter: {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'}
            cloud_cover_filter: {'min': 0, 'max': 40} (opcional)
            max_results: Número máximo de resultados
        
        Returns:
            List[Dict]: Lista de escenas encontradas
        """
        payload = {
            'datasetName': dataset_name,
            'maxResults': max_results,
            'sceneFilter': {
                'spatialFilter': spatial_filter,
                'acquisitionFilter': temporal_filter
            }
        }
        
        if cloud_cover_filter:
            payload['sceneFilter']['cloudCoverFilter'] = cloud_cover_filter
        
        self.logger.info(
            f"Searching scenes in {dataset_name} "
            f"from {temporal_filter['start']} to {temporal_filter['end']}"
        )
        
        result = self._send_request('scene-search', payload)
        
        if result:
            scenes = result.get('results', [])
            self.logger.info(f"Found {len(scenes)} scenes")
            return scenes
        
        return []
    
    def add_scenes_to_list(
        self,
        list_id: str,
        entity_ids: List[str],
        dataset_name: str
    ) -> int:
        """
        Añade escenas a una lista temporal para descarga
        
        Args:
            list_id: ID de la lista temporal
            entity_ids: Lista de entity IDs
            dataset_name: Nombre del dataset
        
        Returns:
            int: Número de escenas añadidas
        """
        payload = {
            'listId': list_id,
            'idField': 'entityId',
            'entityIds': entity_ids,
            'datasetName': dataset_name
        }
        
        self.logger.info(f"Adding {len(entity_ids)} scenes to list {list_id}")
        
        result = self._send_request('scene-list-add', payload)
        
        if result:
            count = result if isinstance(result, int) else len(entity_ids)
            self.logger.info(f"Added {count} scenes to list")
            return count
        
        return 0
    
    def get_download_options(
        self,
        list_id: str,
        dataset_name: str,
        file_type: str = 'band'
    ) -> List[Dict]:
        """
        Obtiene las opciones de descarga para una lista de escenas
        
        Args:
            list_id: ID de la lista temporal
            dataset_name: Nombre del dataset
            file_type: Tipo de archivo ('band', 'bundle', 'band_group')
        
        Returns:
            List[Dict]: Lista de productos disponibles para descarga
        """
        payload = {
            'listId': list_id,
            'datasetName': dataset_name,
            'fileType': file_type
        }
        
        self.logger.info(f"Getting download options for list {list_id} (type: {file_type})")
        
        result = self._send_request('download-options', payload)
        
        if result:
            products = result if isinstance(result, list) else []
            self.logger.info(f"Found {len(products)} products available")
            return products
        
        return []
    
    def filter_bands(
        self,
        products: List[Dict],
        band_names: List[str]
    ) -> List[Dict]:
        """
        Filtra productos para obtener solo las bandas especificadas
        
        Args:
            products: Lista de productos de download-options
            band_names: Lista de nombres de bandas a descargar (ej: ['SR_B3', 'SR_B6', 'QA_PIXEL'])
        
        Returns:
            List[Dict]: Lista de descargas filtradas con formato {'entityId': str, 'productId': str}
        """
        downloads = []
        
        for product in products:
            # Verificar secondary downloads (bandas individuales)
            if product.get('secondaryDownloads'):
                for sd in product['secondaryDownloads']:
                    display_id = sd.get('displayId', '')
                    
                    # Verificar si la banda está en la lista
                    if any(band in display_id for band in band_names):
                        downloads.append({
                            'entityId': sd['entityId'],
                            'productId': sd['id']
                        })
        
        self.logger.info(f"Filtered {len(downloads)} bands from {len(products)} products")
        return downloads
    
    def request_downloads(
        self,
        downloads: List[Dict],
        label: Optional[str] = None
    ) -> Dict:
        """
        Solicita las descargas a M2M API
        
        Args:
            downloads: Lista de descargas con formato [{'entityId': str, 'productId': str}]
            label: Etiqueta opcional para la solicitud
        
        Returns:
            Dict: Resultado con 'availableDownloads' y 'preparingDownloads'
        """
        if not label:
            label = time.strftime("%Y%m%d_%H%M%S")
        
        payload = {
            'downloads': downloads,
            'label': label
        }
        
        self.logger.info(f"Requesting {len(downloads)} downloads with label {label}")
        
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Simulated download request for {len(downloads)} items.")
            # Return a mocked response for dry-run
            mock_available_downloads = []
            if downloads: # Simulate some available downloads if there were actual requests
                # Create a few dummy available downloads
                for i, dl in enumerate(downloads[:min(len(downloads), 2)]): # Mock up to 2 downloads
                    mock_available_downloads.append({'url': f"http://mock-download.com/dryrun_{i}.tif"})

            return {'availableDownloads': mock_available_downloads, 'preparingDownloads': []}

        result = self._send_request('download-request', payload)
        
        if result:
            available = len(result.get('availableDownloads', []))
            preparing = len(result.get('preparingDownloads', []))
            self.logger.info(f"Available: {available}, Preparing: {preparing}")
            return result
        
        return result
    
    def delete_list(self, list_id: str) -> bool:
        """
        Elimina una lista de escenas en M2M API
        
        Args:
            list_id: ID de la lista a eliminar
            
        Returns:
            bool: True si la eliminación fue exitosa, False en caso contrario.
        """
        self.logger.info(f"Deleting M2M list: {list_id}")

        if self.dry_run:
            self.logger.info(f"DRY-RUN: Simulated deletion of M2M list {list_id}.")
            return True

        payload = {'listId': list_id}
        # Use return_full_response=True because delete operation might return null data on success
        result = self._send_request('scene-list-remove', payload, return_full_response=True)
        
        # If result is not None, it means _send_request passed the errorCode check
        if result is not None:
            self.logger.info(f"M2M list {list_id} deleted successfully.")
            return True
        else:
            self.logger.warning(f"Failed to delete M2M list {list_id}.")
            return False
    
    def download_file(
        self,
        url: str,
        output_dir: Path,
        entity_id: str
    ) -> Tuple[bool, Optional[Path], Optional[str], str, Optional[float]]:
        """
        Descarga un archivo desde una URL
        
        Args:
            url: URL de descarga
            output_dir: Directorio donde guardar el archivo
            entity_id: Entity ID para logging
        
        Returns:
            Tuple[bool, Optional[Path], Optional[str], str, Optional[float]]: 
                (éxito, ruta_archivo, mensaje_error, url, duración_descarga_segundos)
        """
        if self.dry_run:
            self.logger.info(f"DRY-RUN: Simulated download of {url} for {entity_id}. No actual download or disk write.")
            # Simulate a small random duration for dry run
            simulated_duration = 0.5 + (hash(url) % 100) / 100.0 # Just to vary it a bit
            
            # Create a more realistic dummy filename for dry-run that _extract_band_name can parse
            # Example: LC90000002026000_SR_B4.tif
            # Extract basic part of entity_id for filename
            entity_part = entity_id.split('_')[0] if '_' in entity_id else entity_id
            dummy_filename = f"{entity_part}_SR_B4.tif" # Mock as a band 4 file
            
            dummy_filepath = output_dir / dummy_filename
            return True, dummy_filepath, None, url, simulated_duration

        start_time = time.time()
        download_duration_seconds = None
        try:
            self.logger.info(f"Downloading from {url}")
            
            response = self.session.get(url, stream=True, timeout=self.timeout)
            response.raise_for_status()
            
            # Intentar obtener nombre de archivo desde Content-Disposition
            content_disposition = response.headers.get('content-disposition')
            filename = None
            
            if content_disposition:
                # Ejemplo: attachment; filename="LC08_L2SP_..._SR_B1.TIF"
                import re
                fname_match = re.search(r'filename="?([^"]+)"?', content_disposition)
                if fname_match:
                    filename = fname_match.group(1)
            
            # Fallback: Extraer nombre de archivo desde URL si no hay header
            if not filename:
                parsed_url = urlparse(url)
                filename = Path(unquote(parsed_url.path)).name
                
            # Si aún no tenemos un nombre válido o es genérico, intentar inferir o loguear warning
            if not filename or filename == 'download':
                 # Último recurso, usar entity_id y timestamp para evitar sobreescritura, pero esto romperá el parser de bandas
                 filename = f"unknown_file_{entity_id}_{int(time.time())}.dat"
                 self.logger.warning(f"Could not determine filename for {entity_id}. Using: {filename}")

            filepath = output_dir / filename
            
            # Verificar Content-Type. Si es JSON, probablemente es un error o redirección inesperada.
            content_type = response.headers.get('Content-Type', '').lower()
            if 'application/json' in content_type:
                error_msg = f"Download failed for {entity_id}: Unexpected JSON response (Content-Type: {content_type}). Likely invalid/expired signature or file not found."
                self.logger.error(error_msg)
                if filepath.exists():
                    filepath.unlink() # Eliminar archivo JSON incorrecto
                return False, None, error_msg, url, None
            
            # Descargar con progress
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            # Verificar tamaño
            file_size = filepath.stat().st_size
            
            if file_size == 0:
                error_msg = f"Downloaded file is empty: {filepath}"
                self.logger.warning(error_msg)
                filepath.unlink()  # Eliminar archivo vacío
                return False, None, error_msg, url, None
            
            end_time = time.time()
            download_duration_seconds = end_time - start_time

            self.logger.info(
                f"Downloaded {filename} ({format_file_size(file_size)}) for {entity_id} in {download_duration_seconds:.2f}s"
            )
            
            return True, filepath, None, url, download_duration_seconds
            
        except Exception as e:
            end_time = time.time()
            download_duration_seconds = end_time - start_time # Still log duration even if it failed
            error_msg = f"Download failed for {entity_id}: {e}"
            self.logger.error(error_msg)
            return False, None, error_msg, url, download_duration_seconds
    
    def download_files_parallel(
        self,
        download_urls: List[Dict],
        output_dir: Path,
        max_workers: int = 5
    ) -> List[Tuple[str, bool, Optional[Path], Optional[str], str, Optional[float]]]:
        """
        Descarga múltiples archivos en paralelo
        
        Args:
            download_urls: Lista de dicts con 'url' y 'entityId'
            output_dir: Directorio de salida
            max_workers: Número de descargas concurrentes
        
        Returns:
            List[Tuple[str, bool, Optional[Path], Optional[str], str, Optional[float]]]: 
                Lista de (entity_id, éxito, ruta, error, url, duración_descarga)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        
        self.logger.info(f"Starting parallel download of {len(download_urls)} files")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.download_file,
                    item['url'],
                    output_dir,
                    item['entityId']
                ): item
                for item in download_urls
            }
            
            for future in as_completed(futures):
                item = futures[future]
                entity_id = item['entityId']
                original_url = item['url']
                try:
                    success, filepath, error, url_returned, duration = future.result()
                    results.append((entity_id, success, filepath, error, url_returned, duration))
                except Exception as e:
                    self.logger.error(f"Unexpected error for {entity_id} (URL: {original_url}): {e}")
                    results.append((entity_id, False, None, str(e), original_url, None))
        
        successful = sum(1 for _, success, _, _, _, _ in results if success)
        self.logger.info(f"Download completed: {successful}/{len(download_urls)} successful")
        
        return results
    
    def __enter__(self):
        """Context manager entry"""
        self.login()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.logout()


# =====================================================
# FUNCIONES DE CONVENIENCIA
# =====================================================

def get_required_bands_for_sensor(sensor: str, config: Optional[Dict] = None) -> List[str]:
    """
    Obtiene la lista de bandas requeridas para un sensor
    
    Args:
        sensor: Tipo de sensor ('OLI', 'ETM+', 'TM')
        config: Configuración (opcional, se carga si es None)
    
    Returns:
        List[str]: Lista de nombres de bandas
    """
    if config is None:
        config = load_config()
    
    # Mapeo de sensores a datasets
    sensor_to_dataset = {
        'OLI': 'landsat_8_9',
        'ETM+': 'landsat_7',
        'TM': 'landsat_5'
    }
    
    dataset_key = sensor_to_dataset.get(sensor)
    if not dataset_key:
        return []
    
    dataset_config = config.get('datasets', {}).get(dataset_key, {})
    bands = dataset_config.get('bands', {})
    
    # Retornar todas las bandas configuradas
    return [
        bands.get('green'),
        bands.get('swir1'),
        bands.get('qa_pixel'),
        bands.get('qa_radsat'),
        bands.get('qa_aerosol'),
        bands.get('metadata')
    ]


if __name__ == '__main__':
    """Test básico del cliente M2M"""
    
    print("=== Testing M2MClient ===\n")
    
    # Test de login
    try:
        with M2MClient() as client:
            print("Login successful")
            print(f"  API Key: {client.api_key[:20]}...")
            
    except Exception as e:
        print(f"Login failed: {e}")
