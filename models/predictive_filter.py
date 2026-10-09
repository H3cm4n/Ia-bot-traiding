"""
models/predictive_filter.py
Módulo de Machine Learning que evalúa candidatos usando un modelo probabilístico (.pkl)
o reglas heurísticas de respaldo.
"""

import sys
import json
import logging
from pathlib import Path

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False

# Configuración básica de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr
)
logger = logging.getLogger("predictive_filter")

MODEL_PATH = Path(__file__).resolve().parent / "model_filter.pkl"
WIN_PROBABILITY_THRESHOLD = 0.40  # Exigir un mínimo de 40% de probabilidad estimada de ganar

# Cargar el modelo si existe
clf_model = None
if HAS_JOBLIB and MODEL_PATH.exists():
    try:
        clf_model = joblib.load(MODEL_PATH)
        logger.info("[ML-FILTER] Modelo probabilístico (.pkl) cargado exitosamente.")
    except Exception as e:
        logger.warning(f"[ML-FILTER] No se pudo cargar el modelo .pkl: {e}")

def evaluate_candidate(candidate: dict) -> tuple[bool, str]:
    price = candidate.get("limit_price", candidate.get("price", 0.0))
    amount = candidate.get("amount", 10.0)

    # Regla 1: Precios inválidos o extremadamente nulos
    if price <= 0.02:
        return False, "PRECIO_INVALIDO_O_NULO"

    # Evaluador de Probabilidad por Modelo Entrenado
    if clf_model is not None:
        try:
            # Obtener probabilidad de la clase 1 (Ganancia)
            probabilities = clf_model.predict_proba([[price, amount]])[0]
            # La clase 1 está en la segunda posición si las clases son [0, 1]
            win_prob = probabilities[1] if len(probabilities) > 1 else probabilities[0]

            if win_prob < WIN_PROBABILITY_THRESHOLD:
                return False, f"PROBABILIDAD_INSUFICIENTE_({win_prob*100:.1f}%_<_40%)"
            
            return True, f"PASSED_MODEL_PROB_({win_prob*100:.1f}%)"
        except Exception as e:
            logger.error(f"Error calculando probabilidades en modelo ML: {e}")

    # Regla Heurística de respaldo (si no se dispone del modelo)
    if 0.40 <= price <= 0.45:
        return False, "FILTRO_HEURISTICO_PATRON_PERDIDA"

    return True, "PASSED"

def filter_snapshot(candidates: list) -> list:
    approved_candidates = []
    for candidate in candidates:
        passed, reason = evaluate_candidate(candidate)
        match_key = candidate.get("condition_id", "DESCONOCIDO")[:10]
        
        if passed:
            logger.info(f"[ML-FILTER] CANDIDATO APROBADO: {match_key} | Precio: {candidate.get('limit_price')} | {reason}")
            approved_candidates.append(candidate)
        else:
            logger.warning(f"[ML-FILTER] CANDIDATO RECHAZADO: {match_key} | Razón: {reason}")
            
    return approved_candidates

if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_candidates = json.loads(raw)
            filtered = filter_snapshot(input_candidates)
            print(json.dumps(filtered))
    except Exception as e:
        logger.error(f"Error en filtro predictivo: {e}", exc_info=True)
        print("[]")

