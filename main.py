"""
main.py
Orquestador principal del bot de trading para Polymarket.
Ejecuta la tubería continua: Ingesta -> Filtro ML -> Ejecución en SQLite.
"""

import time
import sys
import logging
from pathlib import Path

# Importación de los módulos del pipeline
from connectors.polymarket_ingest import get_live_snapshot
from models.predictive_filter import filter_snapshot
from tools.directional_limit_hunter_executor import process_snapshot
from analytics.performance import calculate_metrics

# Configuración de logger unificado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("main_orchestrator")

INTERVAL_SECONDS = 10  # Intervalo de escaneo entre ciclos (en segundos)

def run_loop():
    logger.info("=== INICIANDO BOT DE TRADING EN MODO CONTINUO ===")
    logger.info(f"Frecuencia de monitoreo: Cada {INTERVAL_SECONDS} segundos.")
    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            logger.info(f"--- Ciclo #{cycle_count} ---")
            
            # 1. Ingesta de datos de mercado en vivo desde Polymarket
            raw_candidates = get_live_snapshot()
            logger.info(f"Ingesta: {len(raw_candidates)} candidatos recuperados.")
            
            if raw_candidates:
                # 2. Filtro con el modelo de Machine Learning (.pkl)
                approved_candidates = filter_snapshot(raw_candidates)
                
                # 3. Ejecución y Persistencia en SQLite
                if approved_candidates:
                    process_snapshot(approved_candidates)
                else:
                    logger.info("Filtro ML descartó todos los candidatos en este ciclo.")

            # Cada 5 ciclos, mostrar reporte de rendimiento global (Ev)
            if cycle_count % 5 == 0:
                calculate_metrics()

            time.sleep(INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("\n[!] Detención manual recibida. Cerrando bot ordenadamente...")
        calculate_metrics()
        logger.info("=== BOT DETENIDO CON ÉXITO ===")

if __name__ == "__main__":
    run_loop()
