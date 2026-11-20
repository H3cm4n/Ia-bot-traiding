"""
tools/directional_limit_hunter_executor.py
Ejecutor v3.0 Integrado: Discovery -> Fair Value -> Manipulation Guard -> Kelly Risk -> Paper Execution.
"""

import sys
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH
from services.crypto_universe_discovery import CryptoUniverseDiscovery
from services.clob_client import PolymarketCLOBClient
from models.market_manipulation_guard import MarketManipulationGuard
from models.crypto_fair_value import calculate_time_aware_fair_value, evaluate_updown_fair_value
from services.risk_manager import RiskManager, MAX_RISK_USD
from services.whale_tracker import WhaleTracker
from services.telegram_notifier import TelegramNotifier
from executors.paper_executor import PaperExecutor
from database.db_manager import log_order_event, log_rejected_candidate, log_whale_comparison

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("full_integration")


def has_duplicate_open_position(condition_id, side, db_path=None):
    """Bloquea una nueva orden si ya hay PENDING_CREATED/OPEN para condition_id+side."""
    if not condition_id or not side:
        return False

    target_db = Path(db_path) if db_path else Path(DB_PATH)
    if not target_db.exists():
        return False

    prefix = f"{condition_id}_{str(side).upper()}_"
    try:
        with sqlite3.connect(str(target_db)) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM orders_history
                WHERE action IN ('PENDING_CREATED', 'OPEN')
                  AND substr(match_key, 1, ?) = ?
                  AND UPPER(side) = ?
                LIMIT 1
                """,
                (len(prefix), prefix, str(side).upper()),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.error(f"Error consultando posiciones abiertas: {exc}")
        return True

    return row is not None


def get_candle_open(guard, symbol, interval="1h"):
    """Obtiene el precio de apertura de la vela actual en Binance."""
    klines = guard.get_binance_klines(symbol, interval=interval, limit=1)
    if klines:
        return float(klines[0][1]) # El índice 1 es Open price
    return 0.0


def decide_whale_adjustment(
    tracker,
    side,
    condition_net_flow_usd,
    asset_net_flow_usd,
    size_usd,
    max_risk_usd=MAX_RISK_USD,
):
    """
    Resuelve el ajuste de tamaño por ballenas.

    El flujo del condition_id exacto puede habilitar el boost ALIGNED (1.25x).
    El flujo agregado por activo solo puede vetar o reducir el tamaño, nunca
    incrementarlo, para evitar que trades de otros strikes inflen la posición.
    """
    condition_adj = tracker.evaluate_whale_alignment(
        side, condition_net_flow_usd, size_usd, max_risk_usd
    )
    asset_adj = tracker.evaluate_whale_alignment(
        side, asset_net_flow_usd, size_usd, max_risk_usd
    )

    if asset_adj["rejected"]:
        return asset_adj, "asset"
    if condition_adj["rejected"]:
        return condition_adj, "condition"

    conflicts = [
        adj
        for adj in (asset_adj, condition_adj)
        if adj["whale_alignment"] == "CONFLICT"
    ]
    if conflicts:
        chosen = min(conflicts, key=lambda adj: adj["same_side_flow_usd"])
        return chosen, "asset" if chosen is asset_adj else "condition"

    if condition_adj["whale_alignment"] == "ALIGNED":
        return condition_adj, "condition"

    if condition_adj["same_side_flow_usd"] != 0:
        return condition_adj, "condition"

    neutral = {
        "size_usd": round(size_usd, 2),
        "whale_alignment": "NEUTRAL",
        "action_taken": "PROCEED",
        "rejected": False,
        "same_side_flow_usd": round(asset_adj["same_side_flow_usd"], 2),
    }
    return neutral, "asset"

def run_e2e_cycle():
    discovery = CryptoUniverseDiscovery()
    clob = PolymarketCLOBClient()
    guard = MarketManipulationGuard()
    risk = RiskManager()
    whale_tracker = WhaleTracker()
    notifier = TelegramNotifier()
    # Aumentar max_pending_orders a 15 para permitir que RiskManager gestione la exposición
    executor = PaperExecutor(max_pending_orders=15)

    logger.info("=== INICIANDO CICLO E2E (DIRECCIONAL V3.2) ===")

    markets = discovery.discover_updown_markets()
    logger.info(f"Descubiertos {len(markets)} mercados candidatos.")

    for m in markets:
        symbol = m["symbol"]
        condition_id = m["condition_id"]
        question = m["question"]
        q_upper = question.upper()

        expiry_ok, expiry_reason = risk.validate_expiry(m.get("end_date"))
        if not expiry_ok:
            logger.info(f"[RISK] Candidato {symbol} rechazado: {expiry_reason}")
            log_rejected_candidate(
                match_key=condition_id,
                reason=expiry_reason,
                fair_probability=0.0,
                executable_price=0.0,
                edge=0.0,
                raw_data={
                    "condition_id": condition_id,
                    "end_date": m.get("end_date"),
                    "question": question,
                },
            )
            continue

        # 1. Validación de Strike / Dirección
        if not m["is_updown"] and m["strike_price"] is None:
            continue

        # 2. Consultar Precios Spot y Apertura
        current_spot = guard.get_binance_price(symbol)
        if current_spot <= 0:
            continue

        # 3. Cálculo de Probabilidad Justa (Fair Value)
        try:
            end_ts = datetime.fromisoformat(m["end_date"].replace("Z", "+00:00"))
            seconds_left = (end_ts - datetime.now(timezone.utc)).total_seconds()
            days_left = max(0.0001, seconds_left / (24 * 3600))
        except Exception:
            days_left = 1.0

        if m["is_updown"]:
            interval = "1h" if "HOURLY" in q_upper or days_left < 1 else "1d"
            open_price = get_candle_open(guard, symbol, interval)
            if open_price <= 0:
                open_price = current_spot
            fair_res = evaluate_updown_fair_value(symbol, current_spot, open_price, minutes_left=days_left * 24 * 60)
            fair_prob_yes = fair_res["fair_prob_yes"]
            fair_prob_no = fair_res["fair_prob_no"]
        else:
            pair = symbol.upper() if symbol.upper().endswith("USDT") else f"{symbol.upper()}USDT"
            prob_above = calculate_time_aware_fair_value(current_spot, m["strike_price"], days_left, symbol=pair)
            is_bearish = any(w in q_upper for w in ["DIP", "BELOW", "UNDER", "DROP", "FALL"])
            fair_prob_yes = (1.0 - prob_above) if is_bearish else prob_above
            fair_prob_no = 1.0 - fair_prob_yes

        # 4. Evaluación Bidireccional de Orderbooks
        outcomes_to_eval = [
            {"side": "YES", "token_id": m["token_id_yes"], "fair_prob": fair_prob_yes},
            {"side": "NO", "token_id": m["token_id_no"], "fair_prob": fair_prob_no},
        ]

        best_outcome = None
        max_edge = -1.0

        for out in outcomes_to_eval:
            try:
                book = clob.get_order_book(out["token_id"])
                asks = book.get("asks", [])
                bids = book.get("bids", [])
                if not asks or not bids:
                    continue
                best_ask = min(float(a["price"]) for a in asks)
                if best_ask < 0.05 or best_ask > 0.90:
                    continue
                edge = out["fair_prob"] - best_ask
                if edge > max_edge:
                    max_edge = edge
                    best_outcome = {
                        "side": out["side"],
                        "token_id": out["token_id"],
                        "price": best_ask,
                        "fair_prob": out["fair_prob"],
                        "asks": asks,
                        "bids": bids,
                        "edge": edge,
                    }
            except Exception:
                continue

        if not best_outcome or best_outcome["edge"] <= 0:
            continue

        # 5. Deduplicación por condition_id + side y Gestión de Riesgo
        side = best_outcome["side"]
        if has_duplicate_open_position(condition_id, side):
            logger.info(
                f"[RISK] Candidato {symbol} rechazado: DUPLICATE_OPEN_POSITION"
            )
            log_rejected_candidate(
                match_key=f"{condition_id}_{side}",
                reason="DUPLICATE_OPEN_POSITION",
                fair_probability=best_outcome["fair_prob"],
                executable_price=best_outcome["price"],
                edge=best_outcome["edge"],
                raw_data={"condition_id": condition_id, "side": side},
            )
            continue

        is_valid, reason, size_usd = risk.validate_trade(
            best_outcome["fair_prob"],
            best_outcome["price"],
            end_date_iso=m.get("end_date"),
        )
        if not is_valid:
            if reason != "HAS_OPEN_POSITION":
                logger.info(f"[RISK] Candidato {symbol} rechazado: {reason}")
            continue

        size_usd = round(size_usd, 2)
        logger.info(
            f"Evaluando {symbol} [{best_outcome['side']}]: Price={best_outcome['price']:.3f}, "
            f"Fair={best_outcome['fair_prob']:.3f}, Edge={best_outcome['edge']:.3f}, Size=${size_usd:.2f}"
        )
        # 6. Market Manipulation Guard (sobre el lado elegido)
        guard_res = guard.evaluate(
            symbol=symbol,
            condition_id=condition_id,
            asks=best_outcome["asks"],
            bids=best_outcome["bids"],
            poly_price=best_outcome["price"],
            order_size_usd=size_usd
        )

        if guard_res["status"] != "SAFE_TO_TRADE":
            logger.warning(f"[GUARD] Veto para {symbol} [{best_outcome['side']}]: {guard_res['status']} - {guard_res['reasons']}")
            continue

        # 6.5 Whale Integration
        condition_whale_data = whale_tracker.get_whale_activity(condition_id)
        asset_whale_data = whale_tracker.get_asset_whale_activity(symbol)

        whale_adj, whale_source = decide_whale_adjustment(
            whale_tracker,
            side,
            condition_whale_data["whale_net_flow_usd"],
            asset_whale_data["whale_net_flow_usd"],
            size_usd,
            MAX_RISK_USD,
        )
        whale_data = condition_whale_data if whale_source == "condition" else asset_whale_data
        whale_alignment = whale_adj["whale_alignment"]
        action_taken = whale_adj["action_taken"]

        if whale_adj["rejected"]:
            logger.warning(f"[WHALE] {symbol} rechazado por fuerte oposición de ballenas: {whale_adj['same_side_flow_usd']}")
            log_whale_comparison(
                condition_id=condition_id,
                symbol=symbol,
                bot_side=side,
                bot_fair_prob=best_outcome["fair_prob"],
                market_price=best_outcome["price"],
                whale_yes_usd=whale_data["whale_yes_usd"],
                whale_no_usd=whale_data["whale_no_usd"],
                whale_consensus=whale_data["whale_consensus"],
                whale_alignment=whale_alignment,
                action_taken=whale_alignment
            )
            continue

        size_usd = whale_adj["size_usd"]
        size_usd = round(size_usd, 2)
        print(f"[WHALE-CMP] {symbol} [{side}] | Bot Fair: {best_outcome['fair_prob']:.3f} vs Ask: {best_outcome['price']:.3f} | Whales: YES=${whale_data['whale_yes_usd']:.0f} NO=${whale_data['whale_no_usd']:.0f} ({whale_alignment})")
        
        log_whale_comparison(
            condition_id=condition_id,
            symbol=symbol,
            bot_side=side,
            bot_fair_prob=best_outcome["fair_prob"],
            market_price=best_outcome["price"],
            whale_yes_usd=whale_data["whale_yes_usd"],
            whale_no_usd=whale_data["whale_no_usd"],
            whale_consensus=whale_data["whale_consensus"],
            whale_alignment=whale_alignment,
            action_taken=action_taken
        )

        # 7. Ejecución (Paper Trading)
        logger.info(f"[EXECUTION] Disparando orden en {symbol} [{best_outcome['side']}] por ${size_usd:.2f}")
        
        order_payload = [{
            "symbol": symbol,
            "condition_id": condition_id,
            "token_id": best_outcome["token_id"],
            "outcome": best_outcome["side"],
            "price": best_outcome["price"],
            "amount": size_usd,
            "target_size_usdc": size_usd,
            "fair_probability": best_outcome["fair_prob"],
            "question": question,
            "end_date": m.get("end_date"),
        }]

        
        results = executor.execute_candidates(order_payload)
        logger.info(f"Resultado ejecución: {json.dumps(results, indent=2)}")

        for result in results:
            if result.get("status") not in ("PENDING_CREATED", "FILLED", "PAPER_FILLED"):
                continue

            entry_price = float(result.get("limit_price", best_outcome["price"]))
            entry_amount = float(result.get("amount", size_usd))
            potential_return = (
                entry_amount * (1.0 - entry_price) / entry_price
                if entry_price > 0
                else 0.0
            )

            notifier.notify_order_placed(
                {
                    **order_payload[0],
                    **result,
                    "symbol": symbol,
                    "outcome": best_outcome["side"],
                    "price": entry_price,
                    "limit_price": entry_price,
                    "amount": entry_amount,
                    "fair_probability": best_outcome["fair_prob"],
                    "potential_return": round(potential_return, 2),
                    "whale_alignment": whale_alignment,
                    "whale_yes_usd": whale_data["whale_yes_usd"],
                    "whale_no_usd": whale_data["whale_no_usd"],
                    "whale_net_flow_usd": whale_data["whale_net_flow_usd"],
                }
            )


if __name__ == "__main__":
    run_e2e_cycle()
