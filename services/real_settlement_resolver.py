import os
import sys
import sqlite3
import requests
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.telegram_notifier import TelegramNotifier

logger = logging.getLogger("RealSettlementResolver")
logging.basicConfig(level=logging.INFO)

DB_PATH = os.environ.get("DIRECTIONAL_DB_PATH", "data/trading_bot.db")
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

def check_and_settle_real_markets():
    if not os.path.exists(DB_PATH):
        logger.warning(f"Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Consultar órdenes en estado PENDING_CREATED o OPEN usando la columna 'action'
    cursor.execute("""
        SELECT id, match_key, symbol, side, price, amount 
        FROM orders_history 
        WHERE action IN ('PENDING_CREATED', 'OPEN')
    """)
    pending_orders = cursor.fetchall()

    if not pending_orders:
        logger.info("No hay órdenes pendientes por resolver de mercado real.")
        conn.close()
        return

    logger.info(f"Verificando {len(pending_orders)} órdenes pendientes en Polymarket Gamma API...")
    settled = []

    for trade_id, match_key, symbol, side, limit_price, amount in pending_orders:
        try:
            # Determinar el condition_id desde symbol o match_key
            cond_id = symbol if symbol and symbol.startswith("0x") else match_key.split("_")[0]

            resp = requests.get(f"{GAMMA_API_URL}?condition_id={cond_id}", timeout=10)
            if resp.status_code != 200:
                continue

            markets = resp.json()
            if not markets:
                continue

            market = markets[0]
            is_closed = market.get("closed", False)
            
            # Solo liquidar si el mercado se cerró/resolvió oficialmente
            if is_closed:
                outcome_prices = market.get("outcomePrices")
                outcomes = market.get("outcomes")

                if outcome_prices and outcomes:
                    target_idx = 0 if str(side).upper() == 'YES' else 1
                    final_price = float(outcome_prices[target_idx])

                    limit_price = float(limit_price) if limit_price else 0.5
                    amount = float(amount) if amount else 10.0

                    if final_price >= 0.99:
                        pnl = (1.0 - limit_price) * (amount / limit_price)
                        new_action = 'WIN'
                    else:
                        pnl = -amount
                        new_action = 'LOSS'

                    cursor.execute("""
                        UPDATE orders_history 
                        SET action = ?, pnl = ?, is_settled = 1 
                        WHERE id = ?
                    """, (new_action, pnl, trade_id))
                    
                    logger.info(f"[RESOLVED] Trade ID {trade_id} -> {new_action} | PnL: ${pnl:.2f}")
                    settled.append(
                        {
                            "match_key": match_key,
                            "symbol": symbol or "CRYPTO",
                            "side": side or "N/A",
                            "action": new_action,
                            "pnl": pnl,
                        }
                    )

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