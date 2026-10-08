"""
analytics/model_filter_analysis.py
Módulo para analizar patrones en los datos de trading y extraer
características de las operaciones perdedoras (para ajuste de modelo).
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

def extract_dataset():
    if not DB_PATH.exists():
        print("[-] No existe la base de datos de trading.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, match_key, symbol, side, price, amount, pnl, action, raw_payload
            FROM orders_history
            WHERE pnl IS NOT NULL AND pnl != 0.0
        """)
        rows = cursor.fetchall()

    if not rows:
        print("[-] No hay operaciones con PnL finalizado registrado en la base de datos.")
        return

    winners = []
    losers = []

    for row in rows:
        record = {
            "id": row[0],
            "timestamp": row[1],
            "match_key": row[2],
            "symbol": row[3],
            "side": row[4],
            "price": row[5],
            "amount": row[6],
            "pnl": row[7],
            "action": row[8],
            "raw_payload": json.loads(row[9]) if row[9] else {}
        }
        
        if record["pnl"] > 0:
            winners.append(record)
        else:
            losers.append(record)

    print(f"\n[+] Dataset extraído exitosamente:")
    print(f"    - Operaciones Ganadoras: {len(winners)}")
    print(f"    - Operaciones Perdedoras: {len(losers)}")
    print(f"    - Ratio Win/Loss actual: {len(winners)/(len(winners)+len(losers))*100:.1f}%\n")

    if losers:
        print("=== MUESTRA DE OPERACIONES PERDEDORAS (Para entrenamiento de contraste) ===")
        for loss in losers:
            print(f"ID: {loss['id']} | Símbolo: {loss['symbol']} | Lado: {loss['side']} | Precio Entrada: {loss['price']} | PnL: {loss['pnl']}")

    return winners, losers

if __name__ == "__main__":
    extract_dataset()
