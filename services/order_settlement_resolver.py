"""
services/order_settlement_resolver.py
Resolución de órdenes y retorno de match_keys liquidadas para sincronización de memoria.
"""

import sqlite3
import json
import urllib.request
import logging
from pathlib import Path
from database.db_manager import update_trade_pnl

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"
logger = logging.getLogger("settlement_resolver")

def check_and_settle_orders() -> list:
    """
    Consulta órdenes PENDING_CREATED en SQLite, verifica su resolución en la API de Polymarket
    y devuelve una lista con los match_keys de las órdenes que han sido liquidadas.
    """
    resolved_keys = []
    
    if not DB_PATH.exists():
        return resolved_keys

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT match_key, symbol, side, price, amount 
            FROM orders_history 
            WHERE action = 'PENDING_CREATED'
        """)
        pending_orders = cursor.fetchall()

    if not pending_orders:
        return resolved_keys

    logger.info(f"[RESOLVER] Evaluando resolución para {len(pending_orders)} órdenes pendientes...")

    for match_key, condition_id, side, entry_price, amount in pending_orders:
        url = f"https://gamma-api.polymarket.com/markets?condition_id={condition_id}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    if not data:
                        continue
                    
                    market = data[0]
                    closed = market.get("closed", False)
                    outcome_prices = market.get("outcomePrices")

                    if not outcome_prices:
                        continue

                    prices = json.loads(outcome_prices)
                    current_price_yes = float(prices[0])
                    current_price = current_price_yes if side == "YES" else (1.0 - current_price_yes)

                    # Caso 1: El mercado ya cerró oficialmente
                    if closed:
                        is_win = current_price >= 0.95
                        final_action = "WIN" if is_win else "LOSS"
                        pnl = round(amount * (1.0 - entry_price), 2) if is_win else round(-1 * (amount * entry_price), 2)
                        
                        update_trade_pnl(match_key=match_key, pnl=pnl, final_action=final_action)
                        resolved_keys.append(match_key) # <-- AGREGADO A LA LISTA
                        logger.info(f"[RESOLVER] Mercado cerrado. MatchKey: {match_key} | Resultado: {final_action} | PnL: ${pnl}")
                    
                    # Caso 2: El mercado sigue abierto, evaluar TP/SL dinámico
                    else:
                        price_change = current_price - entry_price
                        
                        # Take-Profit +30% | Stop-Loss -15%
                        if price_change >= 0.30:
                            pnl = round(amount * price_change, 2)
                            update_trade_pnl(match_key=match_key, pnl=pnl, final_action="WIN")
                            resolved_keys.append(match_key) # <-- AGREGADO A LA LISTA
                            logger.info(f"[RESOLVER] Take-Profit alcanzado (+30%). MatchKey: {match_key} | PnL: ${pnl}")
                        
                        elif price_change <= -0.15:
                            pnl = round(amount * price_change, 2)
                            update_trade_pnl(match_key=match_key, pnl=pnl, final_action="LOSS")
                            resolved_keys.append(match_key) # <-- AGREGADO A LA LISTA
                            logger.info(f"[RESOLVER] Stop-Loss ajustado (-15%). MatchKey: {match_key} | PnL: ${pnl}")

        except Exception as e:
            logger.warning(f"[RESOLVER] Error verificando mercado {condition_id[:10]}: {e}")

    return resolved_keys

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    resueltas = check_and_settle_orders()
    if resueltas:
        print(f"Órdenes liquidadas en esta ejecución: {resueltas}")
