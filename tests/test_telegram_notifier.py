"""
Pruebas del notificador de Telegram con red simulada.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import services.telegram_notifier as notifier_module
from services.telegram_notifier import TelegramNotifier


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def sent_messages(monkeypatch):
    messages = []

    def _fake_urlopen(req, timeout=5):
        payload = json.loads(req.data.decode("utf-8"))
        messages.append(payload)
        return _FakeResponse()

    monkeypatch.setattr(notifier_module.urllib.request, "urlopen", _fake_urlopen)
    return messages


@pytest.fixture
def notifier():
    return TelegramNotifier(token="test-token", chat_id="test-chat")


@pytest.fixture
def trading_db(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    now = datetime.now(timezone.utc).isoformat()

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
        conn.executemany(
            """
            INSERT INTO orders_history
                (timestamp, match_key, action, symbol, side, price, amount, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (now, "m1", "WIN", "BTC", "NO", 0.29, 15.0, 36.72),
                (now, "m2", "LOSS", "ETH", "YES", 0.50, 10.0, -5.0),
                (now, "m3", "WIN", "BTC", "NO", 0.40, 10.0, 15.0),
                (now, "open", "PENDING_CREATED", "BTC", "NO", 0.30, 15.0, 0.0),
            ],
        )
        conn.commit()

    return db_path


def test_notify_order_placed_includes_required_fields(notifier, sent_messages):
    result = notifier.notify_order_placed(
        {
            "match_key": "0xabcdef1234567890",
            "symbol": "BTC",
            "outcome": "NO",
            "limit_price": 0.29,
            "amount": 15.0,
            "fair_probability": 0.585,
            "whale_alignment": "NEUTRAL",
            "whale_yes_usd": 2800,
            "whale_no_usd": 5868,
            "whale_net_flow_usd": -3068,
        }
    )

    assert result is True
    text = sent_messages[-1]["text"]
    assert "BTC [NO]" in text
    assert "Ask: $0.2900" in text
    assert "Fair: 58.5%" in text
    assert "Size USD: $15.00" in text
    assert "Retorno potencial: +$36.72" in text
    assert "Ballenas: NEUTRAL" in text
    assert "Flujo: NO" in text
    assert "YES $2,800 / NO $5,868" in text


@pytest.mark.parametrize(
    "alignment,net_flow,expected_flow",
    [
        ("ALIGNED", 3000.0, "YES"),
        ("CONFLICT", -6000.0, "NO"),
        ("NEUTRAL", 0.0, "NEUTRAL"),
    ],
)
def test_notify_order_placed_reports_whale_alignment(
    notifier, sent_messages, alignment, net_flow, expected_flow
):
    notifier.notify_order_placed(
        {
            "match_key": "0xwhale",
            "symbol": "ETH",
            "outcome": "YES",
            "price": 0.40,
            "amount": 10.0,
            "fair_probability": 0.60,
            "whale_alignment": alignment,
            "whale_yes_usd": 1000.0,
            "whale_no_usd": 1000.0,
            "whale_net_flow_usd": net_flow,
        }
    )

    text = sent_messages[-1]["text"]
    assert f"Ballenas: {alignment}" in text
    assert f"Flujo: {expected_flow}" in text


def test_notify_trade_settled_includes_pnl_winrate_and_day_pnl(
    notifier, sent_messages, trading_db
):
    result = notifier.notify_trade_settled(
        {"match_key": "m1", "symbol": "BTC", "side": "NO", "action": "WIN", "pnl": 36.72},
        db_path=trading_db,
    )

    assert result is True
    text = sent_messages[-1]["text"]
    assert "LIQUIDACION WIN" in text
    assert "BTC [NO]" in text
    assert "PnL Operacion: $+36.72" in text
    assert "Win Rate acumulado: 66.67% (2W/1L)" in text
    assert "PnL Neto Hoy: $+46.72" in text


def test_notify_trade_settled_loss(notifier, sent_messages, trading_db):
    notifier.notify_trade_settled(
        {"match_key": "m2", "symbol": "ETH", "side": "YES", "action": "LOSS", "pnl": -5.0},
        db_path=trading_db,
    )

    text = sent_messages[-1]["text"]
    assert "LIQUIDACION LOSS" in text
    assert "PnL Operacion: $-5.00" in text
    assert "Win Rate acumulado: 66.67% (2W/1L)" in text


def test_stats_command_summarizes_sqlite(notifier, trading_db):
    text = notifier.handle_command("/stats", db_path=trading_db)

    assert text is not None
    assert "ESTADO DEL BOT" in text
    assert "PnL Neto: $+46.72" in text
    assert "Ganadas vs. Perdidas: 2W / 1L" in text
    assert "Ganancia Bruta: $+51.72" in text
    assert "Perdida Bruta: $5.00" in text
    assert "Win Rate: 66.67%" in text
    assert "Posiciones abiertas: 1" in text
    assert "PnL Neto Hoy: $+46.72" in text


def test_balance_command_alias_and_unknown_command(notifier, trading_db):
    assert "ESTADO DEL BOT" in notifier.handle_command("/balance", db_path=trading_db)
    assert notifier.handle_command("/unknown", db_path=trading_db) is None


def test_send_command_response_sends_stats(notifier, sent_messages, trading_db):
    assert notifier.send_command_response("/balance", db_path=trading_db) is True
    assert "ESTADO DEL BOT" in sent_messages[-1]["text"]


def test_disabled_notifier_does_not_send(monkeypatch):
    monkeypatch.setattr(notifier_module, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(notifier_module, "TELEGRAM_CHAT_ID", "")

    disabled = TelegramNotifier()
    assert disabled.enabled is False
    assert disabled.send_message("test") is False
