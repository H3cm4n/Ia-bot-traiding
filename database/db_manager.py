"""
database/db_manager.py

Registro estructurado de eventos, snapshots y candidatos rechazados en SQLite.

Tablas:
  - orders_history:      eventos de órdenes (ENTRY, FILL, CLOSED...).
  - snapshots_log:        snapshots de monitoreo (anti sesgo de supervivencia).
  - rejected_candidates: candidatos descartados por Edge/Riesgo.
"""

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH

log = logging.getLogger(__name__)

_initialized = False

__all__ = [
    "get_connection",
    "init_db",
    "log_order_event",
    "close_trade",
    "update_trade_pnl",
    "log_rejected_candidate",
    "is_market_already_traded",
]


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """Timestamp ISO 8601 UTC único para todas las tablas."""
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path=None):
    target_path = db_path or DB_PATH
    return sqlite3.connect(target_path)


# ---------------------------------------------------------------------------
# Inicialización del esquema
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Crea tablas e índices si no existen."""
    global _initialized
    if _initialized:
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                match_key   TEXT NOT NULL,
                action      TEXT NOT NULL,
                symbol      TEXT,
                side        TEXT,
                price       REAL,
                amount      REAL,
                pnl         REAL DEFAULT 0.0,
                raw_payload TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshots_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                candidates_count INTEGER NOT NULL,
                executed_count   INTEGER NOT NULL,
                status           TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS rejected_candidates (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                match_key        TEXT,
                reason           TEXT,
                fair_probability REAL,
                executable_price REAL,
                edge             REAL,
                raw_data         TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS whale_comparisons (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         TEXT NOT NULL,
                condition_id      TEXT NOT NULL,
                symbol            TEXT,
                bot_side          TEXT,
                bot_fair_prob     REAL,
                market_price      REAL,
                whale_yes_usd     REAL,
                whale_no_usd      REAL,
                whale_consensus   TEXT,
                whale_alignment   TEXT,
                action_taken      TEXT
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_match ON orders_history(match_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_ts    ON orders_history(timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rej_reason  ON rejected_candidates(reason)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rej_ts      ON rejected_candidates(timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_whale_cond  ON whale_comparisons(condition_id)")

        conn.commit()

    _initialized = True


# ---------------------------------------------------------------------------
# Escrituras: Ballenas
# ---------------------------------------------------------------------------
def log_whale_comparison(
    condition_id: str,
    symbol: str,
    bot_side: str,
    bot_fair_prob: float,
    market_price: float,
    whale_yes_usd: float,
    whale_no_usd: float,
    whale_consensus: str,
    whale_alignment: str,
    action_taken: str,
) -> None:
    """Registra la comparación entre el bot y el flujo de ballenas."""
    init_db()
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT INTO whale_comparisons
                    (timestamp, condition_id, symbol, bot_side, bot_fair_prob,
                     market_price, whale_yes_usd, whale_no_usd, whale_consensus,
                     whale_alignment, action_taken)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    condition_id,
                    symbol,
                    bot_side,
                    bot_fair_prob,
                    market_price,
                    whale_yes_usd,
                    whale_no_usd,
                    whale_consensus,
                    whale_alignment,
                    action_taken,
                ),
            )
            conn.commit()
    except Exception:
        log.exception("log_whale_comparison falló para %s", condition_id)


# ---------------------------------------------------------------------------
# Escrituras: órdenes
# ---------------------------------------------------------------------------
def log_order_event(
    match_key: Optional[str] = None,
    action: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    price: Optional[float] = None,
    amount: Optional[float] = None,
    pnl: Optional[float] = None,
    raw_data: Optional[dict] = None,
    **kwargs,
) -> None:
    """Guarda un evento de orden en ``orders_history``.

    Acepta ``pnl`` como parámetro opcional (se persiste en la columna ``pnl``)
    y ``**kwargs`` para compatibilidad con firmas legacy. Aliases soportados:

    - ``order_id`` / ``trade_id`` / ``market_id`` → ``match_key``
    - ``status`` / ``event``                    → ``action``
    """
    # --- Aliases legacy ------------------------------------------------
    if match_key is None:
        match_key = kwargs.pop(
            "order_id", kwargs.pop("trade_id", kwargs.pop("market_id", None))
        )
    if action is None:
        action = kwargs.pop("status", kwargs.pop("event", None))

    init_db()
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT INTO orders_history
                    (timestamp, match_key, action, symbol, side, price,
                     amount, pnl, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    str(match_key) if match_key is not None else "",
                    str(action) if action is not None else "UNKNOWN",
                    symbol,
                    side,
                    price,
                    amount,
                    float(pnl) if pnl is not None else 0.0,
                    json.dumps(raw_data) if raw_data is not None else None,
                ),
            )
            conn.commit()
    except Exception:
        log.exception(
            "log_order_event falló match_key=%s action=%s", match_key, action
        )


def update_trade_pnl(trade_id, pnl: float, action: str = None) -> None:
    """Actualiza el campo ``pnl`` (y opcionalmente ``action``) de un trade.

    - Si ``trade_id`` es ``int``: se busca por ``id`` (PK). Si no hay match,
      se reintenta por ``match_key``.
    - Si ``trade_id`` es ``str``: se busca por ``match_key``.
    - Si se pasa ``action``, se actualiza también la columna ``action``
      con ese estado final (``CLOSED``, ``CANCELED``, ``EXIT``…).

    Cuando ``action`` es ``None`` se mantiene el comportamiento previo:
    sólo se actualiza ``pnl`` en filas con acciones
    ``ENTRY``/``PENDING_CREATED``/``FILL``/``CLOSED``.
    """
    init_db()
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            updated = 0

            # 1) Búsqueda por PK si trade_id es int
            if isinstance(trade_id, int):
                if action is not None:
                    cur = conn.execute(
                        "UPDATE orders_history SET pnl = ?, action = ? WHERE id = ?",
                        (float(pnl), action, trade_id),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE orders_history SET pnl = ? WHERE id = ?",
                        (float(pnl), trade_id),
                    )
                updated = cur.rowcount

            # 2) Fallback por match_key
            if updated == 0:
                if action is not None:
                    conn.execute(
                        """
                        UPDATE orders_history
                        SET pnl = ?, action = ?
                        WHERE match_key = ?
                        """,
                        (float(pnl), action, str(trade_id)),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE orders_history SET pnl = ?
                        WHERE match_key = ?
                          AND action IN ('ENTRY', 'PENDING_CREATED', 'FILL', 'CLOSED')
                        """,
                        (float(pnl), str(trade_id)),
                    )

            conn.commit()
    except Exception:
        log.exception(
            "update_trade_pnl falló trade_id=%s action=%s", trade_id, action
        )


def close_trade(match_key: str, pnl: float, final_action: str = "CLOSED") -> None:
    """Registra el cierre de un trade (evento + actualización de PnL y estado)."""
    log_order_event(match_key=match_key, action=final_action, pnl=pnl)
    update_trade_pnl(match_key, pnl, action=final_action)


# ---------------------------------------------------------------------------
# Escrituras: candidatos rechazados
# ---------------------------------------------------------------------------
def log_rejected_candidate(
    match_key: str,
    reason: str,
    fair_probability: float,
    executable_price: float,
    edge: float,
    raw_data: Optional[dict] = None,
) -> None:
    """Persiste candidatos descartados por filtros de riesgo/edge."""
    init_db()
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT INTO rejected_candidates
                    (timestamp, match_key, reason, fair_probability,
                     executable_price, edge, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    match_key,
                    reason,
                    fair_probability,
                    executable_price,
                    edge,
                    json.dumps(raw_data) if raw_data is not None else None,
                ),
            )
            conn.commit()
    except Exception:
        log.exception(
            "log_rejected_candidate falló match_key=%s reason=%s", match_key, reason
        )


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def is_market_already_traded(market_id, include_closed: bool = True) -> bool:
    """Indica si ``market_id`` ya tiene una posición activa o procesada.

    - ``include_closed=True`` (default): devuelve ``True`` ante **cualquier**
      registro (``ENTRY``/``PENDING_CREATED``/``FILL``/``CLOSED``/``CANCELED``)
      para ese ``match_key``. Interpreta "procesada" de forma amplia.
    - ``include_closed=False``: devuelve ``True`` sólo si hay una posición
      abierta (``ENTRY``/``PENDING_CREATED``/``FILL``) sin un
      ``CLOSED``/``CANCELED``/``EXIT`` posterior.

    En caso de error de BD devuelve ``False`` (fail-open para no bloquear
    operativa; el error queda registrado en el log).
    """
    init_db()
    market_str = str(market_id)
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            if include_closed:
                cur = conn.execute(
                    "SELECT 1 FROM orders_history WHERE match_key = ? LIMIT 1",
                    (market_str,),
                )
                return cur.fetchone() is not None

            # Sólo posiciones abiertas: la última ENTRY/FILL es posterior
            # al último cierre/cancelación (o no existe cierre).
            cur = conn.execute(
                """
                SELECT 1 FROM orders_history
                WHERE match_key = ?
                  AND action IN ('ENTRY', 'PENDING_CREATED', 'FILL')
                  AND id > COALESCE(
                      (SELECT MAX(id) FROM orders_history
                       WHERE match_key = ?
                         AND action IN ('CLOSED', 'CANCELED', 'EXIT')),
                      0
                  )
                LIMIT 1
                """,
                (market_str, market_str),
            )
            return cur.fetchone() is not None
    except Exception:
        log.exception("is_market_already_traded falló market_id=%s", market_id)
        return False