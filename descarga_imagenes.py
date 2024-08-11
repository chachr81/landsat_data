import json
import requests
import sys
from urllib.parse import urlparse, unquote
import time
import os
import pandas as pd
import geopandas as gpd
from geojson import dump
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

import warnings
warnings.filterwarnings("ignore")

# Leer credenciales desde el archivo
def read_credentials(filepath):
    credentials = {}
    with open(filepath, 'r') as file:
        lines = file.readlines()
        for line in lines:
            if ':' in line:
                key, value = line.strip().split(':', 1)
                credentials[key.lower()] = value
            else:
                print(f"Línea ignorada: {line.strip()}")
    return credentials

# Función para enviar solicitudes al API
def send_request(url, data, headers=None, exit_if_no_response=True):
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()

        output = response.json()
        if output.get('errorCode'):
            print(f"{output['errorCode']} - {output['errorMessage']}")
            if exit_if_no_response:
                sys.exit()
            return False

        return output.get('data')

    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        if exit_if_no_response:
            sys.exit()
        return False

# Función para descargar archivos
def download_file(url, filename, entity_id, retries=10, delay_between_retries=10, delay_after_success=15):
    try:
        print(f"Iniciando descarga desde {url}")
        for attempt in range(retries):
            try:
                response = requests.get(url, stream=True, timeout=30)
                response.raise_for_status()

                parsed_url = urlparse(url)
                cleaned_filename = os.path.basename(parsed_url.path)
                cleaned_filename = unquote(cleaned_filename)
                filepath = os.path.join(os.path.dirname(filename), cleaned_filename)

                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                print(f"Archivo descargado: {filepath} para EntityId: {entity_id}")

                if os.path.getsize(filepath) > 0:
                    print(f"Archivo guardado correctamente: {filepath}")
                else:
                    print(f"Advertencia: El archivo {filepath} parece estar vacío.")
                
                # Delay después de una descarga exitosa
                time.sleep(delay_after_success)
                break

            except requests.exceptions.RequestException as e:
                print(f"Error en el intento {attempt + 1} al descargar {filename} desde {url}: {e}")
                if attempt < retries - 1:
                    print(f"Reintentando en {delay_between_retries} segundos...")
                    time.sleep(delay_between_retries)
                else:
                    print(f"Descarga fallida después de {retries} intentos.")
    
    except OSError as io_err:
        print(f"Error al escribir el archivo {filename}: {io_err}")

# Función para convertir shapefile a GeoJSON y obtener spatial_filter
def shapefile_to_geojson(shapefile_dir, output_dir):
    shapefiles = [f for f in os.listdir(shapefile_dir) if f.endswith('.shp')]

    if not shapefiles:
        raise FileNotFoundError("No shapefiles found in the directory.")

    shapefile_path = os.path.join(shapefile_dir, shapefiles[0])

    gdf = gpd.read_file(shapefile_path)
    gdf = gdf.to_crs(epsg=4326)

    geojson_dict = json.loads(gdf.to_json())

    output_filename = os.path.splitext(shapefiles[0])[0] + '.geojson'
    geojson_path = os.path.join(output_dir, output_filename)

    with open(geojson_path, 'w') as f:
        dump(geojson_dict, f)

    print(f"GeoJSON creado: {geojson_path}")

    spatial_filter = {
        'filterType': 'geojson',
        'geoJson': geojson_dict['features'][0]['geometry']
    }

    return spatial_filter

# Función para eliminar listas de escenas
def remove_scene_list(service_url, list_id, headers, secondary_list_id=None):
    # Remove the primary scene list
    remove_scnlst_payload = {"listId": list_id}
    send_request(service_url + "scene-list-remove", remove_scnlst_payload, headers)

    # Remove the secondary scene list if it exists
    if secondary_list_id:
        remove_scnlst2_payload = {"listId": secondary_list_id}
        send_request(service_url + "scene-list-remove", remove_scnlst2_payload, headers)

# Función principal
def main():
    cred_file_path = r'C:\Workspace\descarga_landsat\credenciales.txt'
    credentials = read_credentials(cred_file_path)
    username = credentials['username']
    token = credentials['token']

    service_url = "https://m2m.cr.usgs.gov/api/api/json/stable/"
    login_url = service_url + "login-token"
    payload = {"username": username, "token": token}
    response = requests.post(login_url, json=payload)

    if response.status_code == 200:
        api_key = response.json()['data']
        print('\nLogin Successful, API Key Received!')
        headers = {'X-Auth-Token': api_key}
    else:
        print("\nLogin was unsuccessful. Please check your token and try again.")
        print(response.json())
        raise Exception("Login failed")

    # Convertir shapefile a GeoJSON y obtener spatial_filter
    shapefile_dir = r'C:\Workspace\descarga_landsat\utils'
    output_dir = r'C:\Workspace\descarga_landsat\utils'
    spatial_filter = shapefile_to_geojson(shapefile_dir, output_dir)

    dataset_name = 'landsat_ot_c2_l2'
    label = time.strftime("%Y%m%d_%H%M%S")

    temporal_filter = {'start': '2023-12-01', 'end': '2024-12-31'}
    cloud_cover_filter = {'min': 0, 'max': 10}
    file_type = 'band'  # Cambiar entre 'band', 'bundle' y 'band_group' según sea necesario
    band_names = {'SR_B3', 'SR_B5', 'ANG', 'MTL'}  # Solo relevante para 'band'

    search_payload = {
        'datasetName': dataset_name,
        'sceneFilter': {
            'spatialFilter': spatial_filter,
            'acquisitionFilter': temporal_filter,
            'cloudCoverFilter': cloud_cover_filter
        }
    }

    scenes = send_request(service_url + "scene-search", search_payload, headers)
    if scenes:
        entity_ids = [scene['entityId'] for scene in scenes['results']]
        print("Escenas encontradas:", entity_ids)

        df_scenes = pd.json_normalize(scenes['results'])
        df_scenes_filtered = df_scenes[['cloudCover', 'entityId', 'displayId', 'temporalCoverage.endDate', 'temporalCoverage.startDate']]
        print(df_scenes_filtered)

        list_id = f"temp_{dataset_name}_list"
        scn_list_add_payload = {
            "listId": list_id,
            'idField': 'entityId',
            "entityIds": entity_ids,
            "datasetName": dataset_name
        }
        count = send_request(service_url + "scene-list-add", scn_list_add_payload, headers)
        print(f"Añadidas {count} escenas a la lista.")

        download_opt_payload = {
            "listId": list_id,
            "datasetName": dataset_name,
            "fileType": file_type
        }

        products = send_request(service_url + "download-options", download_opt_payload, headers)
        downloads = []

        if products:
            if file_type == 'bundle':
                for product in products:
                    if product.get("bulkAvailable") and product.get('downloadSystem') != 'folder':
                        downloads.append({"entityId": product["entityId"], "productId": product["id"]})
            elif file_type == 'band':
                for product in products:
                    if product.get("secondaryDownloads"):
                        for sd in product["secondaryDownloads"]:
                            if any(band in sd['displayId'] for band in band_names):
                                downloads.append({"entityId": sd["entityId"], "productId": sd["id"]})
            elif file_type == 'band_group':
                for product in products:
                    if product.get("secondaryDownloads"):
                        for sd in product["secondaryDownloads"]:
                            downloads.append({"entityId": sd["entityId"], "productId": sd["id"]})

        if downloads:
            download_req_payload = {
                "downloads": downloads,
                "label": label
            }

            print(f"Solicitando descargas para {len(downloads)} archivos...")
            download_results = send_request(service_url + "download-request", download_req_payload, headers)

            if download_results and 'availableDownloads' in download_results:
                print("Descargas disponibles:")

                download_dir = r'C:\Workspace\descarga_landsat\data'
                os.makedirs(download_dir, exist_ok=True)

                with ThreadPoolExecutor(max_workers=5) as executor:
                    for download in download_results['availableDownloads']:
                        entity_id = download.get('entityId', f"download_{download['downloadId']}")
                        filename = os.path.join(download_dir, os.path.basename(download['url']))
                        executor.submit(download_file, download['url'], filename, entity_id)
            else:
                print("No se encontraron archivos para descargar.")

            # Eliminar listas de escenas después de la descarga
            remove_scene_list(service_url, list_id, headers)

        else:
            print("No se seleccionaron productos para descargar. Revisa las bandas o el tipo de archivo.")
    else:
        print("No se encontraron escenas para los criterios dados.")

if __name__ == "__main__":
    main()