import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.telegram_notifier import TelegramNotifier

logger = logging.getLogger("RealSettlementResolver")
logging.basicConfig(level=logging.INFO)

DB_PATH = os.environ.get("DIRECTIONAL_DB_PATH", "data/trading_bot.db")
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
CLOB_API_URL = "https://clob.polymarket.com/markets"


def _parse_list_field(value):
    """Normaliza campos de Gamma API que pueden venir como lista o JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def _condition_id_from_order(symbol, match_key):
    if symbol and str(symbol).startswith("0x"):
        return str(symbol)
    parts = str(match_key or "").split("_")
    return parts[0] if parts else ""


def _side_from_order(side, match_key):
    normalized = str(side or "").upper()
    if normalized in ("YES", "NO"):
        return normalized

    parts = str(match_key or "").split("_")
    if len(parts) > 1 and parts[1].upper() in ("YES", "NO"):
        return parts[1].upper()
    return normalized or "YES"


def _target_outcome_index(outcomes, side):
    """Mapea YES/NO al índice correcto, soportando etiquetas Up/Down."""
    desired = str(side or "YES").upper()
    aliases = {
        "YES": {"YES", "UP", "TRUE", "1"},
        "NO": {"NO", "DOWN", "FALSE", "0"},
    }
    wanted = aliases.get(desired, {desired})

    for index, label in enumerate(outcomes):
        if str(label).upper() in wanted:
            return index

    return 0 if desired == "YES" else 1


def _is_market_closed(market):
    closed = market.get("closed")
    return closed is True or str(closed).lower() == "true"


def _extract_markets(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "markets", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _select_market(markets, condition_id):
    """Garantiza que solo se liquide el mercado del condition_id solicitado."""
    expected = str(condition_id).lower()
    for market in markets:
        if str(market.get("conditionId", "")).lower() == expected:
            return market

    if len(markets) == 1 and not markets[0].get("conditionId"):
        return markets[0]

    return None


def _as_bool(value):
    return value is True or str(value).lower() == "true"


def _market_from_clob(payload, condition_id):
    """Normaliza la respuesta de CLOB a un mercado Gamma-like resoluble."""
    if not isinstance(payload, dict):
        return None

    payload_cid = payload.get("condition_id") or payload.get("conditionId")
    if payload_cid and str(payload_cid).lower() != str(condition_id).lower():
        return None

    if not _is_market_closed(payload):
        return None

    tokens = payload.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        return None

    winners = [_as_bool(token.get("winner")) for token in tokens]
    if sum(winners) != 1:
        return None

    return {
        "conditionId": payload_cid or condition_id,
        "closed": True,
        "outcomes": [token.get("outcome") for token in tokens],
        "outcomePrices": [1.0 if winner else 0.0 for winner in winners],
    }


def _settle_order(cursor, trade_id, match_key, symbol, side, limit_price, amount, market):
    if not _is_market_closed(market):
        return None

    outcomes = _parse_list_field(market.get("outcomes"))
    outcome_prices = _parse_list_field(market.get("outcomePrices"))
    if not outcome_prices:
        logger.warning(f"[SKIP] Mercado sin outcomePrices para trade ID {trade_id}")
        return None

    resolved_side = _side_from_order(side, match_key)
    target_idx = _target_outcome_index(outcomes, resolved_side)
    if target_idx >= len(outcome_prices):
        logger.warning(
            f"[SKIP] Índice de outcome fuera de rango para trade ID {trade_id}"
        )
        return None

    try:
        final_price = float(outcome_prices[target_idx])
    except (ValueError, TypeError):
        logger.warning(f"[SKIP] outcomePrice inválido para trade ID {trade_id}")
        return None

    price = float(limit_price) if limit_price else 0.5
    stake = float(amount) if amount else 10.0

    if price <= 0:
        logger.warning(f"[SKIP] Precio de entrada inválido para trade ID {trade_id}")
        return None

    if final_price >= 0.99:
        pnl = stake * (1.0 - price) / price
        new_action = "WIN"
    elif final_price <= 0.01:
        pnl = -stake
        new_action = "LOSS"
    else:
        logger.info(
            f"[WAIT] Mercado cerrado sin resolución binaria definitiva "
            f"(trade ID {trade_id}, price={final_price:.4f})"
        )
        return None

    pnl = round(pnl, 2)
    cursor.execute(
        """
        UPDATE orders_history
        SET action = ?, pnl = ?
        WHERE id = ?
          AND action IN ('PENDING_CREATED', 'OPEN')
        """,
        (new_action, pnl, trade_id),
    )
    if cursor.rowcount == 0:
        return None

    logger.info(f"[RESOLVED] Trade ID {trade_id} -> {new_action} | PnL: ${pnl:.2f}")
    return {
        "match_key": match_key,
        "symbol": symbol or "CRYPTO",
        "side": resolved_side,
        "action": new_action,
        "pnl": pnl,
    }


def check_and_settle_real_markets():
    if not os.path.exists(DB_PATH):
        logger.warning(f"Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, match_key, symbol, side, price, amount
        FROM orders_history
        WHERE action IN ('PENDING_CREATED', 'OPEN')
        """
    )
    pending_orders = cursor.fetchall()

    if not pending_orders:
        logger.info("No hay órdenes pendientes por resolver de mercado real.")
        conn.close()
        return

    logger.info(
        f"Verificando {len(pending_orders)} órdenes pendientes en Polymarket Gamma API..."
    )
    settled = []

    for trade_id, match_key, symbol, side, limit_price, amount in pending_orders:
        try:
            cond_id = _condition_id_from_order(symbol, match_key)
            if not cond_id:
                logger.warning(f"[SKIP] Sin condition_id para trade ID {trade_id}")
                continue

            market = None
            try:
                resp = requests.get(
                    GAMMA_API_URL, params={"condition_ids": cond_id}, timeout=10
                )
                if resp.status_code == 200:
                    market = _select_market(
                        _extract_markets(resp.json()), cond_id
                    )
                else:
                    logger.warning(
                        f"[WARN] Gamma API HTTP {resp.status_code} para {cond_id}"
                    )
            except requests.RequestException as exc:
                logger.warning(f"[WARN] Error Gamma API para {cond_id}: {exc}")

            if not market:
                logger.info(
                    f"[FALLBACK] Gamma sin mercado {cond_id}; consultando CLOB API..."
                )
                try:
                    clob_resp = requests.get(
                        f"{CLOB_API_URL}/{cond_id}", timeout=10
                    )
                    if clob_resp.status_code == 200:
                        market = _market_from_clob(clob_resp.json(), cond_id)
                    else:
                        logger.warning(
                            f"[WARN] CLOB API HTTP {clob_resp.status_code} "
                            f"para {cond_id}"
                        )
                except requests.RequestException as exc:
                    logger.warning(f"[WARN] Error CLOB API para {cond_id}: {exc}")

            if not market:
                logger.warning(
                    f"[SKIP] Sin mercado resoluble Gamma/CLOB para {cond_id} "
                    f"(trade ID {trade_id})"
                )
                continue

            settlement = _settle_order(
                cursor,
                trade_id,
                match_key,
                symbol,
                side,
                limit_price,
                amount,
                market,
            )
            if settlement:
                settled.append(settlement)

        except Exception as e:
            logger.error(f"Error procesando resolución para trade ID {trade_id}: {e}")

    conn.commit()
    conn.close()

    if settled:
        notifier = TelegramNotifier()
        for trade in settled:
            notifier.notify_trade_settled(trade, db_path=DB_PATH)


if __name__ == "__main__":
    check_and_settle_real_markets()
