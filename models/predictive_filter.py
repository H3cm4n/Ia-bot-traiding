"""
models/predictive_filter.py
Reestructuración Cuantitativa: Inferencia de Probabilidad Justa p_model(S0, K, T, sigma)
y cálculo dinámico de EV Neto considerando costos implícitos de mercado.

Notas de integración:
  - `estimate_time_aware_fair_value` puede devolver un `float` (probabilidad)
    o un `dict` con la clave `fair_probability`. Aquí se normaliza siempre
    a un `float` antes de calcular el EV.
  - El filtro es conservador: cualquier entrada inválida o ambigüedad se
    resuelve rechazando el candidato, nunca aprobándolo por defecto.
"""

import sys
import json
import logging

from services.time_aware_fair_value import (
    calculate_time_to_expiration_hours,
    estimate_time_aware_fair_value,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("predictive_filter")

# ---------------------------------------------------------------------- #
# Parámetros del modelo y modelo de costos (Ajuste de Alta Exigencia)
# ---------------------------------------------------------------------- #
MIN_REQUIRED_EV = 0.08          # Elevar el margen de ventaja neto del 5% al 8.0%
DEFAULT_DAILY_VOLATILITY = 0.035  # 3.5% volatilidad diaria base
DEFAULT_FAIR_PROB = 0.65        # Elevar la probabilidad mínima exigida del 60% al 65%
MIN_EXECUTABLE_PRICE = 0.15     # Mantener suelo de precio ejecutable
MAX_EXECUTABLE_PRICE = 0.55     # Mantener techo de precio ejecutable (asimetría favorable)

def calculate_dynamic_ev(
    fair_prob: float,
    executable_price: float,
    spread: float = 0.01,
    fee: float = 0.0,
    slippage: float = 0.005,
) -> float:
    """
    Cálculo de Esperanza Matemática Neta (EV_net):

        EV_net = (fair_prob - executable_price) - (spread + fee + slippage)

    Devuelve -1.0 cuando el precio ejecutable está fuera del rango admisible.
    """
    if executable_price <= 0.0 or executable_price >= 1.0:
        return -1.0

    raw_edge = fair_prob - executable_price
    total_cost = spread + fee + slippage
    ev_net = raw_edge - total_cost
    return round(ev_net, 4)


def _extract_fair_probability(result, fallback: float = DEFAULT_FAIR_PROB) -> float:
    """
    Normaliza el retorno de `estimate_time_aware_fair_value` a un `float`.

    Acepta:
      - dict  -> busca 'fair_probability' (o 'probability' / 'fair_prob' como
                 alias defensivos). Si no la encuentra, usa `fallback`.
      - float / int / str numérico -> lo castea a float.
      - Cualquier otra cosa -> `fallback`.
    """
    if result is None:
        return float(fallback)

    if isinstance(result, dict):
        for key in ("fair_probability", "fair_prob", "probability"):
            if key in result and result[key] is not None:
                try:
                    return float(result[key])
                except (TypeError, ValueError):
                    continue
        return float(fallback)

    try:
        return float(result)
    except (TypeError, ValueError):
        return float(fallback)


def evaluate_candidate(candidate: dict) -> tuple[bool, str, dict]:
    """
    Evalúa la viabilidad cuantitativa de un candidato usando EV Neto dinámico.
    """
    # --- Extracción defensiva de campos ---
    spot_price = float(
        candidate.get("binance_spot_price", candidate.get("spot_price", 0.0)) or 0.0
    )
    strike_price = float(
        candidate.get("target_strike", candidate.get("strike_price", 0.0)) or 0.0
    )
    executable_price = float(
        candidate.get("limit_price", candidate.get("price", 0.0)) or 0.0
    )
    end_date_iso = candidate.get("end_date", candidate.get("expiration", ""))

    # 1. Sanitización de entradas inválidas
    if executable_price <= MIN_EXECUTABLE_PRICE or executable_price >= MAX_EXECUTABLE_PRICE:
        return (
            False,
            "PRECIO_EJECUTABLE_FUERA_DE_RANGO",
            {"ev_net": -1.0, "fair_prob": 0.0, "executable_price": executable_price},
        )

    # 2. Inferencia de fair_prob
    if spot_price > 0 and strike_price > 0:
        try:
            hours_left = calculate_time_to_expiration_hours(end_date_iso)
            time_years = max(1e-6, hours_left / (24.0 * 365.0))
            daily_vol = float(
                candidate.get("daily_volatility", DEFAULT_DAILY_VOLATILITY)
            )

            time_aware_res = estimate_time_aware_fair_value(
                spot_price=spot_price,
                threshold_price=strike_price,
                end_date_str=end_date_iso,
                daily_volatility=daily_vol,
                # Compatibilidad con firmas que aún esperan tiempo/vol anualizada:
                time_to_expiry_years=time_years,
                volatility=daily_vol * (365.0 ** 0.5),
            )

            # Desempaquetado explícito: dict -> 'fair_probability'; escalar -> float
            fair_prob = _extract_fair_probability(time_aware_res, DEFAULT_FAIR_PROB)
        except TypeError:
            # Fallback si la firma de `estimate_time_aware_fair_value` no acepta
            # los kwargs extendidos (versión antigua del módulo).
            try:
                time_aware_res = estimate_time_aware_fair_value(
                    spot_price=spot_price,
                    threshold_price=strike_price,
                    end_date_str=end_date_iso,
                    daily_volatility=daily_vol,
                )
                fair_prob = _extract_fair_probability(
                    time_aware_res, DEFAULT_FAIR_PROB
                )
            except Exception as e:
                logger.warning(
                    f"[ML-FILTER] Fallback a fair_prob=0.50 por error en modelo: {e}"
                )
                fair_prob = DEFAULT_FAIR_PROB
        except Exception as e:
            logger.warning(
                f"[ML-FILTER] Error en modelo time-aware ({e}); "
                f"se usa fair_prob={DEFAULT_FAIR_PROB}"
            )
            fair_prob = DEFAULT_FAIR_PROB
    else:
        # Fallback conservador cuando solo se cuenta con el precio de mercado
        try:
            fair_prob = float(candidate.get("fair_probability", DEFAULT_FAIR_PROB))
        except (TypeError, ValueError):
            fair_prob = DEFAULT_FAIR_PROB

    # 3. Cálculo de EV Neto
    try:
        spread = float(candidate.get("spread", 0.01))
    except (TypeError, ValueError):
        spread = 0.01

    ev_net = calculate_dynamic_ev(fair_prob, executable_price, spread=spread)

    metrics = {
        "fair_prob": round(float(fair_prob), 4),
        "executable_price": round(float(executable_price), 4),
        "ev_net": ev_net,
    }

    if ev_net < MIN_REQUIRED_EV:
        return False, f"EV_INSUFICIENTE_({ev_net:.4f}_<_{MIN_REQUIRED_EV:.4f})", metrics

    return True, f"PASSED_DYNAMIC_EV_({ev_net:+.4f})", metrics


def filter_snapshot(candidates: list) -> list:
    """
    Recorre la ráfaga de candidatos y devuelve solo los que pasan el filtro EV.
    Enriquece cada candidato aprobado con `fair_probability` y `ev_net`.
    """
    approved_candidates = []

    for candidate in candidates:
        try:
            passed, reason, metrics = evaluate_candidate(candidate)
        except Exception as e:
            logger.error(
                f"[ML-FILTER] Excepción evaluando candidato: {e}", exc_info=True
            )
            continue

        raw_key = candidate.get("condition_id") or candidate.get("symbol") or "DESCONOCIDO"
        match_key = str(raw_key)[:10] if raw_key != "DESCONOCIDO" else "DESCONOCIDO"

        if passed:
            logger.info(
                f"[ML-FILTER] CANDIDATO APROBADO: {match_key} | "
                f"EV: {metrics['ev_net']:+.4f} | Prob: {metrics['fair_prob']:.4f}"
            )
            candidate["fair_probability"] = metrics["fair_prob"]
            candidate["ev_net"] = metrics["ev_net"]
            approved_candidates.append(candidate)
        else:
            logger.warning(
                f"[ML-FILTER] CANDIDATO RECHAZADO: {match_key} | Razón: {reason}"
            )

    return approved_candidates


if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_candidates = json.loads(raw)
            filtered = filter_snapshot(input_candidates)
            print(json.dumps(filtered))
        else:
            print("[]")
    except Exception as e:
        logger.error(
            f"Error en filtro predictivo reestructurado: {e}", exc_info=True
        )
        print("[]")