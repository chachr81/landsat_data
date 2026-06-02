#!/usr/bin/env python3
"""
Entry Point Principal - GIS Engine
Uso: python main.py [comando] [opciones]
"""
import argparse
import logging
import sys
from datetime import datetime

# Importar los módulos necesarios del paquete ETL
from aculeo.bronze.ingestion import BronzeIngestion
from aculeo.bronze.m2m_client import M2MClient
from aculeo.infra.config import setup_logger, load_config

def handle_process_clear(args):
    """Procesa aculeo_raw -> aculeo_clear: aplica QA, calcula indices espectrales."""
    from aculeo.infra.config import load_env
    from aculeo.infra.db import get_ssh_tunnel
    from aculeo.silver.processor import ClearProcessor

    logger = setup_logger('ClearProcessor', level='INFO')
    env    = load_env()

    with get_ssh_tunnel(env, remote_port_key='DB_PORT') as local_port:
        processor = ClearProcessor(
            sscuenca_id=args.cuenca,
            local_port=local_port,
            logger=logger,
        )
        stats = processor.process_pending(index_types=args.indices)

    logger.info("aculeo_clear — procesadas=%s omitidas=%s fallidas=%s",
                stats['processed'], stats['skipped'], stats['failed'])


def handle_analyze(args):
    """Silver + Gold para una cuenca: Bronze→aculeo_clear→aculeo_metricas."""
    from aculeo.infra.config import setup_logger, load_env
    from aculeo.infra.db import get_ssh_tunnel, get_db_connection_string
    from aculeo.silver.processor import ClearProcessor
    from aculeo.gold.providers import SpectralIndexExtractor, WaterMetricsWriter
    from aculeo.gold.detector import WaterBodyDetector
    from aculeo.gold.pipeline import MetricsPipeline
    import psycopg2

    logger = setup_logger('Analyze', level='INFO')
    if args.verbose:
        logging.getLogger('aculeo.gold.detector').setLevel(logging.DEBUG)
    logger.info("Iniciando analisis para cuenca %s%s",
                args.cuenca,
                " [DRY-RUN]" if args.dry_run else "")

    env = load_env()

    with get_ssh_tunnel(env, remote_port_key='DB_PORT') as local_port:
        conn_str = get_db_connection_string(env, local_port=local_port)

        if args.dry_run:
            with psycopg2.connect(conn_str) as conn:
                with conn.cursor() as cur:
                    # Escenas pendientes para Silver
                    cur.execute("""
                        SELECT COUNT(DISTINCT s.scene_id)
                        FROM aculeo_raw.landsat_scenes s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM aculeo_clear.spectral_indices ci
                            WHERE ci.scene_id    = s.scene_id
                              AND ci.sscuenca_id = %s
                              AND ci.index_type  = ANY(%s)
                        )
                    """, (args.cuenca, args.indices))
                    pending_silver = cur.fetchone()[0]

                    # Escenas ya en aculeo_clear listas para Gold
                    gold_query = """
                        SELECT COUNT(DISTINCT scene_id)
                        FROM aculeo_clear.spectral_indices
                        WHERE sscuenca_id = %s
                    """
                    gold_params = [args.cuenca]
                    if args.year:
                        gold_query += " AND year = %s"
                        gold_params.append(args.year)
                    cur.execute(gold_query, gold_params)
                    ready_gold = cur.fetchone()[0]

                    # Escenas ya procesadas en Gold
                    metrics_query = """
                        SELECT COUNT(DISTINCT scene_id)
                        FROM aculeo_metricas.water_metrics
                        WHERE sscuenca_id = %s
                    """
                    metrics_params = [args.cuenca]
                    if args.year:
                        metrics_query += " AND year = %s"
                        metrics_params.append(args.year)
                    cur.execute(metrics_query, metrics_params)
                    done_gold = cur.fetchone()[0]

            logger.info(
                "[DRY-RUN] Silver — escenas pendientes de procesar : %s",
                pending_silver
            )
            logger.info(
                "[DRY-RUN] Gold   — escenas listas en aculeo_clear : %s",
                ready_gold
            )
            logger.info(
                "[DRY-RUN] Gold   — escenas ya en aculeo_metricas  : %s",
                done_gold
            )
            logger.info(
                "[DRY-RUN] Gold   — escenas a procesar             : %s",
                ready_gold - done_gold
            )
            return

        # --- Silver: Bronze → aculeo_clear ---
        clear = ClearProcessor(
            sscuenca_id=args.cuenca,
            local_port=local_port,
            logger=logger,
        )
        clear_stats = clear.process_pending(index_types=args.indices)
        logger.info(
            "Silver completado: %s procesadas, %s omitidas, %s fallidas",
            clear_stats["processed"],
            clear_stats["skipped"],
            clear_stats["failed"],
        )

        # --- Gold: aculeo_clear → aculeo_metricas ---
        extractor = SpectralIndexExtractor(local_port=local_port)
        writer    = WaterMetricsWriter(local_port=local_port)
        detector  = WaterBodyDetector()

        pipeline = MetricsPipeline(
            extractor=extractor,
            writer=writer,
            detector=detector,
            logger=logger,
            sscuenca_id=args.cuenca,
        )

        with psycopg2.connect(conn_str) as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT DISTINCT ci.scene_id
                    FROM aculeo_clear.spectral_indices ci
                    WHERE ci.sscuenca_id = %s
                """
                params = [args.cuenca]
                if args.year:
                    query += " AND ci.year = %s"
                    params.append(args.year)
                cur.execute(query, params)
                scenes = [row[0] for row in cur.fetchall()]

        if not scenes:
            logger.warning("Sin indices en aculeo_clear para analizar.")
            return

        logger.info("%d escenas con indices a procesar.", len(scenes))
        for scene_id in scenes:
            pipeline.process_scene(scene_id, index_types=args.indices)

        logger.info("Analisis completado.")


def handle_plot(args):
    """Genera gráficas de serie temporal desde aculeo_metricas."""
    import subprocess
    logger = setup_logger('Plot', level='INFO')
    logger.info("Generando gráfica para cuenca %s (Indice: %s)", args.cuenca, args.index)
    script_path = "aculeo/viz/time_series.py"
    cmd = [".venv/bin/python", script_path,
           "--cuenca", str(args.cuenca), "--index", args.index, "--out", args.out]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Error ejecutando script de gráficas: %s", e)


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

    log_level = args.log_level or config.get('logging', {}).get('level', 'INFO')
    logger = setup_logger('BronzeETL', level=log_level)
    
    logger.info("--- INICIO PROCESO DE INGESTA ---")
    logger.info("Periodo: %s - %s", args.start, args.end)

    if args.dry_run:
        logger.warning(
            "MODO DRY-RUN ACTIVADO: Simulación de ejecución. "
            "No se descargarán ni insertarán datos."
        )
        print(
            "MODO DRY-RUN ACTIVADO: La ejecución simulará todas las fases "
            "sin efectos secundarios en disco o base de datos."
        )

    try:
        max_clouds = args.clouds or config.get(
            'm2m',
            {},
        ).get('max_cloud_cover', 40)

        selected_datasets = None
        if args.datasets:
            all_datasets_config = config.get('datasets', {})
            selected_datasets = [
                all_datasets_config.get(ds, {}).get('m2m_name')
                for ds in args.datasets
            ]
            if not all(selected_datasets):
                logger.error(
                    "Uno o más datasets seleccionados no se encontraron "
                    "en la configuración."
                )
                sys.exit(1)

        import psycopg2
        from aculeo.infra.config import load_env as load_environment
        from aculeo.infra.db import get_ssh_tunnel
        env = load_environment()

        with get_ssh_tunnel(env, remote_port_key='DB_PORT') as local_port:
            ingestion = BronzeIngestion(
                start_date=args.start,
                end_date=args.end,
                max_cloud_cover=max_clouds,
                logger=logger,
                dry_run=args.dry_run,
                local_port=local_port,
            )
            stats = ingestion.run(datasets=selected_datasets)

        logger.info("--- RESUMEN DE INGESTA ---")
        logger.info("Escenas encontradas: %s", stats['total_scenes'])
        logger.info("Procesadas OK:       %s", stats['successful_scenes'])
        logger.info("Fallidas:            %s", stats['failed_scenes'])

        if stats['failed_scenes'] > 0:
            sys.exit(1)

    except (IOError, ValueError, KeyError, psycopg2.Error) as e:
        logger.error("Error fatal en ingesta: %s", e, exc_info=True)
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

            logger.info(
                "Limpieza completada. Listas borradas: %s/%s",
                deleted_count,
                len(lists_to_delete),
            )

    except Exception as e:
        logger.error("Error durante la limpieza: %s", e)
        sys.exit(1)


def main():
    """Punto de entrada principal del CLI."""
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
        epilog=(
            "Ejemplo: python main.py ingest --start 2024-01-01 "
            "--end 2024-01-31"
        ),
    )

    subparsers = parser.add_subparsers(
        dest='command',
        help='Comandos disponibles',
    )
    subparsers.required = True

    # --- Subcomando: ingest ---
    parser_ingest = subparsers.add_parser(
        'ingest',
        help='Ingesta de datos Landsat (Capa Bronze)',
    )
    parser_ingest.add_argument('--start', required=True, help='Fecha inicio (YYYY-MM-DD)')
    parser_ingest.add_argument('--end', required=True, help='Fecha fin (YYYY-MM-DD)')
    parser_ingest.add_argument(
        '--clouds',
        type=int,
        help='Max cobertura de nubes %% (default: config)',
    )
    parser_ingest.add_argument(
        '--datasets',
        nargs='+',
        choices=available_datasets,
        help='Datasets específicos (default: todos)',
    )
    parser_ingest.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING'],
        help='Nivel de log (default: config)',
    )
    parser_ingest.add_argument(
        '--dry-run',
        action='store_true',
        help='Ejecutar sin descargar/insertar',
    )
    parser_ingest.set_defaults(func=handle_ingest)

    # --- Subcomando: cleanup-lists ---
    parser_cleanup = subparsers.add_parser(
        'cleanup-lists',
        help='Limpiar listas de escenas específicas en M2M',
    )
    parser_cleanup.add_argument(
        '--list-id',
        required=True,
        nargs='+',
        help='ID(s) de las listas a borrar',
    )
    parser_cleanup.add_argument(
        '--dry-run',
        action='store_true',
        help='Simular borrado sin ejecutarlo',
    )
    parser_cleanup.add_argument(
        '--force',
        action='store_true',
        help='Borrar sin pedir confirmación',
    )
    parser_cleanup.set_defaults(func=handle_cleanup)

    # --- Subcomando: process-clear ---
    parser_clear = subparsers.add_parser(
        'process-clear',
        help=(
            'Aplica QA, recorta y calcula indices espectrales '
            '(aculeo_raw -> aculeo_clear)'
        ),
    )
    parser_clear.add_argument(
        '--cuenca',
        type=int,
        default=411,
        help='sscuenca_id (default: 411)',
    )
    parser_clear.add_argument(
        '--indices',
        nargs='+',
        choices=['MNDWI', 'NDWI'],
        default=['MNDWI', 'NDWI'],
        help='Indices a calcular (default: MNDWI NDWI)',
    )
    parser_clear.set_defaults(func=handle_process_clear)

    # --- Subcomando: analyze ---
    parser_analyze = subparsers.add_parser(
        'analyze',
        help=(
            'Deteccion estadistica de agua y metricas '
            '(aculeo_clear -> aculeo_metricas)'
        ),
    )
    parser_analyze.add_argument(
        '--cuenca',
        type=int,
        default=411,
        help='sscuenca_id (default: 411)',
    )
    parser_analyze.add_argument(
        '--indices',
        nargs='+',
        choices=['MNDWI', 'NDWI'],
        default=['MNDWI', 'NDWI'],
        help='Indices a analizar (default: MNDWI NDWI)',
    )
    parser_analyze.add_argument(
        '--year',
        type=int,
        help='Procesar solo este año (opcional)',
    )
    parser_analyze.add_argument(
        '--dry-run',
        action='store_true',
        help='Solo reportar conteos, sin procesar',
    )
    parser_analyze.add_argument(
        '--verbose', action='store_true',
        help='Activar logging DEBUG del detector (detalle de filtros F1–F5 por escena)'
    )
    parser_analyze.set_defaults(func=handle_analyze)

    # --- Subcomando: plot ---
    parser_plot = subparsers.add_parser(
        'plot',
        help='Generar gráficas de series de tiempo',
    )
    parser_plot.add_argument(
        '--cuenca',
        type=int,
        required=True,
        help='ID de la subsubcuenca (ej: 411)',
    )
    parser_plot.add_argument(
        '--index',
        type=str,
        default='MNDWI',
        choices=['MNDWI', 'NDWI'],
        help='Índice a graficar',
    )
    parser_plot.add_argument(
        '--out',
        type=str,
        default='water_variation.png',
        help='Ruta de salida de la gráfica',
    )
    parser_plot.set_defaults(func=handle_plot)

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
