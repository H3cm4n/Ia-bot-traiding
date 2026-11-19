"""
Pruebas del resolvedor real de liquidaciones contra Polymarket Gamma API.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

import services.real_settlement_resolver as resolver


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture
def resolver_env(tmp_path, monkeypatch):
    db_path = tmp_path / "trading_bot.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE orders_history (
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
            """
        )
        conn.commit()

    notifications = []

    class _FakeNotifier:
        def notify_trade_settled(self, trade, db_path=None):
            notifications.append((trade, db_path))

    monkeypatch.setattr(resolver, "DB_PATH", str(db_path))
    monkeypatch.setattr(resolver, "TelegramNotifier", _FakeNotifier)
    return db_path, notifications


def _insert_order(
    db_path,
    match_key,
    symbol="BTC",
    side="YES",
    price=0.40,
    amount=10.0,
    action="PENDING_CREATED",
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO orders_history
                (timestamp, match_key, action, symbol, side, price, amount, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0.0)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                match_key,
                action,
                symbol,
                side,
                price,
                amount,
            ),
        )
        conn.commit()


def _fetch_order(db_path, match_key):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT action, pnl, side FROM orders_history WHERE match_key = ?",
            (match_key,),
        ).fetchone()


def _mock_gamma(monkeypatch, payload, status_code=200):
    calls = []

    def _fake_get(url, params=None, timeout=10):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(payload, status_code)

    monkeypatch.setattr(resolver.requests, "get", _fake_get)
    return calls


def test_match_key_helpers_parse_current_format():
    assert resolver._condition_id_from_order("BTC", "0xabc123_NO_1795104520") == "0xabc123"
    assert resolver._side_from_order(None, "0xabc123_NO_1795104520") == "NO"


def test_closed_yes_market_settles_win_with_binary_payout(resolver_env, monkeypatch):
    db_path, notifications = resolver_env
    match_key = "0xcond_YES_1795104520"
    _insert_order(db_path, match_key, side="YES", price=0.40, amount=10.0)

    calls = _mock_gamma(
        monkeypatch,
        [
            {
                "conditionId": "0xcond",
                "closed": True,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }
        ],
    )

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "WIN"
    assert pnl == pytest.approx(15.0)
    assert calls[0]["params"] == {"condition_ids": "0xcond"}
    assert notifications[0][0]["action"] == "WIN"
    assert notifications[0][0]["pnl"] == pytest.approx(15.0)


def test_closed_no_market_with_up_down_labels_settles_loss(resolver_env, monkeypatch):
    db_path, notifications = resolver_env
    match_key = "0xcond_NO_1795104520"
    _insert_order(db_path, match_key, side="NO", price=0.40, amount=10.0)

    _mock_gamma(
        monkeypatch,
        [
            {
                "conditionId": "0xcond",
                "closed": True,
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["1", "0"]',
            }
        ],
    )

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "LOSS"
    assert pnl == pytest.approx(-10.0)
    assert notifications[0][0]["action"] == "LOSS"


def test_open_market_stays_pending(resolver_env, monkeypatch):
    db_path, notifications = resolver_env
    match_key = "0xcond_YES_1795104520"
    _insert_order(db_path, match_key)

    _mock_gamma(
        monkeypatch,
        [
            {
                "conditionId": "0xcond",
                "closed": False,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
            }
        ],
    )

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "PENDING_CREATED"
    assert pnl == pytest.approx(0.0)
    assert notifications == []


def test_closed_market_without_binary_resolution_stays_pending(
    resolver_env, monkeypatch
):
    db_path, notifications = resolver_env
    match_key = "0xcond_YES_1795104520"
    _insert_order(db_path, match_key)

    _mock_gamma(
        monkeypatch,
        [
            {
                "conditionId": "0xcond",
                "closed": True,
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.5", "0.5"],
            }
        ],
    )

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "PENDING_CREATED"
    assert pnl == pytest.approx(0.0)
    assert notifications == []


def test_gamma_error_leaves_order_pending(resolver_env, monkeypatch):
    db_path, notifications = resolver_env
    match_key = "0xcond_YES_1795104520"
    _insert_order(db_path, match_key)

    _mock_gamma(monkeypatch, {"error": "not found"}, status_code=500)

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "PENDING_CREATED"
    assert notifications == []


def test_gamma_response_for_other_market_is_not_used(resolver_env, monkeypatch):
    db_path, notifications = resolver_env
    match_key = "0xcond_YES_1795104520"
    _insert_order(db_path, match_key)

    _mock_gamma(
        monkeypatch,
        [
            {
                "conditionId": "0xother",
                "closed": True,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }
        ],
    )

    resolver.check_and_settle_real_markets()

    action, pnl, _ = _fetch_order(db_path, match_key)
    assert action == "PENDING_CREATED"
    assert notifications == []
