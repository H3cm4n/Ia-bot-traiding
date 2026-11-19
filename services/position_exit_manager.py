"""
services/position_exit_manager.py
Gestor de cierre de posiciones y salidas anticipadas (TP / SL / Edge Decay)
utilizando el VWAP Bid real en Polymarket CLOB.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import DB_PATH
from services.clob_client import PolymarketCLOBClient

logger = logging.getLogger("exit_manager")


class PositionExitManager:
    def __init__(
        self,
        take_profit_pct: float = 0.25,  # Take-Profit en +25%
        stop_loss_pct: float = -0.20,   # Stop-Loss en -20%
        clob_client: Optional[PolymarketCLOBClient] = None
    ):
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.clob = clob_client or PolymarketCLOBClient()

    @staticmethod
    def get_open_positions() -> List[Dict[str, Any]]:
        """Recupera las órdenes activas en estado PENDING_CREATED o FILLED desde SQLite."""
        if not DB_PATH.exists():
            return []

        positions = []
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT match_key, symbol, side, price as entry_price, amount, token_id
                    FROM orders_history
                    WHERE action IN ('PENDING_CREATED', 'FILLED') AND is_settled = 0
                """)
                rows = cursor.fetchall()
                for row in rows:
                    positions.append(dict(row))
        except sqlite3.Error as e:
            logger.error(f"[EXIT] Error consultando posiciones abiertas en SQLite: {e}")

        return positions

    def evaluate_position_exit(
        self,
        position: Dict[str, Any],
        current_fair_prob: Optional[float] = None,
        book: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evalúa si una posición individual debe cerrarse por TP, SL o pérdida de ventaja.
        Acepta un `book` opcional para pruebas sintéticas o consultas pre-cargadas.
        """
        match_key = position.get("match_key")
        token_id = position.get("token_id")
        entry_price = float(position.get("entry_price", 0.0))
        amount_usdc = float(position.get("amount", 0.0))

        if entry_price <= 0 or amount_usdc <= 0:
            return {"should_exit": False, "reason": "INVALID_POSITION_DATA"}

        # 1. Obtener libro de ordenes (simulado o via API CLOB)
        if book is None:
            if not token_id or token_id == "N/A":
                return {"should_exit": False, "reason": "MISSING_TOKEN_ID"}
            book = self.clob.get_order_book(token_id)

        bids = book.get("bids", [])

        # Número de acciones (shares) = Monto USDC / Precio Entrada
        shares = amount_usdc / entry_price
        vwap_res = self.clob.calculate_vwap_bid(bids, target_shares=shares)

        if not vwap_res.get("valid", False):
            return {"should_exit": False, "reason": "INSUFFICIENT_BID_LIQUIDITY"}

        exit_price = vwap_res["vwap_price"]
        pnl_pct = (exit_price - entry_price) / entry_price
        pnl_usdc = round(shares * (exit_price - entry_price), 2)

        # 2. Evaluación de Reglas de Salida
        if pnl_pct >= self.take_profit_pct:
            return {
                "should_exit": True,
                "reason": "TAKE_PROFIT",
                "match_key": match_key,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_usdc": pnl_usdc,
            }

        if pnl_pct <= self.stop_loss_pct:
            return {
                "should_exit": True,
                "reason": "STOP_LOSS",
                "match_key": match_key,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_usdc": pnl_usdc,
            }

        if current_fair_prob is not None:
            ev_current = current_fair_prob - exit_price
            if ev_current <= 0:
                return {
                    "should_exit": True,
                    "reason": "EDGE_DECAY",
                    "match_key": match_key,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "pnl_usdc": pnl_usdc,
                }

        return {
            "should_exit": False,
            "reason": "HOLDING",
            "pnl_pct": round(pnl_pct * 100, 2),
            "pnl_usdc": pnl_usdc,
        }