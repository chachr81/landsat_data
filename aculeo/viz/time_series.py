import sys
from pathlib import Path
import psycopg2
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Añadir el root del proyecto al path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from etl.utils import load_env, get_db_connection_string

def get_water_metrics(sscuenca_id: int, index_type: str = 'MNDWI') -> pd.DataFrame:
    """Extrae las métricas de la Capa Gold como un DataFrame de Pandas"""
    env = load_env()
    from etl.utils import get_ssh_tunnel
    
    with get_ssh_tunnel(env) as local_port:
        conn_str = get_db_connection_string(env, local_port=local_port)
        
        query = """
            SELECT acquisition_date, year, total_water_area_km2, valid_pixels_ratio, sensor
            FROM gold.water_metrics
            WHERE sscuenca_id = %s AND water_index_type = %s
            ORDER BY acquisition_date
        """
        
        with psycopg2.connect(conn_str) as conn:
            df = pd.read_sql_query(query, conn, params=(sscuenca_id, index_type))
            
    df['acquisition_date'] = pd.to_datetime(df['acquisition_date'])
    return df

def plot_water_variation(df: pd.DataFrame, output_path: str, sscuenca_id: int, index_type: str):
    """
    Genera un gráfico atractivo de la serie temporal (ideal para divulgación científica/redes sociales)
    """
    # Filtrar datos de baja calidad (< 50% de píxeles válidos) para no distorsionar la gráfica
    df_clean = df[df['valid_pixels_ratio'] > 0.5].copy()
    
    if df_clean.empty:
        print("No hay suficientes datos limpios para graficar.")
        return

    # Establecer el estilo Seaborn
    sns.set_theme(style="whitegrid", context="paper")
    plt.rcParams.update({'font.size': 12, 'font.family': 'sans-serif'})
    
    fig, ax = plt.subplots(figsize=(14, 7))
    
    # 1. Gráfica principal (Scatter + Línea conectora suave)
    sns.lineplot(
        data=df_clean, x='acquisition_date', y='total_water_area_km2',
        ax=ax, color='#1f77b4', linewidth=1.5, alpha=0.7, zorder=1
    )
    
    sns.scatterplot(
        data=df_clean, x='acquisition_date', y='total_water_area_km2',
        hue='sensor', palette='viridis', style='sensor', s=80, ax=ax, zorder=2
    )

    # 2. Agregar Media Móvil Anual (Rolling Average) para ver tendencia
    df_clean.set_index('acquisition_date', inplace=True)
    df_trend = df_clean['total_water_area_km2'].rolling('365D', min_periods=1).mean()
    df_clean.reset_index(inplace=True)
    
    ax.plot(
        df_clean['acquisition_date'], df_trend.values, 
        color='#ff7f0e', linewidth=2.5, linestyle='--', label='Tendencia (Media Móvil 1 Año)', zorder=3
    )

    # 3. Estética y Etiquetas
    ax.set_title(f"Variación del Espejo de Agua - Cuenca {sscuenca_id}\n({index_type} - Serie Temporal Landsat)", fontsize=16, fontweight='bold', pad=15)
    ax.set_ylabel("Área Total de Agua (km²)", fontsize=14, fontweight='bold')
    ax.set_xlabel("Fecha de Adquisición", fontsize=14, fontweight='bold')
    
    # Mejorar la leyenda
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles=handles, labels=labels, title="Sensor / Tendencia", loc='upper left', bbox_to_anchor=(1, 1))
    
    # Rango en el eje Y un poco más amplio
    y_min, y_max = df_clean['total_water_area_km2'].min(), df_clean['total_water_area_km2'].max()
    padding = (y_max - y_min) * 0.1
    ax.set_ylim(max(0, y_min - padding), y_max + padding)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"Gráfica generada exitosamente en: {output_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generar gráfica de variación de agua")
    parser.add_argument("--cuenca", type=int, required=True, help="ID de la subsubcuenca (ej: 411)")
    parser.add_argument("--index", type=str, default="MNDWI", choices=["MNDWI", "NDWI"], help="Índice a graficar")
    parser.add_argument("--out", type=str, default="water_variation.png", help="Ruta de salida de la imagen")
    
    args = parser.parse_args()
    
    try:
        df_metrics = get_water_metrics(args.cuenca, args.index)
        plot_water_variation(df_metrics, args.out, args.cuenca, args.index)
    except Exception as e:
        print(f"Error generando la gráfica: {e}")
