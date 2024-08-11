import json
import requests
from urllib.parse import urlparse, unquote
import time
import os
import pandas as pd
import geopandas as gpd
from geojson import Polygon, Feature, FeatureCollection, dump
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
def send_request(url, payload, headers=None):
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.json()['data']
    else:
        print(f"Error {response.status_code}: {response.text}")
        return None

# Función para descargar archivos
def download_file(url, filename, entity_id):
    try:
        print(f"Iniciando descarga desde {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()  # Verifica si hubo un error en la solicitud

        # Limpiar el nombre del archivo
        parsed_url = urlparse(url)
        cleaned_filename = os.path.basename(parsed_url.path)
        cleaned_filename = unquote(cleaned_filename)  # Decodificar caracteres especiales
        filepath = os.path.join(os.path.dirname(filename), cleaned_filename)

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"Archivo descargado: {filepath} para EntityId: {entity_id}")

        # Verificar tamaño del archivo para confirmar la descarga
        if os.path.getsize(filepath) > 0:
            print(f"Archivo guardado correctamente: {filepath}")
        else:
            print(f"Advertencia: El archivo {filepath} parece estar vacío.")
    
    except requests.exceptions.RequestException as e:
        print(f"Error al descargar {filename} desde {url}: {e}")
    except IOError as io_err:
        print(f"Error al escribir el archivo {filename}: {io_err}")


# Función para ejecutar la descarga con múltiples hilos
def run_download(threads, url, filename, entity_id):
    thread = threading.Thread(target=download_file, args=(url, filename, entity_id))
    threads.append(thread)
    thread.start()

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

    # Crear un archivo GeoJSON
    polygon = Polygon([[[-68.00847232151489, 9.993051075414456],  
                        [-68.00741959094407, 10.334374973060754],  
                        [-67.49442572538628, 10.332388252838966],  
                        [-67.49602212224453, 9.991131292108372],   
                        [-68.00847232151489, 9.993051075414456]]])  

    features = [Feature(geometry=polygon, properties={"name": "Lago de Valencia Area", "country": "Venezuela"})]
    feature_collection = FeatureCollection(features)

    geojson_path = r'C:\Workspace\descarga_landsat\utils\Lago_de_Valencia_Venezuela_aoi.geojson'
    with open(geojson_path, 'w') as f:
        dump(feature_collection, f)

    gdf = gpd.read_file(geojson_path)
    geojson_dict = json.loads(gdf.to_json())

    spatial_filter = {
        'filterType': 'geojson',
        'geoJson': geojson_dict['features'][0]['geometry']
    }

    dataset_name = 'landsat_ot_c2_l2'
    label = time.strftime("%Y%m%d_%H%M%S")

    temporal_filter = {'start': '2024-01-01', 'end': '2024-08-06'}
    cloud_cover_filter = {'min': 0, 'max': 10}
    file_type = 'band'
    band_names = {'SR_B3', 'SR_B5', 'ANG'}

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
        print(df_scenes)

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

                if download_results and 'availableDownloads' in download_results:
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        for download in download_results['availableDownloads']:
                            entity_id = download.get('entityId', f"download_{download['downloadId']}")
                            filename = os.path.join(download_dir, os.path.basename(download['url']))
                            executor.submit(download_file, download['url'], filename, entity_id)
                else:
                    print("No se encontraron archivos para descargar.")
        else:
            print("No se seleccionaron productos para descargar. Revisa las bandas o el tipo de archivo.")
    else:
        print("No se encontraron escenas para los criterios dados.")

if __name__ == "__main__":
    main()