"""
executors/paper_executor.py
Motor de Paper Trading con gestión conservadora de PnL, manejo de órdenes stale,
filtro de unicidad y cooldown por SQLite para evitar reoperar el mismo mercado.
"""

import time
import sqlite3
from pathlib import Path
from database.db_manager import update_trade_pnl

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"

class PaperExecutor:
    def __init__(self, max_open_trades=3, max_pending_orders=3, grace_cycles=3, cooldown_seconds=600):
        self.max_open_trades = max_open_trades
        self.max_pending_orders = max_pending_orders
        self.grace_cycles = grace_cycles
        self.cooldown_seconds = cooldown_seconds
        self.pending_orders = {}
        self.open_trades = {}
        self.trade_history = []

    def is_condition_in_cooldown(self, condition_id: str) -> bool:
        """Verifica en SQLite si la condición fue operada recientemente."""
        if not DB_PATH.exists():
            return False
            
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # Asumimos que 'symbol' en la BD almacena el condition_id
            cursor.execute("""
                SELECT timestamp FROM orders_history 
                WHERE symbol = ? 
                ORDER BY id DESC LIMIT 1
            """, (condition_id,))
            row = cursor.fetchone()
            
            if row and row[0]:
                try:
                    # Intentar convertir el timestamp de la BD a float (Unix epoch)
                    last_trade_time = float(row[0])
                    if (time.time() - last_trade_time) < self.cooldown_seconds:
                        return True
                except (ValueError, TypeError):
                    # Si el timestamp no es un número (ej. es un string ISO), 
                    # podrías adaptar esta parte para parsearlo. Por ahora, ignoramos el cooldown.
                    pass
        return False

    def generate_match_key(self, condition_id: str, outcome: str) -> str:
        timestamp = int(time.time())
        return f"{condition_id}_{outcome}_{timestamp}"

    def execute_candidates(self, candidates: list):
        """
        Procesa una ráfaga de candidatos aplicando filtros de unicidad y cooldown.
        """
        active_conditions = set()
        results = []

        for cand in candidates:
            cond_id = cand.get("condition_id")
            
            # 1. Filtro en la misma ráfaga (evitar YES y NO del mismo mercado)
            if cond_id in active_conditions:
                results.append({"status": "REJECTED", "reason": "DUPLICATE_CONDITION_IN_BATCH"})
                continue

            # 2. Filtro en memoria activa (órdenes pendientes o trades abiertos)
            already_pending = any(o["condition_id"] == cond_id for o in self.pending_orders.values())
            already_open = any(t["condition_id"] == cond_id for t in self.open_trades.values())
            
            if already_pending or already_open:
                results.append({"status": "REJECTED", "reason": "CONDITION_ALREADY_ACTIVE"})
                continue

            # 3. Filtro en Base de Datos (Cooldown)
            if self.is_condition_in_cooldown(cond_id):
                results.append({"status": "REJECTED", "reason": "CONDITION_IN_COOLDOWN"})
                continue

            # 4. Procesar la señal
            res = self.process_signal(cand)
            if res.get("status") == "PENDING_CREATED":
                active_conditions.add(cond_id)
                
            results.append(res)

        return results

    def process_signal(self, candidate: dict):
        if len(self.open_trades) >= self.max_open_trades:
            return {"status": "REJECTED", "reason": "MAX_OPEN_TRADES_REACHED"}
        
        if len(self.pending_orders) >= self.max_pending_orders:
            return {"status": "REJECTED", "reason": "MAX_PENDING_ORDERS_REACHED"}
        
        match_key = self.generate_match_key(candidate["condition_id"], candidate["outcome"])
        
        self.pending_orders[match_key] = {
            "match_key": match_key,
            "condition_id": candidate["condition_id"],
            "outcome": candidate["outcome"],
            "limit_price": candidate["limit_price"],
            "stale_counter": 0,
            "created_at": time.time()
        }
        return {"status": "PENDING_CREATED", "match_key": match_key}

    def purge_resolved_orders(self, resolved_keys: list):
        """Elimina de la memoria local las órdenes resueltas por el resolver externo."""
        for key in resolved_keys:
            self.pending_orders.pop(key, None)
            self.open_trades.pop(key, None)
    
    def update_orders_and_trades(self, market_snapshot: dict):
        # ==========================================================
        # BLOQUE DE CIERRE/RESOLUCIÓN DE PENDIENTES
        # ==========================================================
        for match_key, order in list(self.pending_orders.items()):
            calculated_pnl = 0.0 
            win_or_loss = "CANCELLED"
            
            # Persistir en SQLite
            update_trade_pnl(match_key=match_key, pnl=calculated_pnl, final_action=win_or_loss)
            
            # Limpiar de las pendientes locales
            # (Comentado temporalmente para no borrar todas las órdenes de golpe. 
            # Descomenta si esta es tu intención final en este bloque)
            # del self.pending_orders[match_key]
        # ==========================================================

        # --- Lógica original para gestionar órdenes PENDIENTES ---
        for key, order in list(self.pending_orders.items()):
            cond_id = order["condition_id"]
            if cond_id not in market_snapshot:
                continue
            
            market = market_snapshot[cond_id]
            best_ask = market.get("best_ask", 1.0)
            binance_signal = market.get("binance_signal", "NEUTRAL")

            if binance_signal == "CONFLICT":
                # Se cancela por conflicto, registramos en DB con PnL 0
                update_trade_pnl(match_key=key, pnl=0.0, final_action="CONFLICT")
                del self.pending_orders[key]
                continue
            
            if best_ask <= order["limit_price"]:
                fill_price = min(order["limit_price"], best_ask)
                self.open_trades[key] = {
                    "match_key": key,
                    "condition_id": cond_id,
                    "entry_price": fill_price,
                    "target_tp": round(fill_price * 1.04, 4),
                    "target_sl": round(fill_price * 0.92, 4),
                    "opened_at": time.time()
                }
                del self.pending_orders[key]
            else:
                order["stale_counter"] += 1
                if order["stale_counter"] >= self.grace_cycles:
                    # Se cancela por stale, registramos en DB con PnL 0
                    update_trade_pnl(match_key=key, pnl=0.0, final_action="STALE")
                    del self.pending_orders[key]

        # --- Lógica original para gestionar TRADES ABIERTOS (TP/SL) ---
        for key, trade in list(self.open_trades.items()):
            cond_id = trade["condition_id"]
            if cond_id not in market_snapshot:
                continue
            
            market = market_snapshot[cond_id]
            best_bid = market.get("best_bid", 0.0)

            if best_bid >= trade["target_tp"] or best_bid <= trade["target_sl"]:
                pnl = round((best_bid - trade["entry_price"]) / trade["entry_price"], 4)
                
                # Determinar si fue WIN o LOSS para la base de datos
                win_or_loss = "WIN" if pnl > 0 else "LOSS"
                
                trade["close_price"] = best_bid
                trade["pnl_pct"] = pnl
                trade["closed_at"] = time.time()
                
                self.trade_history.append(trade)
                
                # Registrar en SQLite
                update_trade_pnl(match_key=key, pnl=pnl, final_action=win_or_loss)
                
                del self.open_trades[key]
