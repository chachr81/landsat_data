#!/usr/bin/env python3
"""
Entry Point Principal - GIS Engine
Uso: python main.py [comando] [opciones]
"""
import argparse
import sys
from datetime import datetime

# Importar los módulos necesarios del paquete ETL
from etl.bronze_ingestion import BronzeIngestion
from etl.m2m_client import M2MClient
from etl.utils import setup_logger, load_config

def handle_ingest(args):
    """
    Manejador para el comando 'ingest'.
    """
    # ... (contenido existente de handle_ingest) ...
    config = load_config()
    
    # Validar fechas
    try:
        start_date = datetime.strptime(args.start, '%Y-%m-%d')
        end_date = datetime.strptime(args.end, '%Y-%m-%d')
        if start_date > end_date:
            print(f"Error: La fecha de inicio ({args.start}) es posterior al fin ({args.end})")
            sys.exit(1)
    except ValueError as e:
        print(f"Error en formato de fecha: {e}")
        sys.exit(1)

    # Configurar logging
    log_level = args.log_level or config.get('logging', {}).get('level', 'INFO')
    logger = setup_logger(
        'BronzeETL',
        log_file=f'ingest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
        level=log_level
    )
    
    logger.info("--- INICIO PROCESO DE INGESTA ---")
    logger.info(f"Periodo: {args.start} - {args.end}")
    
    if args.dry_run:
        logger.warning("MODO DRY-RUN ACTIVADO: Simulación de ejecución. No se descargarán ni insertarán datos.")
        print("MODO DRY-RUN ACTIVADO: La ejecución simulará todas las fases sin efectos secundarios en disco o base de datos.")
    
    try:
        max_clouds = args.clouds or config.get('m2m', {}).get('max_cloud_cover', 40)
        
        # Obtener los m2m_name de los datasets seleccionados desde la config
        selected_datasets = None
        if args.datasets:
            all_datasets_config = config.get('datasets', {})
            selected_datasets = [
                all_datasets_config.get(ds, {}).get('m2m_name')
                for ds in args.datasets
            ]
            if not all(selected_datasets):
                logger.error("Uno o más datasets seleccionados no se encontraron en la configuración.")
                sys.exit(1)
        
        ingestion = BronzeIngestion(
            start_date=args.start,
            end_date=args.end,
            max_cloud_cover=max_clouds,
            logger=logger,
            dry_run=args.dry_run # Pass the dry_run flag
        )
        
        stats = ingestion.run(datasets=selected_datasets)
        
        logger.info("--- RESUMEN DE INGESTA ---")
        logger.info(f"Escenas encontradas: {stats['total_scenes']}")
        logger.info(f"Procesadas OK:       {stats['successful_scenes']}")
        logger.info(f"Fallidas:            {stats['failed_scenes']}")
        
        if stats['failed_scenes'] > 0:
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Error fatal en ingesta: {e}", exc_info=True)
        sys.exit(1)

def handle_cleanup(args):
    """
    Manejador para el comando 'cleanup-lists'.
    Limpia listas de escenas específicas en M2M API.
    """
    logger = setup_logger('CleanupLists', level='INFO')
    
    if args.dry_run:
        logger.warning("MODO DRY-RUN: Solo se simulará el borrado.")
    
    lists_to_delete = args.list_id
    
    if not lists_to_delete:
        logger.info("No se especificaron listas para borrar.")
        return

    print(f"\nSe han seleccionado {len(lists_to_delete)} listas para borrar:")
    for lid in lists_to_delete:
        print(f"  - {lid}")
    
    if args.dry_run:
        print("\n[DRY-RUN] Se borrarían estas listas.")
        return

    if not args.force:
        confirm = input(f"\n¿Está seguro de que desea borrar estas {len(lists_to_delete)} listas? (y/n): ")
        if confirm.lower() != 'y':
            print("Operación cancelada.")
            return
    
    try:
        with M2MClient(logger=logger, dry_run=args.dry_run) as client:
            print("\nIniciando borrado...")
            deleted_count = 0
            for lid in lists_to_delete:
                if client.delete_list(lid):
                    deleted_count += 1
            
            logger.info(f"Limpieza completada. Listas borradas: {deleted_count}/{len(lists_to_delete)}")

    except Exception as e:
        logger.error(f"Error durante la limpieza: {e}")
        sys.exit(1)

def main():
    # Cargar config para obtener la lista de datasets para el CLI
    try:
        config = load_config()
        available_datasets = list(config.get('datasets', {}).keys())
    except FileNotFoundError:
        print("Error: No se encontró el archivo de configuración 'config/landsat_config.yaml'.")
        # Usar una lista por defecto si no se puede cargar la configuración
        available_datasets = ['landsat_8_9', 'landsat_7', 'landsat_4_5']

    parser = argparse.ArgumentParser(
        description="GIS Engine - CLI de Gestión de Datos Landsat",
        epilog="Ejemplo: python main.py ingest --start 2024-01-01 --end 2024-01-31"
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Comandos disponibles')
    subparsers.required = True
    
    # --- Subcomando: ingest ---
    parser_ingest = subparsers.add_parser('ingest', help='Ingesta de datos Landsat (Capa Bronze)')
    parser_ingest.add_argument('--start', required=True, help='Fecha inicio (YYYY-MM-DD)')
    parser_ingest.add_argument('--end', required=True, help='Fecha fin (YYYY-MM-DD)')
    parser_ingest.add_argument('--clouds', type=int, help='Max cobertura de nubes %% (default: config)')
    parser_ingest.add_argument('--datasets', nargs='+', choices=available_datasets, help='Datasets específicos (default: todos)')
    parser_ingest.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING'], help='Nivel de log (default: config)')
    parser_ingest.add_argument('--dry-run', action='store_true', help='Ejecutar sin descargar/insertar')
    parser_ingest.set_defaults(func=handle_ingest)
    
    # --- Subcomando: cleanup-lists ---
    parser_cleanup = subparsers.add_parser('cleanup-lists', help='Limpiar listas de escenas específicas en M2M')
    parser_cleanup.add_argument('--list-id', required=True, nargs='+', help='ID(s) de las listas a borrar')
    parser_cleanup.add_argument('--dry-run', action='store_true', help='Simular borrado sin ejecutarlo')
    parser_cleanup.add_argument('--force', action='store_true', help='Borrar sin pedir confirmación')
    parser_cleanup.set_defaults(func=handle_cleanup)
    
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
