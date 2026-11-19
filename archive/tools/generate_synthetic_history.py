"""
tools/generate_synthetic_history.py
Genera historial sintético refinado en SQLite.
"""

import sqlite3
import random
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

def generate_synthetic_data(num_records=150):
    if not DB_PATH.exists():
        print("[-] No se encontró la base de datos.")
        return

    # Limpiar tabla anterior para entrenar con patrones limpios y bien definidos
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM orders_history WHERE match_key LIKE '%_synth_%'")
        conn.commit()

    print(f"[+] Generando {num_records} registros sintéticos refinados...")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        for i in range(num_records):
            timestamp = f"2026-10-09T{random.randint(10,23):02d}:{random.randint(10,59):02d}:{random.randint(10,59):02d}"
            condition_id = f"0x_synth_{random.randint(10000, 99999)}"
            match_key = f"{condition_id}_YES_{int(time.time())}_{i}"
            
            price = round(random.uniform(0.10, 0.85), 2)
            amount = 10.0
            
            # Probabilidad de ganar proporcional al precio de entrada (a mayor precio, mayor probabilidad)
            win_probability = price
            is_win = random.random() < win_probability
            
            if is_win:
                pnl = round(amount * (1.0 - price), 2)
                action = "WIN"
            else:
                pnl = round(-1 * (amount * price), 2)
                action = "LOSS"

            cursor.execute("""
                INSERT INTO orders_history (timestamp, match_key, action, symbol, side, price, amount, pnl, raw_payload)
                VALUES (?, ?, ?, ?, 'YES', ?, ?, ?, ?)
            """, (
                timestamp, match_key, action, condition_id, price, amount, pnl,
                f'{{"condition_id": "{condition_id}", "outcome": "YES", "limit_price": {price}, "amount": {amount}}}'
            ))

        conn.commit()

    print("[+] Generación completada con éxito.")

if __name__ == "__main__":
    generate_synthetic_data(150)
