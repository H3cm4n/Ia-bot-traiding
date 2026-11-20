"""
Pruebas del filtro de vencimiento corto y deduplicación por condition_id+side.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import services.risk_manager as risk_module
from config import MIN_MARKET_DURATION_MINUTES
from services.crypto_universe_discovery import (
    CryptoUniverseDiscovery,
    _market_duration_minutes as resolver_duration,
    _slug_duration_minutes,
    _title_window_minutes,
)
from services.risk_manager import RiskManager
from tools.directional_limit_hunter_executor import has_duplicate_open_position


@pytest.fixture
def isolated_risk_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_module, "DB_PATH", tmp_path / "missing.db")
    return RiskManager(initial_capital_usdc=1000.0)


def test_risk_manager_accepts_expiry_inside_1_hour(isolated_risk_manager):
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    for hours in (0.01, 0.5, 1.0):
        end_date = (now + timedelta(hours=hours)).isoformat()
        is_valid, reason = isolated_risk_manager.validate_expiry(end_date, now=now)
        assert is_valid is True, reason
        assert reason == "PASSED"


def test_risk_manager_rejects_expired_and_beyond_1_hour(isolated_risk_manager):
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    expired = (now - timedelta(seconds=1)).isoformat()
    too_far = (now + timedelta(hours=1, seconds=1)).isoformat()

    for end_date in (expired, too_far, "not-an-iso-date", None):
        is_valid, reason = isolated_risk_manager.validate_expiry(end_date, now=now)
        assert is_valid is False
        assert reason == "EXPIRY_OUT_OF_RANGE"


def test_validate_trade_applies_expiry_filter(isolated_risk_manager):
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    too_far = (now + timedelta(hours=2)).isoformat()

    is_valid, reason, size = isolated_risk_manager.validate_trade(
        fair_prob=0.9, price=0.30, end_date_iso=too_far, now=now
    )

    assert is_valid is False
    assert reason == "EXPIRY_OUT_OF_RANGE"
    assert size == 0.0


def test_discovery_queries_short_expiry_window_and_filters_long_markets(monkeypatch):
    now = datetime.now(timezone.utc)
    short_end = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    long_end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = []

    class _FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "markets": [
                        {
                            "conditionId": "0xshort",
                            "question": "Bitcoin Up or Down - short horizon",
                            "endDate": short_end,
                            "outcomePrices": '["0.5", "0.5"]',
                            "clobTokenIds": json.dumps(["yes-short", "no-short"]),
                            "slug": "btc-short",
                        },
                        {
                            "conditionId": "0xlong",
                            "question": "Bitcoin Up or Down - long horizon",
                            "endDate": long_end,
                            "outcomePrices": '["0.5", "0.5"]',
                            "clobTokenIds": json.dumps(["yes-long", "no-long"]),
                            "slug": "btc-long",
                        },
                    ]
                }
            ]

    def _fake_get(url, params=None, timeout=10):
        calls.append(params)
        return _FakeResponse()

    monkeypatch.setattr(
        "services.crypto_universe_discovery.requests.get", _fake_get
    )

    markets = CryptoUniverseDiscovery().discover_updown_markets()

    assert calls
    assert calls[0]["order"] == "endDate"
    assert calls[0]["ascending"] == "true"
    assert calls[0]["end_date_min"].endswith("Z")
    assert calls[0]["end_date_max"].endswith("Z")
    assert [m["condition_id"] for m in markets] == ["0xshort"]


def _create_orders_db(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE orders_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                match_key TEXT NOT NULL,
                action    TEXT NOT NULL,
                symbol    TEXT,
                side      TEXT,
                price     REAL,
                amount    REAL,
                pnl       REAL DEFAULT 0.0
            )
            """
        )
        conn.commit()


def _insert_order(db_path, match_key, action, side):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO orders_history
                (timestamp, match_key, action, symbol, side, price, amount)
            VALUES (?, ?, ?, 'BTC', ?, 0.5, 15.0)
            """,
            (datetime.now(timezone.utc).isoformat(), match_key, action, side),
        )
        conn.commit()


def test_duplicate_open_position_uses_condition_id_and_side(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    _create_orders_db(db_path)
    _insert_order(db_path, "0xcond_YES_123", "PENDING_CREATED", "YES")

    assert has_duplicate_open_position("0xcond", "YES", db_path=db_path) is True
    assert has_duplicate_open_position("0xcond", "NO", db_path=db_path) is False
    assert has_duplicate_open_position("0xother", "YES", db_path=db_path) is False
    assert has_duplicate_open_position("0xcond", "YES", db_path=tmp_path / "nope.db") is False


def test_duplicate_open_position_ignores_settled_or_canceled_orders(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    _create_orders_db(db_path)

    for action in ("WIN", "LOSS", "STALE", "CANCELED", "CLOSED"):
        _insert_order(db_path, f"0xcond_YES_{action}", action, "YES")

    assert has_duplicate_open_position("0xcond", "YES", db_path=db_path) is False

    _insert_order(db_path, "0xcond_YES_999", "OPEN", "YES")
    assert has_duplicate_open_position("0xcond", "YES", db_path=db_path) is True


def test_duplicate_open_position_does_not_match_longer_condition_id(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    _create_orders_db(db_path)
    _insert_order(db_path, "0xabcd_YES_123", "PENDING_CREATED", "YES")

    assert has_duplicate_open_position("0xabc", "YES", db_path=db_path) is False
    assert has_duplicate_open_position("0xabcd", "YES", db_path=db_path) is True


def test_market_duration_detects_five_minutes_in_slug_and_title():
    end_date = datetime(2026, 11, 20, 3, 15, tzinfo=timezone.utc)

    assert resolver_duration(
        {}, "Bitcoin Up or Down - 10:10PM-10:15PM ET", "btc-updown-5m-123", end_date
    ) == pytest.approx(5)
    assert (
        _slug_duration_minutes("btc-updown-5m-123") == 5
    )
    assert (
        _title_window_minutes("Bitcoin Up or Down - 10:10PM-10:15PM ET") == 5
    )


def test_market_duration_accepts_fifteen_minutes_or_more():
    end_date = datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)

    assert MIN_MARKET_DURATION_MINUTES == pytest.approx(15.0)

    assert _slug_duration_minutes("btc-updown-15m-123") == 15
    assert _title_window_minutes("Bitcoin Up or Down - 11:45PM-12:00AM ET") == 15
    assert resolver_duration(
        {}, "Bitcoin Up or Down - 11:00PM-12:00AM ET", "btc-updown-1h-123", end_date
    ) == pytest.approx(60)


def test_discovery_rejects_markets_shorter_than_15_minutes(monkeypatch):
    now = datetime.now(timezone.utc)
    five_end = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fifteen_end = (now + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "markets": [
                        {
                            "conditionId": "0x5m",
                            "question": "Bitcoin Up or Down - 9:50PM-9:55PM ET",
                            "slug": "btc-updown-5m-123",
                            "endDate": five_end,
                            "outcomePrices": '["0.5", "0.5"]',
                            "clobTokenIds": json.dumps(["yes-5m", "no-5m"]),
                        },
                        {
                            "conditionId": "0x15m",
                            "question": "Bitcoin Up or Down - 9:45PM-10:00PM ET",
                            "slug": "btc-updown-15m-123",
                            "endDate": fifteen_end,
                            "outcomePrices": '["0.5", "0.5"]',
                            "clobTokenIds": json.dumps(["yes-15m", "no-15m"]),
                        },
                    ]
                }
            ]

    monkeypatch.setattr(
        "services.crypto_universe_discovery.requests.get",
        lambda *args, **kwargs: _FakeResponse(),
    )

    markets = CryptoUniverseDiscovery().discover_updown_markets()

    assert [m["condition_id"] for m in markets] == ["0x15m"]
