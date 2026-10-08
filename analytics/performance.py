"""
analytics/performance.py
Módulo para calcular métricas de rendimiento y Esperanza Matemática (Ev).
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

def calculate_metrics():
    if not DB_PATH.exists():
        print("No se encontró la base de datos.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Obtener todas las órdenes finalizadas (CLOSED, FILLED, WIN, LOSS)
        cursor.execute("SELECT pnl FROM orders_history WHERE pnl IS NOT NULL AND pnl != 0.0")
        pnls = [row[0] for row in cursor.fetchall()]

    total_trades = len(pnls)
    if total_trades == 0:
        print("Aún no hay suficientes trades cerrados con PnL registrado para calcular Ev.")
        return

    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]

    win_rate = len(wins) / total_trades
    loss_rate = len(losses) / total_trades

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    ev = (win_rate * avg_win) - (loss_rate * avg_loss)

    print("\n=== REPORTE DE RENDIMIENTO (Ev) ===")
    print(f"Total Trades Cerrados: {total_trades}")
    print(f"Tasa de Acierto (Win Rate): {win_rate * 100:.2f}%")
    print(f"Ganancia Promedio: ${avg_win:.2f}")
    print(f"Pérdida Promedio: ${avg_loss:.2f}")
    print(f"Esperanza Matemática (Ev): ${ev:.4f} por operación")
    print("===================================\n")

if __name__ == "__main__":
    calculate_metrics()
