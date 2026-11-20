"""
services/risk_manager.py
Gestión de riesgo centralizada y Sizing Dinámico con Criterio de Kelly Fraccionado (1/4 Kelly).
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

from config import (
    DB_PATH,
    DEFAULT_ORDER_AMOUNT_USDC,
    MAX_EXECUTABLE_PRICE,
    MAX_HOURS_TO_EXPIRY,
    MAX_PENDING_ORDERS,
    MIN_EXECUTABLE_PRICE,
)

logger = logging.getLogger("risk_manager")

# Configuración de Riesgo por Defecto
MAX_RISK_USD = float(os.getenv("MAX_RISK_USD", 15.0))

class RiskManager:
    def __init__(
        self,
        initial_capital_usdc: float = 1000.0,
        max_daily_loss_usdc: float = 100.0,
        max_portfolio_exposure_pct: float = 0.25, # Máximo 25% de capital en juego
        kelly_fraction: float = 0.25,             # 1/4 Kelly
        min_trade_pct: float = 0.02,              # Piso 2%
        max_trade_pct: float = 0.10,              # Techo 10%
    ):
        self.initial_capital_usdc = initial_capital_usdc
        self.max_daily_loss_usdc = max_daily_loss_usdc
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
        self.kelly_fraction = kelly_fraction
        self.min_trade_pct = min_trade_pct
        self.max_trade_pct = max_trade_pct

    def get_current_balance(self) -> float:
        """Calcula el balance vivo sumando PnL realizado al capital inicial."""
        if not DB_PATH.exists():
            return self.initial_capital_usdc

        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                # En orders_history la columna es 'pnl' y las acciones relevantes son 'CLOSED' o 'EXIT'
                cursor.execute("SELECT SUM(pnl) FROM orders_history WHERE action IN ('CLOSED', 'EXIT', 'WIN', 'LOSS')")
                row = cursor.fetchone()
                realized_pnl = row[0] if row and row[0] is not None else 0.0
                return max(100.0, self.initial_capital_usdc + realized_pnl)
        except Exception as e:
            logger.warning(f"Error calculating balance: {e}")
            return self.initial_capital_usdc

    def get_active_exposure(self) -> float:
        """Calcula el capital actualmente comprometido en posiciones abiertas."""
        if not DB_PATH.exists():
            return 0.0
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                # Usamos price y amount de orders_history. amount es USD.
                cursor.execute("""
                    SELECT SUM(amount) FROM orders_history o
                    WHERE action IN ('ENTRY', 'FILL', 'PENDING_CREATED', 'PAPER_FILLED')
                    AND NOT EXISTS (
                        SELECT 1 FROM orders_history 
                        WHERE match_key = o.match_key AND action IN ('CLOSED', 'EXIT', 'WIN', 'LOSS', 'CANCELED')
                    )
                """)
                row = cursor.fetchone()
                return row[0] if row and row[0] is not None else 0.0
        except Exception as e:
            logger.warning(f"Error calculating exposure: {e}")
            return 0.0

    def has_open_position(self, market_id_or_key: str) -> bool:
        """Indica si ya existe una posición abierta para ese mercado."""
        if not DB_PATH.exists():
            return False
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM orders_history o
                    WHERE match_key = ? 
                    AND action IN ('ENTRY', 'FILL', 'PENDING_CREATED', 'PAPER_FILLED')
                    AND NOT EXISTS (
                        SELECT 1 FROM orders_history 
                        WHERE match_key = o.match_key AND action IN ('CLOSED', 'EXIT', 'CANCELED', 'WIN', 'LOSS')
                    )
                    LIMIT 1
                """, (str(market_id_or_key),))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def calculate_risk_based_size(self, entry_price: float) -> float:
        """
        Calcula el tamaño de la posición en USD para que el riesgo sea MAX_RISK_USD.
        Si compramos 'shares' a 'entry_price', el costo (y pérdida máxima) es shares * entry_price.
        Queremos shares * entry_price = MAX_RISK_USD => shares = MAX_RISK_USD / entry_price.
        El monto a invertir en USD es MAX_RISK_USD.
        """
        if entry_price <= 0:
            return 0.0
        
        # El usuario pide calcular acciones como round(MAX_RISK_USD / entry_price, 2)
        # Pero el bot opera en USD (amount), así que invertimos MAX_RISK_USD.
        # Esto garantiza que si el contrato va a 0, perdemos exactamente MAX_RISK_USD.
        return round(MAX_RISK_USD, 2)

    def calculate_fractional_kelly_size(
        self, fair_prob: float, price: float, available_capital: Optional[float] = None
    ) -> float:
        """
        Calcula el monto en USDC aplicando 1/4 Kelly con cotas [2%, 10%].
        Ahora también limitado por MAX_RISK_USD.
        """
        balance = available_capital if available_capital is not None else self.get_current_balance()
        
        if price <= 0 or price >= 1.0 or fair_prob <= price:
            return 0.0

        b = (1.0 - price) / price  # Odds netas (Decimal)
        p = fair_prob
        q = 1.0 - p

        # Kelly Full: f = (bp - q) / b
        full_kelly = (b * p - q) / b
        if full_kelly <= 0:
            return 0.0

        # Kelly Fraccional (25%)
        target_fraction = full_kelly * self.kelly_fraction
        
        # Aplicar límites [2%, 10%] del balance actual
        final_fraction = max(self.min_trade_pct, min(self.max_trade_pct, target_fraction))
        
        position_usd = balance * final_fraction

        # CAP por Riesgo Máximo USD
        position_usd = min(position_usd, MAX_RISK_USD)

        # Verificar si excede la exposición total de cartera (25%)
        active_exposure = self.get_active_exposure()
        remaining_portfolio_space = (balance * self.max_portfolio_exposure_pct) - active_exposure
        
        final_size = min(position_usd, remaining_portfolio_space)
        
        return round(max(0.0, final_size), 2)

    def is_kill_switch_active(self) -> bool:
        """Verifica si la pérdida diaria acumulada cruzó el tope."""
        if not DB_PATH.exists():
            return False
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT SUM(pnl) FROM orders_history
                    WHERE date(timestamp) = date('now') AND pnl < 0
                """)
                row = cursor.fetchone()
                daily_loss = abs(row[0]) if row and row[0] is not None else 0.0
                return daily_loss >= self.max_daily_loss_usdc
        except Exception:
            return False

    def validate_expiry(
        self, end_date_iso: str, now: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """Rechaza mercados expirados o con cierre superior a MAX_HOURS_TO_EXPIRY."""
        if not end_date_iso:
            return False, "EXPIRY_OUT_OF_RANGE"

        try:
            end_date = datetime.fromisoformat(
                str(end_date_iso).strip().replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return False, "EXPIRY_OUT_OF_RANGE"

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        seconds_left = (end_date - reference).total_seconds()
        if seconds_left <= 0 or seconds_left > MAX_HOURS_TO_EXPIRY * 3600:
            return False, "EXPIRY_OUT_OF_RANGE"

        return True, "PASSED"

    def validate_trade(
        self,
        fair_prob: float,
        price: float,
        end_date_iso: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str, float]:
        """Validación integral antes de disparar."""
        if self.is_kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE", 0.0

        if end_date_iso is not None:
            expiry_ok, expiry_reason = self.validate_expiry(end_date_iso, now=now)
            if not expiry_ok:
                return False, expiry_reason, 0.0

        if price < MIN_EXECUTABLE_PRICE or price > MAX_EXECUTABLE_PRICE:
            return False, "PRICE_OUT_OF_ASYMMETRIC_RANGE", 0.0

        # Usamos Kelly pero capeado por MAX_RISK_USD (implementado en calculate_fractional_kelly_size)
        size = self.calculate_fractional_kelly_size(fair_prob, price)
        
        if size < 5.0: # Monto mínimo operativo para evitar polvo (dust)
            return False, "SIZE_TOO_SMALL", size

        return True, "PASSED", size