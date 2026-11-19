"""
services/dashboard_metrics.py
Métricas de solo lectura para el dashboard web/API.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DB_PATH

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "all": None}

CLOSED_ACTIONS = {"WIN", "LOSS", "CLOSED", "EXIT", "CANCELED"}
OPEN_ACTIONS = {"ENTRY", "FILL", "PENDING_CREATED", "PAPER_FILLED", "OPEN"}


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _market_title(row: Dict[str, Any]) -> str:
    raw_payload = row.get("raw_payload")
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            for key in ("market_title", "question", "title", "market"):
                value = payload.get(key)
                if value:
                    return str(value)

    symbol = row.get("symbol")
    if symbol:
        return str(symbol)
    return "Mercado"


def _timeframe(row: Dict[str, Any], parsed_ts: Optional[datetime], now: datetime) -> str:
    raw_payload = row.get("raw_payload")
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            value = payload.get("timeframe") or payload.get("interval")
            if value:
                return str(value)

    if parsed_ts is None:
        return "N/D"

    delta = now - parsed_ts
    total_minutes = max(0, int(delta.total_seconds() // 60))
    if total_minutes < 60:
        return f"{total_minutes}m"

    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        return f"{hours}h {minutes}m"

    days = hours // 24
    remaining_hours = hours % 24
    if days < 7:
        return f"{days}d {remaining_hours}h"

    weeks = days // 7
    return f"{weeks}sem"


def _side_label(side: Any) -> str:
    normalized = str(side or "").upper()
    if normalized in ("YES", "UP", "LONG"):
        return "Posición Alcista"
    if normalized in ("NO", "DOWN", "SHORT"):
        return "Posición Bajista"
    return "Posición"


def _status_label(action: Any) -> str:
    normalized = str(action or "").upper()
    if normalized in CLOSED_ACTIONS:
        return "Concluida"
    return "En Curso"


def _condition_from_match_key(match_key: Any) -> str:
    return str(match_key or "").split("_")[0]


def _latest_whale_alignments(conn: sqlite3.Connection) -> Dict[tuple, str]:
    try:
        rows = conn.execute(
            """
            SELECT condition_id, bot_side, whale_alignment
            FROM whale_comparisons w
            WHERE w.id IN (
                SELECT MAX(id)
                FROM whale_comparisons
                GROUP BY condition_id, bot_side
            )
            """
        ).fetchall()
    except sqlite3.Error:
        return {}

    return {
        (str(condition_id), str(side or "").upper()): str(alignment or "NEUTRAL").upper()
        for condition_id, side, alignment in rows
    }


def _empty_result(window: str) -> Dict[str, Any]:
    return {
        "window": window,
        "net_pnl_usdc": 0.0,
        "win_rate_pct": 0.0,
        "closed_count": 0,
        "total_volume_usdc": 0.0,
        "wins_count": 0,
        "losses_count": 0,
        "gross_profit_usdc": 0.0,
        "gross_loss_usdc": 0.0,
        "operations": [],
    }


def get_performance(
    window: str = "all", db_path: Optional[str | Path] = None
) -> Dict[str, Any]:
    """Calcula métricas y operaciones desde SQLite (solo lectura)."""
    normalized_window = str(window or "all").lower()
    if normalized_window not in WINDOWS:
        raise ValueError(f"window inválida: {window}")

    target = Path(db_path) if db_path else DB_PATH
    if not target.exists():
        return _empty_result(normalized_window)

    now = datetime.now(timezone.utc)
    delta = WINDOWS[normalized_window]

    with sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        whale_alignments = _latest_whale_alignments(conn)
        rows = conn.execute(
            """
            SELECT id, timestamp, match_key, action, symbol, side,
                   price, amount, pnl, raw_payload
            FROM orders_history
            ORDER BY id DESC
            """
        ).fetchall()

    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    net_pnl = 0.0
    total_volume = 0.0
    operations: List[Dict[str, Any]] = []
    sortable_operations = []

    for raw_row in rows:
        row = dict(raw_row)
        parsed_ts = _parse_timestamp(row.get("timestamp"))

        if delta is not None and (parsed_ts is None or parsed_ts < now - delta):
            continue

        action = str(row.get("action") or "").upper()
        pnl = float(row.get("pnl") or 0.0)
        amount = float(row.get("amount") or 0.0)

        total_volume += amount
        if action == "WIN":
            wins += 1
            gross_profit += pnl
            net_pnl += pnl
        elif action == "LOSS":
            losses += 1
            gross_loss += pnl
            net_pnl += pnl

        condition_id = _condition_from_match_key(row.get("match_key"))
        side = str(row.get("side") or "").upper()
        operation = {
            "market_title": _market_title(row),
            "side_label": _side_label(side),
            "timeframe": _timeframe(row, parsed_ts, now),
            "pnl_usdc": round(pnl, 2),
            "stake_usdc": round(amount, 2),
            "status": _status_label(action),
            "whale_alignment": whale_alignments.get(
                (condition_id, side), "NEUTRAL"
            ),
            "timestamp": row.get("timestamp"),
        }
        sortable_operations.append((parsed_ts, int(row.get("id") or 0), operation))

    sortable_operations.sort(
        key=lambda item: (
            item[0] or datetime.min.replace(tzinfo=timezone.utc),
            item[1],
        ),
        reverse=True,
    )
    operations = [item[2] for item in sortable_operations]

    closed_count = wins + losses
    return {
        "window": normalized_window,
        "net_pnl_usdc": round(net_pnl, 2),
        "win_rate_pct": round((wins / closed_count * 100.0) if closed_count else 0.0, 2),
        "closed_count": closed_count,
        "total_volume_usdc": round(total_volume, 2),
        "wins_count": wins,
        "losses_count": losses,
        "gross_profit_usdc": round(gross_profit, 2),
        "gross_loss_usdc": round(gross_loss, 2),
        "operations": operations,
    }
