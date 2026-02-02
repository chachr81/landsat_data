import sys
from pathlib import Path
from datetime import datetime, timedelta
import logging

# Add project root to sys.path to allow imports from etl
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from etl.m2m_client import M2MClient
from etl.utils import setup_logger, load_aoi_geojson, geojson_to_m2m_spatial_filter, load_env

# Setup a dedicated logger for this script
logger = setup_logger('M2MDebugClient', level='DEBUG')

def debug_m2m_download_options():
    logger.info("Starting M2M download options debug script...")
    env = load_env()

    try:
        with M2MClient(logger=logger) as client:
            # 1. Search for a recent scene with low cloud cover
            logger.info("Searching for a recent Landsat 8-9 scene...")
            aoi_geojson = load_aoi_geojson()
            spatial_filter = geojson_to_m2m_spatial_filter(aoi_geojson)

            # Look for scenes in the last 60 days
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)
            temporal_filter = {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            }
            cloud_filter = {'min': 0, 'max': 10} # Very low cloud cover

            scenes = client.search_scenes(
                dataset_name='landsat_ot_c2_l2', # Landsat 8-9
                spatial_filter=spatial_filter,
                temporal_filter=temporal_filter,
                cloud_cover_filter=cloud_filter,
                max_results=1
            )

            if not scenes:
                logger.warning("No suitable scenes found with current filters. Trying a wider cloud cover range.")
                cloud_filter = {'min': 0, 'max': 50}
                scenes = client.search_scenes(
                    dataset_name='landsat_ot_c2_l2', # Landsat 8-9
                    spatial_filter=spatial_filter,
                    temporal_filter=temporal_filter,
                    cloud_cover_filter=cloud_filter,
                    max_results=1
                )
                if not scenes:
                    logger.error("Still no scenes found. Cannot proceed with download options test.")
                    return

            entity_id = scenes[0]['entityId']
            logger.info(f"Found scene: {entity_id}")

            # 2. Add scene to a temporary list
            list_id = f"debug_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            logger.info(f"Adding scene {entity_id} to temporary list: {list_id}")
            client.add_scenes_to_list(list_id, [entity_id], 'landsat_ot_c2_l2')

            # 3. Attempt to get download options for the list
            file_types_to_test = ['band', 'bundle', 'band_group']
            
            for file_type_to_test in file_types_to_test:
                logger.info(f"Attempting to get download options for list {list_id} with fileType='{file_type_to_test}'...")
                products = client.get_download_options(list_id, 'landsat_ot_c2_l2', file_type=file_type_to_test)

                logger.info(f"Successfully retrieved {len(products)} products/download options for fileType='{file_type_to_test}'.")
                if products:
                    logger.debug(f"Sample product ({file_type_to_test}): {products[0]}")
                else:
                    logger.warning(f"No products/download options found for fileType='{file_type_to_test}'. This might indicate a problem or no downloads are available for this type.")
            
            # Optionally, clean up the list
            # client.remove_scenes_from_list(list_id, 'landsat_ot_c2_l2')
            # client.delete_list(list_id)

    except Exception as e:
        logger.error(f"An error occurred during M2M debug: {e}", exc_info=True)

if __name__ == '__main__':
    debug_m2m_download_options()
