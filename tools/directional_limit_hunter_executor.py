"""
tools/directional_limit_hunter_executor.py
Ejecutor de órdenes v1.5 para Directional Limit Hunter.
Incorpora simulación de límites, cancelación por conflicto y persistencia SQLite.
"""

import sys
import json
import logging
from pathlib import Path

# Añadir raíz al sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from executors.paper_executor import PaperExecutor
from database.db_manager import log_order_event

# Configuración de logger básico
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr
)
logger = logging.getLogger("directional_executor")

def process_snapshot(candidates: list):
    executor = PaperExecutor()
    
    for candidate in candidates:
        # Llamada al método correcto: process_signal
        res = executor.process_signal(candidate)
        status = res.get("status")
        action = res.get("action", status)
        match_key = res.get("match_key", "")
        
        if status == "REJECTED":
            logger.warning(f"[v1.5] Candidato RECHAZADO: {res.get('reason')}")
        else:
            logger.info(f"[v1.5] Evento procesado: {status} | Match Key: {match_key}")
            
        # Obtener el precio buscando ambas claves posibles
        order_price = candidate.get("limit_price", candidate.get("price"))

        log_order_event(
            match_key=match_key,
            action=action,
            symbol=candidate.get("condition_id"),
            side=candidate.get("outcome"),
            price=order_price,
            amount=candidate.get("amount"),
            raw_data=candidate
        )

if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            process_snapshot(data)
    except Exception as e:
        logger.error(f"Error en ejecutor directional: {e}", exc_info=True)
