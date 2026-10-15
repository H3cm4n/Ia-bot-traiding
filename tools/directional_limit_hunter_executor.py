"""
tools/directional_limit_hunter_executor.py
Ejecutor de órdenes v2.1 con integración del filtro por EV_net, tope de precio
ejecutable (asimetría favorable), registro de rechazados, inferencia dinámica ML
y deduplicación previa contra SQLite (markets ya operados).
"""
import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.market_resolver import MarketResolver
from services.clob_client import PolymarketCLOBClient
from services.realistic_entry_filter import RealisticEntryFilter
from executors.paper_executor import PaperExecutor
from database.db_manager import (
    log_order_event,
    log_rejected_candidate,
)
from connectors.polymarket_ingest import fetch_diversified_markets
from models.predictive_filter import evaluate_candidate, DEFAULT_FAIR_PROB

logger = logging.getLogger("directional_executor")

SIDE_SUFFIX = " [LADO NO]"

# --- Rango de precio ejecutable (asimetría favorable) ---
MAX_ENTRY_PRICE = 0.55
MIN_ENTRY_PRICE = 0.15

# Fallback conservador si el modelo ML no puede predecir
DEFAULT_FAIR_PROBABILITY = 0.50

# --- Ruta única de la DB (coherente con el runner y con db_manager) ---
DIRECTIONAL_DB_PATH = os.environ.get(
    "DIRECTIONAL_DB_PATH",
    str(PROJECT_ROOT / "database" / "trading.db"),
)


# ---------------------------------------------------------------------------
# Modelo ML: carga perezosa y singleton
# ---------------------------------------------------------------------------
_ML_PREDICTOR = None
_ML_PREDICTOR_FAILED = False


def _get_ml_predictor():
    global _ML_PREDICTOR, _ML_PREDICTOR_FAILED

    if _ML_PREDICTOR is not None:
        return _ML_PREDICTOR
    if _ML_PREDICTOR_FAILED:
        return None

    try:
        _ML_PREDICTOR = evaluate_candidate
        logger.info("[ML] Predictor cuantitativo time-aware cargado correctamente.")
        return _ML_PREDICTOR
    except Exception as e:
        logger.error(f"[ML] No se pudo cargar el predictor: {e}")
        _ML_PREDICTOR_FAILED = True
        return None


def _resolve_fair_probability(cand: dict) -> tuple[float, str]:
    """Devuelve (fair_probability, source)."""
    if "fair_probability" in cand and cand["fair_probability"] is not None:
        return float(cand["fair_probability"]), "candidate"

    try:
        _, _, metrics = evaluate_candidate(cand)
        fair_prob = metrics.get("fair_prob", DEFAULT_FAIR_PROB)
        return float(fair_prob), "predictive_filter"
    except Exception as e:
        logger.error(f"[ML] Error evaluando candidato con predictive_filter: {e}")
        return DEFAULT_FAIR_PROB, "default"


# ---------------------------------------------------------------------------
# Resolución de mercado
# ---------------------------------------------------------------------------
def _norm_question(text) -> str:
    return str(text or "").replace(SIDE_SUFFIX, "").strip().lower()


def resolve_market_for_candidate(candidate: dict, live_markets: list) -> Optional[dict]:
    cid = candidate.get("condition_id")

    if cid and live_markets:
        for m in live_markets:
            m_cid = m.get("condition_id") or m.get("conditionId")
            if m_cid and str(cid).lower() == str(m_cid).lower():
                return m

    question = _norm_question(candidate.get("question"))
    if question and live_markets:
        for m in live_markets:
            m_q = _norm_question(m.get("question"))
            if m_q and m_q == question:
                return m

    return None


def _as_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _extract_token_id(market: dict, outcome: str) -> Optional[str]:
    outcomes = _as_list(market.get("outcomes"))
    token_ids = _as_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    if not outcomes or not token_ids:
        return None
    for name, tid in zip(outcomes, token_ids):
        if str(name).upper() == outcome:
            return tid
    return None


def _load_live_markets(resolver) -> list:
    try:
        events = fetch_diversified_markets(limit=100)
        markets = []
        for ev in events or []:
            markets.extend(ev.get("markets", []) or [])
        return markets
    except Exception as e:
        logger.error(f"No se pudieron cargar mercados en vivo desde Gamma: {e}")
        return []


# ---------------------------------------------------------------------------
# Procesamiento del snapshot
# ---------------------------------------------------------------------------
def process_snapshot(candidates_data: list) -> list:
    resolver = MarketResolver()
    clob = PolymarketCLOBClient()
    entry_filter = RealisticEntryFilter(min_edge_margin=0.02)
    executor = PaperExecutor(max_pending_orders=20)

    live_markets = _load_live_markets(resolver)

    ml_predictor = _get_ml_predictor()
    if ml_predictor is None:
        logger.warning(
            "[ML] Ejecutando sin inferencia dinámica; "
            "se usarán fair_probability del candidato o default."
        )

    approved_candidates = []
    results = []

    for cand in candidates_data:
        symbol = cand.get("symbol", cand.get("question", "CRYPTO"))

        default_outcome = "NO" if SIDE_SUFFIX in str(cand.get("question", "")) else "YES"
        outcome = str(cand.get("outcome", default_outcome)).upper()

        # 1. Resolver mercado
        market = resolve_market_for_candidate(cand, live_markets)

        if market:
            cond_id = market.get("condition_id") or market.get("conditionId")
            token_id = _extract_token_id(market, outcome)
        else:
            tokens = resolver.get_market_tokens(symbol)
            cond_id = tokens.get("condition_id") if tokens else cand.get("condition_id")
            token_id = tokens.get(outcome) if tokens else None

        if not cond_id:
            logger.warning(f"Sin condition_id para '{symbol}', se omite")
            results.append({"status": "REJECTED", "reason": "MARKET_NOT_RESOLVED", "symbol": symbol})
            continue

        cand["condition_id"] = cond_id
        cand["token_id"] = token_id
        cand["outcome"] = outcome

        # --- 1.a. Deduplicación previa contra SQLite ------------------------
        # Se descarta el candidato si (cond_id, outcome) ya tiene una orden
        # activa ('PENDING_CREATED') o ya liquidada ('WIN'/'LOSS'). Se ejecuta
        # ANTES de la inferencia ML y del filtro de precio para ahorrar
        # cómputo, llamadas a CLOB y escritura de rechazados.
        if executor.is_market_already_traded(cond_id, outcome, DIRECTIONAL_DB_PATH):
            logger.info(
                f"[DEDUP] Mercado {cond_id} [{outcome}] ya tiene orden registrada. Se omite."
            )
            results.append({
                "status": "REJECTED",
                "reason": "MARKET_ALREADY_TRADED",
                "condition_id": cond_id,
                "outcome": outcome,
            })
            continue
        # --------------------------------------------------------------------

        executable_price = cand.get("limit_price", cand.get("price", 0.50))

        # --- Inferencia dinámica de fair_probability ---
        fair_probability, prob_source = _resolve_fair_probability(cand)
        cand["fair_probability"] = fair_probability
        cand["fair_probability_source"] = prob_source

        logger.info(
            f"[ML] Prob para {cond_id} [{outcome}]: "
            f"{fair_probability:.4f} (origen={prob_source})"
        )

        # --- 1.b. Filtro de rango de precio ejecutable ---
        if not (MIN_ENTRY_PRICE <= executable_price <= MAX_ENTRY_PRICE):
            reason = "OUT_OF_BOUNDS_PRICE"
            match_key = f"{cond_id}_{outcome}"

            logger.warning(
                f"[PRICE-FILTER] CANDIDATO RECHAZADO: {cond_id} [{outcome}] | "
                f"Precio: {executable_price:.4f} fuera de rango "
                f"[{MIN_ENTRY_PRICE:.2f}, {MAX_ENTRY_PRICE:.2f}] | "
                f"Prob: {fair_probability:.4f} ({prob_source}) | Razón: {reason}"
            )

            log_rejected_candidate(
                match_key=match_key,
                reason=reason,
                fair_probability=fair_probability,
                executable_price=executable_price,
                edge=0.0,
                raw_data=cand,
            )
            results.append({
                "status": "REJECTED",
                "reason": reason,
                "condition_id": cond_id,
                "outcome": outcome,
                "executable_price": executable_price,
                "min_entry_price": MIN_ENTRY_PRICE,
                "max_entry_price": MAX_ENTRY_PRICE,
                "fair_probability_source": prob_source,
            })
            continue

        # 2. Libro CLOB en vivo
        if token_id:
            book = clob.get_order_book(token_id)
            cand["orderbook_asks"] = book.get("asks", [])
            cand["orderbook_bids"] = book.get("bids", [])

        # 3. Evaluación de Expectativa Neta (EV_net)
        eval_res = entry_filter.evaluate_opportunity(
            fair_probability=fair_probability,
            executable_price=executable_price,
            spread=0.01,
            slippage=0.005,
        )

        match_key = f"{cond_id}_{outcome}"
        edge_val = eval_res.get("edge", 0.0)

        if not eval_res["approved"]:
            reason = eval_res["reason"]

            logger.warning(
                f"[ML-FILTER] CANDIDATO RECHAZADO: {cond_id} [{outcome}] | "
                f"EV: {edge_val:+.4f} | Prob: {fair_probability:.4f} ({prob_source}) | "
                f"Precio: {executable_price:.2f} | Razón: {reason}"
            )

            log_rejected_candidate(
                match_key=match_key,
                reason=reason,
                fair_probability=fair_probability,
                executable_price=executable_price,
                edge=edge_val,
                raw_data=cand,
            )
            results.append({
                "status": "REJECTED",
                "reason": reason,
                "condition_id": cond_id,
                "outcome": outcome,
                "fair_probability_source": prob_source,
            })
        else:
            logger.info(
                f"[ML-FILTER] CANDIDATO APROBADO: {cond_id} [{outcome}] | "
                f"EV: {edge_val:+.4f} | Prob: {fair_probability:.4f} ({prob_source}) | "
                f"Precio: {executable_price:.2f}"
            )
            approved_candidates.append(cand)

    # 4. Ejecutar solo los candidatos aprobados
    if approved_candidates:
        logger.info(f"[EJECUCIÓN] Enviando {len(approved_candidates)} candidatos aprobados al PaperExecutor...")
        exec_results = executor.execute_candidates(approved_candidates)
        results.extend(exec_results)
    else:
        logger.info("[FILTROS] Todos los candidatos de este snapshot fueron descartados.")

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        raw_input = sys.stdin.read()
        if raw_input.strip():
            data = json.loads(raw_input)
            res = process_snapshot(data)
            print(json.dumps(res, indent=2))
    except Exception as e:
        logger.error(f"Error en ejecutor directional v2.1: {e}")