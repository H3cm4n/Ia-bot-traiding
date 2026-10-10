"""
main.py
Orquestador principal del bot de trading para Polymarket.
Flujo: Resolución -> Sincronización -> Ingesta -> Filtro ML -> Ejecución -> Auto-Entrenamiento.
"""

import time
import logging

from connectors.polymarket_ingest import get_live_snapshot
from models.predictive_filter import filter_snapshot
from tools.directional_limit_hunter_executor import process_snapshot, executor
from services.order_settlement_resolver import check_and_settle_orders
from models.train_filter import train_model
from analytics.performance import calculate_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("main_orchestrator")

INTERVAL_SECONDS = 10

def run_loop():
    logger.info("=== INICIANDO BOT DE TRADING AUTO-SOSTENIBLE ===")
    logger.info(f"Frecuencia de monitoreo: Cada {INTERVAL_SECONDS} segundos.")
    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            logger.info(f"--- Ciclo #{cycle_count} ---")
            
            # 1. Resolver órdenes en la BD y sincronizar memoria local
            resolved_keys = check_and_settle_orders()
            if resolved_keys:
                logger.info(f"[MEMORY] Purgando {len(resolved_keys)} órdenes liquidadas de la memoria local.")
                executor.purge_resolved_orders(resolved_keys)

            # 2. Ingesta de datos de mercado en vivo
            raw_candidates = get_live_snapshot()
            logger.info(f"Ingesta: {len(raw_candidates)} candidatos recuperados.")
            
            if raw_candidates:
                # 3. Filtro de Machine Learning
                approved_candidates = filter_snapshot(raw_candidates)
                
                # 4. Ejecución de Órdenes Aprobadas
                if approved_candidates:
                    process_snapshot(approved_candidates)
                else:
                    logger.info("Filtro ML descartó todos los candidatos en este ciclo.")

            # Cada 5 ciclos, re-entrenar modelo y mostrar métricas
            if cycle_count % 5 == 0:
                logger.info("[AUTO-TRAIN] Re-entrenando modelo ML con nuevos resultados...")
                train_model()
                calculate_metrics()

            time.sleep(INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("\n[!] Detención manual recibida. Cerrando bot ordenadamente...")
        calculate_metrics()
        logger.info("=== BOT DETENIDO CON ÉXITO ===")

if __name__ == "__main__":
    run_loop()
