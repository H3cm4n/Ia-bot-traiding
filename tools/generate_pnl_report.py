"""
tools/generate_pnl_report.py
Generador de reportes de rendimiento y PnL por activo leyendo SQLite.
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/trading_bot.db")

def generate_report():
    if not DB_PATH.exists():
        print(f"[!] No se encontró la base de datos en: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # Resumen general por estado/acción
        cursor.execute("SELECT action, COUNT(*), SUM(pnl) FROM orders_history GROUP BY action")
        orders_summary = cursor.fetchall()

        # Posiciones liquidadas (WIN / LOSS / CLOSED)
        cursor.execute("""
            SELECT symbol, action, price, amount, pnl 
            FROM orders_history 
            WHERE pnl IS NOT NULL AND pnl != 0.0
        """)
        closed_trades = cursor.fetchall()

    print("==================================================")
    print("      REPORTE DE RENDIMIENTO DIRECTIONAL v1.5     ")
    print("==================================================")

    if not orders_summary:
        print("Sin operaciones registradas aún en la base de datos.")
        print("==================================================")
        return

    print("RESUMEN GENERAL DE ÓRDENES:")
    for action, count, total_pnl in orders_summary:
        pnl_val = total_pnl if total_pnl is not None else 0.0
        print(f"  * Estado: {action:<18} | Cantidad: {count:<4} | PnL Acumulado: ${pnl_val:+.2f} USDC")

    print("--------------------------------------------------")
    
    if not closed_trades:
        print("Sin operaciones cerradas con PnL finalizado aún.")
        print("==================================================")
        return

    total_trades = len(closed_trades)
    wins = [t for t in closed_trades if t[4] > 0]
    total_pnl = sum(t[4] for t in closed_trades)
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0.0

    print(f"Total Trades Cerrados : {total_trades}")
    print(f"Win Rate Global       : {win_rate:.2f}%")
    print(f"PnL Acumulado (USDC)  : ${total_pnl:+.2f}")
    print("==================================================")

if __name__ == "__main__":
    generate_report()
