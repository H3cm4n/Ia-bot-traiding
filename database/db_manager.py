"""
database/db_manager.py
Módulo para el registro estructurado de eventos y ejecuciones en SQLite.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

def init_db():
    """Inicializa las tablas de la base de datos si no existen."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Tabla de ejecuciones de órdenes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                match_key TEXT NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                pnl REAL DEFAULT 0.0,
                raw_payload TEXT
            )
        """)
        
        # Tabla de snapshots de monitoreo (para evitar el sesgo de supervivencia)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                candidates_count INTEGER NOT NULL,
                executed_count INTEGER NOT NULL,
                status TEXT NOT NULL
            )
        """)
        conn.commit()

def log_order_event(match_key: str, action: str, symbol: str = None, side: str = None, price: float = None, amount: float = None, raw_data: dict = None):
    """Guarda un evento de orden en la base de datos."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders_history (timestamp, match_key, action, symbol, side, price, amount, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            match_key,
            action,
            symbol,
            side,
            price,
            amount,
            json.dumps(raw_data) if raw_data else None
        ))
        conn.commit()

def update_trade_pnl(match_key: str, pnl: float, final_action: str = "CLOSED"):
    """Actualiza el PnL y estado final de un trade cerrado por match_key."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE orders_history
            SET pnl = ?, action = ?
            WHERE match_key = ?
        """, (pnl, final_action, match_key))
        conn.commit()
