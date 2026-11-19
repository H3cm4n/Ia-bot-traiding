"""
tools/simulate_settlement.py
Simulador de liquidación de órdenes pendientes en SQLite.
"""

import sys
import random
import sqlite3
from pathlib import Path

# Garantizar importación desde la raíz del proyecto
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH
from services.telegram_notifier import TelegramNotifier


def simulate_closures():
    if not DB_PATH.exists():
        print(f"No se encontró la base de datos en: {DB_PATH}")
        return

    settled = []

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT match_key, symbol, side, price, amount "
            "FROM orders_history WHERE action = 'PENDING_CREATED'"
        )
        pending_orders = cursor.fetchall()

        if not pending_orders:
            print("No hay órdenes pendientes por liquidar.")
            return

        print(f"Procesando simulación de cierre para {len(pending_orders)} órdenes pendientes...\n")

        for match_key, symbol, side, price, amount in pending_orders:
            price_val = price if price is not None else 0.5
            amount_val = amount if amount is not None else 10.0

            is_win = random.random() < 0.6
            if is_win:
                pnl = round(amount_val * random.uniform(0.10, 0.45), 2)
                outcome = "WIN"
            else:
                pnl = round(-1 * (amount_val * price_val), 2)
                outcome = "LOSS"

            cursor.execute(
                """
                UPDATE orders_history
                SET action = ?, pnl = ?
                WHERE match_key = ? AND action = 'PENDING_CREATED'
                """,
                (outcome, pnl, match_key),
            )
            settled.append(
                {
                    "match_key": match_key,
                    "symbol": symbol or "CRYPTO",
                    "side": side or "N/A",
                    "action": outcome,
                    "pnl": pnl,
                }
            )
            print(f"Match Key: {match_key} | Resultado: {outcome} | PnL: ${pnl}")

        conn.commit()

    if settled:
        notifier = TelegramNotifier()
        for trade in settled:
            notifier.notify_trade_settled(trade, db_path=DB_PATH)


if __name__ == "__main__":
    simulate_closures()