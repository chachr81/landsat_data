import requests
import sys
import pandas as pd

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

# Función principal
def main():
    # Leer las credenciales desde un archivo
    cred_file_path = r'C:\Workspace\descarga_landsat\credenciales.txt'
    credentials = read_credentials(cred_file_path)
    username = credentials['username']
    token = credentials['token']

    # URL del servicio de la API
    service_url = "https://m2m.cr.usgs.gov/api/api/json/stable/"
    
    # Autenticación
    login_url = service_url + "login-token"
    payload = {"username": username, "token": token}
    response = requests.post(login_url, json=payload)

    if response.status_code == 200:
        api_key = response.json()['data']
        headers = {'X-Auth-Token': api_key}
        print('Login Successful, API Key Received!')
    else:
        print("Login failed")
        return
    
    # Realizar la consulta de los datasets
    dataset_search_payload = {}  # No necesitamos datos específicos aquí, pero el payload debe estar presente.
    datasets = send_request(service_url + "dataset-search", dataset_search_payload, headers=headers)
    
    # Verificar si se recibieron datasets
    if datasets:
        # Filtrar datasets con nombre válido en los campos especificados
        valid_datasets = [dataset for dataset in datasets if dataset.get('datasetAlias') or dataset.get('collectionName') or dataset.get('collectionLongName')]
        
        if valid_datasets:
            # Convertir a DataFrame de pandas
            df = pd.DataFrame(valid_datasets)
            
            # Guardar en un archivo CSV
            output_csv = r'C:\Workspace\descarga_landsat\documents\datasets_info.csv'
            df.to_csv(output_csv, index=False)
            print(f"Datos guardados en: {output_csv}")
        else:
            print("No se encontraron datasets con nombres válidos.")
    else:
        print("No se encontraron datasets.")

if __name__ == "__main__":
    main()