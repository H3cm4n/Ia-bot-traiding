"""
tools/simulate_settlement.py
Simulador de liquidación de órdenes pendientes en SQLite.
"""

import sqlite3
import random
from pathlib import Path
from database.db_manager import update_trade_pnl

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

def simulate_closures():
    if not DB_PATH.exists():
        print("No se encontró la base de datos.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT match_key, price, amount FROM orders_history WHERE action = 'PENDING_CREATED'")
        pending_orders = cursor.fetchall()

    if not pending_orders:
        print("No hay órdenes pendientes por liquidar.")
        return

    print(f"Procesando simulación de cierre para {len(pending_orders)} órdenes pendientes...\n")

    for match_key, price, amount in pending_orders:
        # Simulación: 60% de probabilidad de ganar, 40% de perder
        is_win = random.random() < 0.6
        
        if is_win:
            pnl = round(amount * random.uniform(0.10, 0.45), 2)  # Ganancia
            outcome = "WIN"
        else:
            pnl = round(-1 * (amount * price), 2)                # Pérdida total de la posición
            outcome = "LOSS"

        update_trade_pnl(match_key=match_key, pnl=pnl, final_action=outcome)
        print(f"Match Key: {match_key} | Resultado: {outcome} | PnL: ${pnl}")

if __name__ == "__main__":
    simulate_closures()
