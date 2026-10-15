"""
executors/paper_executor.py

Motor de Paper Trading con:
- Registro compatible con orders_history.
- Resolución de condition_id.
- Filtro de duplicados y cooldown SQLite.
- Verificación previa en SQLite (is_market_already_traded) antes de
  registrar una nueva orden pendiente.
- Gestión de órdenes pendientes y órdenes stale.
- Simulación de liquidez mediante VWAP.
- Gestión de trades abiertos con TP/SL.
- Registro de eventos PENDING_CREATED.
- Sin envío de órdenes reales a Polymarket.
"""

import logging
import sqlite3
import time
from pathlib import Path

from database.db_manager import log_order_event, update_trade_pnl


logger = logging.getLogger("paper_executor")

DB_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "trading_bot.db"
)


# ---------------------------------------------------------------------------
# Consulta: ¿ya existe una orden activa o liquidada para este mercado/lado?
#
# Adaptada al esquema REAL de orders_history (ver is_condition_in_cooldown):
#     symbol       <- condition_id
#     side         <- outcome ("YES" / "NO")
#     action       <- 'PENDING_CREATED' al crear la orden
#     final_action <- 'WIN' / 'LOSS' / 'STALE' / 'CONFLICT' al cerrar
#
# Si tu instalación usa otra tabla/columnas, ajusta únicamente este SQL.
# ---------------------------------------------------------------------------
_MARKET_ALREADY_TRADED_SQL = """
    SELECT COUNT(*)
    FROM orders_history
    WHERE symbol = ?
      AND side   = ?
      AND (
            action = 'PENDING_CREATED'
         OR final_action IN ('WIN', 'LOSS')
      )
"""


class PaperExecutor:
    def __init__(
        self,
        max_open_trades=3,
        max_pending_orders=3,
        grace_cycles=3,
        cooldown_seconds=600,
        target_size_usdc=100.0,
    ):
        self.max_open_trades = max_open_trades
        self.max_pending_orders = max_pending_orders
        self.grace_cycles = grace_cycles
        self.cooldown_seconds = cooldown_seconds
        self.target_size_usdc = target_size_usdc

        self.pending_orders = {}
        self.open_trades = {}
        self.trade_history = []

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    @staticmethod
    def _resolve_condition_id(candidate: dict):
        """Obtiene el condition_id del candidato."""

        condition_id = (
            candidate.get("condition_id")
            or candidate.get("conditionId")
            or candidate.get("symbol")
        )

        if condition_id is None:
            return None

        condition_id = str(condition_id).strip()

        return condition_id or None

    @staticmethod
    def _resolve_limit_price(candidate: dict):
        """Obtiene limit_price o, como fallback, price."""

        raw_price = candidate.get("limit_price")

        if raw_price is None:
            raw_price = candidate.get("price")

        try:
            return float(raw_price)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_outcome(candidate: dict):
        """Acepta únicamente los outcomes YES y NO."""

        outcome = str(candidate.get("outcome", "")).strip().upper()

        if outcome not in {"YES", "NO"}:
            return None

        return outcome

    def generate_match_key(
        self,
        condition_id: str,
        outcome: str,
    ) -> str:
        """Genera una clave única para cada orden simulada."""

        timestamp_ns = time.time_ns()

        return f"{condition_id}_{outcome}_{timestamp_ns}"

    # ----------------------------------------------------------
    # Verificación previa en SQLite
    # ----------------------------------------------------------

    def is_market_already_traded(
        self,
        condition_id: str,
        outcome: str,
        db_path: str | None = None,
    ) -> bool:
        """
        Verifica si ya existe una orden activa ('PENDING_CREATED') o ya
        liquidada ('WIN'/'LOSS') en orders_history para este
        condition_id + outcome.

        Devolver True bloquea la creación de una nueva orden, incluso si
        el proceso se reinició y las órdenes pendientes en memoria se
        perdieron.

        Fail-safe: si SQLite falla, se devuelve True para no arriesgar
        duplicados por un error de lectura.
        """
        if not condition_id or not outcome:
            return False

        target_db = Path(db_path) if db_path else DB_PATH

        if not target_db.exists():
            return False

        try:
            with sqlite3.connect(str(target_db)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    _MARKET_ALREADY_TRADED_SQL,
                    (str(condition_id), str(outcome).upper()),
                )
                row = cursor.fetchone()
        except sqlite3.Error:
            logger.exception(
                "Error en is_market_already_traded para %s/%s",
                condition_id,
                outcome,
            )
            # Fail-safe: si no podemos verificar, preferimos no duplicar.
            return True

        if not row:
            return False

        try:
            count = int(row[0])
        except (TypeError, ValueError):
            return False

        return count > 0

    # ----------------------------------------------------------
    # Cooldown persistente
    # ----------------------------------------------------------

    def is_condition_in_cooldown(self, condition_id: str) -> bool:
        """
        Comprueba si existe un evento reciente para la condición.
        Soporta timestamps en formato numérico (POSIX) y formato ISO/texto SQLite.
        """
        if not condition_id or not DB_PATH.exists():
            return False

        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT timestamp
                    FROM orders_history
                    WHERE symbol = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (condition_id,),
                )
                row = cursor.fetchone()

        except sqlite3.Error:
            logger.exception("Error consultando el cooldown en SQLite")
            return True

        if not row or not row[0]:
            return False

        raw_ts = row[0]
        last_trade_time = None

        try:
            last_trade_time = float(raw_ts)
        except (TypeError, ValueError):
            from datetime import datetime, timezone
            try:
                clean_ts = str(raw_ts).replace("Z", "").replace("T", " ")
                dt = datetime.strptime(clean_ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
                last_trade_time = dt.replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                logger.warning(
                    "No se pudo interpretar el timestamp en orders_history: %r",
                    raw_ts,
                )
                return False

        elapsed = time.time() - last_trade_time
        return 0 <= elapsed < self.cooldown_seconds

    # ----------------------------------------------------------
    # Simulación de consumo de liquidez
    # ----------------------------------------------------------

    def simulate_orderbook_fill(
        self,
        orderbook_asks: list,
        target_size_usdc: float,
    ) -> dict:
        """
        Simula una compra recorriendo los niveles de asks.

        Cada nivel debe contener:
            {"price": "0.45", "size": "100"}
        """

        if not orderbook_asks:
            return {"filled": False, "reason": "EMPTY_ORDERBOOK"}

        try:
            remaining_usdc = float(target_size_usdc)
        except (TypeError, ValueError):
            return {"filled": False, "reason": "INVALID_TARGET_SIZE"}

        if remaining_usdc <= 0:
            return {"filled": False, "reason": "INVALID_TARGET_SIZE"}

        total_shares = 0.0
        total_cost = 0.0

        for level in orderbook_asks:
            try:
                price = float(level["price"])
                size = float(level["size"])
            except (KeyError, TypeError, ValueError):
                continue

            if not (0 < price < 1) or size <= 0:
                continue

            level_value_usdc = price * size
            amount_to_spend = min(remaining_usdc, level_value_usdc)

            shares_bought = amount_to_spend / price

            total_shares += shares_bought
            total_cost += amount_to_spend
            remaining_usdc -= amount_to_spend

            if remaining_usdc <= 1e-9:
                remaining_usdc = 0.0
                break

        if remaining_usdc > 1e-9:
            return {
                "filled": False,
                "reason": "INSUFFICIENT_LIQUIDITY_DEPTH",
                "filled_usdc": round(total_cost, 6),
                "unfilled_usdc": round(remaining_usdc, 6),
                "total_shares": round(total_shares, 6),
            }

        if total_shares <= 0:
            return {"filled": False, "reason": "NO_VALID_LIQUIDITY"}

        vwap_price = total_cost / total_shares

        return {
            "filled": True,
            "vwap_price": round(vwap_price, 6),
            "total_shares": round(total_shares, 6),
            "total_cost_usdc": round(total_cost, 6),
        }

    # ----------------------------------------------------------
    # Pipeline de candidatos
    # ----------------------------------------------------------

    def execute_candidates(self, candidates: list) -> list:
        """
        Procesa candidatos evitando duplicados, mercados activos
        y operaciones dentro del periodo de cooldown.
        """

        active_conditions = set()
        results = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                results.append({
                    "status": "REJECTED",
                    "reason": "INVALID_CANDIDATE",
                })
                continue

            condition_id = self._resolve_condition_id(candidate)

            if not condition_id:
                results.append({
                    "status": "REJECTED",
                    "reason": "MISSING_CONDITION_ID",
                })
                continue

            if condition_id in active_conditions:
                results.append({
                    "status": "REJECTED",
                    "reason": "DUPLICATE_CONDITION_IN_BATCH",
                    "condition_id": condition_id,
                })
                continue

            already_pending = any(
                order["condition_id"] == condition_id
                for order in self.pending_orders.values()
            )

            already_open = any(
                trade["condition_id"] == condition_id
                for trade in self.open_trades.values()
            )

            if already_pending or already_open:
                results.append({
                    "status": "REJECTED",
                    "reason": "CONDITION_ALREADY_ACTIVE",
                    "condition_id": condition_id,
                })
                continue

            if self.is_condition_in_cooldown(condition_id):
                results.append({
                    "status": "REJECTED",
                    "reason": "CONDITION_IN_COOLDOWN",
                    "condition_id": condition_id,
                })
                continue

            result = self.process_signal(candidate)

            if result.get("status") == "PENDING_CREATED":
                active_conditions.add(condition_id)

            result.setdefault("condition_id", condition_id)
            results.append(result)

        return results

    # ----------------------------------------------------------
    # Crear y registrar una orden pendiente
    # ----------------------------------------------------------

    def process_signal(self, candidate: dict) -> dict:
        """Valida, registra y crea una orden exclusivamente simulada."""

        condition_id = self._resolve_condition_id(candidate)

        if not condition_id:
            return {
                "status": "REJECTED",
                "reason": "MISSING_CONDITION_ID",
            }

        outcome = self._resolve_outcome(candidate)

        if not outcome:
            return {
                "status": "REJECTED",
                "reason": "INVALID_OUTCOME",
                "condition_id": condition_id,
            }

        # --- Verificación previa en SQLite -------------------------------
        # Bloquea duplicados incluso si el proceso se reinició y las
        # órdenes en memoria se perdieron. Se ejecuta ANTES de validar
        # precio/tamaño para fallar rápido y no ensuciar logs con
        # rechazos irrelevantes.
        if self.is_market_already_traded(condition_id, outcome):
            logger.info(
                "MARKET_ALREADY_TRADED | %s [%s] ya existe en orders_history "
                "(PENDING_CREATED/WIN/LOSS).",
                condition_id,
                outcome,
            )
            return {
                "status": "REJECTED",
                "reason": "MARKET_ALREADY_TRADED",
                "condition_id": condition_id,
                "outcome": outcome,
            }
        # ------------------------------------------------------------------

        limit_price = self._resolve_limit_price(candidate)

        if limit_price is None or not 0 < limit_price < 1:
            return {
                "status": "REJECTED",
                "reason": "INVALID_LIMIT_PRICE",
                "condition_id": condition_id,
            }

        if len(self.open_trades) >= self.max_open_trades:
            return {
                "status": "REJECTED",
                "reason": "MAX_OPEN_TRADES_REACHED",
                "condition_id": condition_id,
            }

        if len(self.pending_orders) >= self.max_pending_orders:
            return {
                "status": "REJECTED",
                "reason": "MAX_PENDING_ORDERS_REACHED",
                "condition_id": condition_id,
            }

        try:
            target_size_usdc = float(
                candidate.get("target_size_usdc", self.target_size_usdc)
            )
        except (TypeError, ValueError):
            return {
                "status": "REJECTED",
                "reason": "INVALID_TARGET_SIZE",
                "condition_id": condition_id,
            }

        if target_size_usdc <= 0:
            return {
                "status": "REJECTED",
                "reason": "INVALID_TARGET_SIZE",
                "condition_id": condition_id,
            }

        match_key = self.generate_match_key(condition_id, outcome)

        symbol = str(candidate.get("symbol") or condition_id)

        try:
            amount = float(candidate.get("amount", target_size_usdc))
        except (TypeError, ValueError):
            return {
                "status": "REJECTED",
                "reason": "INVALID_AMOUNT",
                "condition_id": condition_id,
            }

        if amount <= 0:
            return {
                "status": "REJECTED",
                "reason": "INVALID_AMOUNT",
                "condition_id": condition_id,
            }

        try:
            log_order_event(
                match_key=match_key,
                action="PENDING_CREATED",
                symbol=symbol,
                side=outcome,
                price=float(limit_price),
                amount=amount,
            )
        except Exception:
            logger.exception(
                "No se pudo registrar PENDING_CREATED: %s",
                match_key,
            )
            return {
                "status": "REJECTED",
                "reason": "ORDER_HISTORY_WRITE_FAILED",
                "condition_id": condition_id,
                "match_key": match_key,
            }

        self.pending_orders[match_key] = {
            "match_key": match_key,
            "condition_id": condition_id,
            "symbol": symbol,
            "outcome": outcome,
            "limit_price": float(limit_price),
            "amount": amount,
            "target_size_usdc": target_size_usdc,
            "stale_counter": 0,
            "created_at": time.time(),
        }

        logger.info(
            "PENDING_CREATED | match_key=%s | side=%s | price=%.4f | amount=%.2f",
            match_key,
            outcome,
            limit_price,
            amount,
        )

        return {
            "status": "PENDING_CREATED",
            "match_key": match_key,
            "condition_id": condition_id,
            "outcome": outcome,
            "limit_price": limit_price,
            "amount": amount,
        }

    # ----------------------------------------------------------
    # Sincronización con un resolver externo
    # ----------------------------------------------------------

    def purge_resolved_orders(self, resolved_keys: list):
        """Elimina órdenes o trades resueltos del estado en memoria."""

        for key in resolved_keys:
            self.pending_orders.pop(key, None)
            self.open_trades.pop(key, None)

    def cancel_all_pending(self, reason: str = "MANUAL_FLUSH"):
        """Cancela todas las órdenes pendientes del simulador."""

        for key in list(self.pending_orders):
            try:
                update_trade_pnl(
                    match_key=key,
                    pnl=0.0,
                    final_action=reason,
                )
            except Exception:
                logger.exception(
                    "No se pudo actualizar el evento al cancelar %s",
                    key,
                )
                continue

            del self.pending_orders[key]

    # ----------------------------------------------------------
    # Actualizar órdenes pendientes y trades abiertos
    # ----------------------------------------------------------

    def update_orders_and_trades(self, market_snapshot: dict) -> list:
        """
        Actualiza órdenes pendientes y trades abiertos a partir
        de una instantánea de mercado.

        Esta función no envía órdenes reales.
        """

        events = []

        # A. Órdenes pendientes
        for key, order in list(self.pending_orders.items()):
            condition_id = order["condition_id"]

            if condition_id not in market_snapshot:
                continue

            market = market_snapshot[condition_id]
            binance_signal = market.get("binance_signal", "NEUTRAL")

            if binance_signal == "CONFLICT":
                try:
                    update_trade_pnl(
                        match_key=key,
                        pnl=0.0,
                        final_action="CONFLICT",
                    )
                except Exception:
                    logger.exception(
                        "Error registrando cancelación por conflicto: %s",
                        key,
                    )
                    continue

                del self.pending_orders[key]

                events.append({
                    "status": "CANCELLED",
                    "reason": "CONFLICT",
                    "match_key": key,
                    "condition_id": condition_id,
                })
                continue

            filled = False
            fill_price = None
            orderbook_asks = market.get("orderbook_asks") or []

            if orderbook_asks:
                simulation = self.simulate_orderbook_fill(
                    orderbook_asks,
                    order.get("target_size_usdc", self.target_size_usdc),
                )

                if simulation.get("filled"):
                    vwap_price = simulation["vwap_price"]
                    if vwap_price <= order["limit_price"]:
                        filled = True
                        fill_price = vwap_price
            else:
                best_ask = market.get("best_ask")
                try:
                    best_ask = float(best_ask)
                except (TypeError, ValueError):
                    best_ask = None

                if (
                    best_ask is not None
                    and 0 < best_ask < 1
                    and best_ask <= order["limit_price"]
                ):
                    filled = True
                    fill_price = best_ask

            if filled:
                self.open_trades[key] = {
                    "match_key": key,
                    "condition_id": condition_id,
                    "symbol": order.get("symbol", condition_id),
                    "outcome": order.get("outcome"),
                    "entry_price": fill_price,
                    "size_usdc": order.get(
                        "target_size_usdc", self.target_size_usdc
                    ),
                    "target_tp": round(fill_price * 1.04, 4),
                    "target_sl": round(fill_price * 0.92, 4),
                    "opened_at": time.time(),
                }

                del self.pending_orders[key]

                events.append({
                    "status": "PAPER_FILLED",
                    "match_key": key,
                    "condition_id": condition_id,
                    "fill_price": fill_price,
                })

                logger.info("PAPER_FILLED | %s | precio=%.4f", key, fill_price)

            else:
                order["stale_counter"] += 1

                if order["stale_counter"] >= self.grace_cycles:
                    try:
                        update_trade_pnl(
                            match_key=key,
                            pnl=0.0,
                            final_action="STALE",
                        )
                    except Exception:
                        logger.exception(
                            "Error registrando orden stale: %s", key
                        )
                        continue

                    del self.pending_orders[key]

                    events.append({
                        "status": "CANCELLED",
                        "reason": "STALE",
                        "match_key": key,
                        "condition_id": condition_id,
                    })

        # B. Trades abiertos: take profit y stop loss
        for key, trade in list(self.open_trades.items()):
            condition_id = trade["condition_id"]

            if condition_id not in market_snapshot:
                continue

            market = market_snapshot[condition_id]
            best_bid = market.get("best_bid")

            try:
                best_bid = float(best_bid)
            except (TypeError, ValueError):
                continue

            if not 0 < best_bid < 1:
                continue

            take_profit = best_bid >= trade["target_tp"]
            stop_loss = best_bid <= trade["target_sl"]

            if not (take_profit or stop_loss):
                continue

            entry_price = trade["entry_price"]

            if entry_price <= 0:
                continue

            pnl_pct = round((best_bid - entry_price) / entry_price, 4)
            pnl_usdc = round(trade["size_usdc"] * pnl_pct, 4)

            final_action = "WIN" if pnl_usdc > 0 else "LOSS"

            trade["close_price"] = best_bid
            trade["pnl_pct"] = pnl_pct
            trade["pnl_usdc"] = pnl_usdc
            trade["closed_at"] = time.time()

            try:
                update_trade_pnl(
                    match_key=key,
                    pnl=pnl_usdc,
                    final_action=final_action,
                )
            except Exception:
                logger.exception(
                    "No se pudo guardar el cierre del trade: %s", key
                )
                continue

            self.trade_history.append(dict(trade))
            del self.open_trades[key]

            events.append({
                "status": "PAPER_CLOSED",
                "match_key": key,
                "condition_id": condition_id,
                "close_price": best_bid,
                "pnl_pct": pnl_pct,
                "pnl_usdc": pnl_usdc,
                "final_action": final_action,
            })

            logger.info(
                "PAPER_CLOSED | %s | acción=%s | PnL=%.4f USDC",
                key,
                final_action,
                pnl_usdc,
            )

        return events