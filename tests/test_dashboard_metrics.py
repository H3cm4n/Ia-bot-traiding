"""
Pruebas de métricas y servidor HTTP del dashboard.
"""

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from services.dashboard_metrics import get_performance
from web.dashboard_app import create_server


@pytest.fixture
def trading_db(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    now = datetime.now(timezone.utc)

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
        conn.execute(
            """
            CREATE TABLE whale_comparisons (
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
            """
        )

        rows = [
            (
                (now - timedelta(days=10)).isoformat(),
                "cond-btc_NO_1",
                "WIN",
                "BTC",
                "NO",
                0.50,
                10.0,
                5.0,
                json.dumps({"question": "BTC above 100k?"}),
            ),
            (
                (now - timedelta(days=2)).isoformat(),
                "cond-eth_YES_2",
                "LOSS",
                "ETH",
                "YES",
                0.50,
                20.0,
                -10.0,
                json.dumps({"question": "ETH above 4k?"}),
            ),
            (
                (now - timedelta(hours=2)).isoformat(),
                "cond-sol_YES_3",
                "WIN",
                "SOL",
                "YES",
                0.40,
                12.0,
                6.0,
                json.dumps({"question": "SOL above 200?", "timeframe": "1h"}),
            ),
            (
                (now - timedelta(hours=1)).isoformat(),
                "cond-btc_NO_4",
                "PENDING_CREATED",
                "BTC",
                "NO",
                0.30,
                15.0,
                0.0,
                None,
            ),
            (
                (now - timedelta(minutes=30)).isoformat(),
                "cond-btc_NO_5",
                "LOSS",
                "BTC",
                "NO",
                0.50,
                5.0,
                -2.5,
                None,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO orders_history
                (timestamp, match_key, action, symbol, side, price, amount, pnl, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        whale_rows = [
            ("cond-btc", "NO", "ALIGNED"),
            ("cond-eth", "YES", "CONFLICT"),
            ("cond-sol", "YES", "NEUTRAL"),
        ]
        conn.executemany(
            """
            INSERT INTO whale_comparisons
                (timestamp, condition_id, bot_side, whale_alignment)
            VALUES (?, ?, ?, ?)
            """,
            [(now.isoformat(), condition, side, alignment) for condition, side, alignment in whale_rows],
        )
        conn.commit()

    return db_path


def test_performance_all_window(trading_db):
    result = get_performance("all", db_path=trading_db)

    assert result["window"] == "all"
    assert result["wins_count"] == 2
    assert result["losses_count"] == 2
    assert result["closed_count"] == 4
    assert result["net_pnl_usdc"] == pytest.approx(-1.5)
    assert result["win_rate_pct"] == pytest.approx(50.0)
    assert result["gross_profit_usdc"] == pytest.approx(11.0)
    assert result["gross_loss_usdc"] == pytest.approx(-12.5)
    assert result["total_volume_usdc"] == pytest.approx(62.0)
    assert len(result["operations"]) == 5


def test_performance_24h_window(trading_db):
    result = get_performance("24h", db_path=trading_db)

    assert result["wins_count"] == 1
    assert result["losses_count"] == 1
    assert result["closed_count"] == 2
    assert result["net_pnl_usdc"] == pytest.approx(3.5)
    assert result["win_rate_pct"] == pytest.approx(50.0)
    assert result["gross_profit_usdc"] == pytest.approx(6.0)
    assert result["gross_loss_usdc"] == pytest.approx(-2.5)
    assert result["total_volume_usdc"] == pytest.approx(32.0)
    assert len(result["operations"]) == 3


def test_performance_7d_window(trading_db):
    result = get_performance("7d", db_path=trading_db)

    assert result["wins_count"] == 1
    assert result["losses_count"] == 2
    assert result["closed_count"] == 3
    assert result["net_pnl_usdc"] == pytest.approx(-6.5)
    assert result["win_rate_pct"] == pytest.approx(33.33)
    assert len(result["operations"]) == 4


def test_operations_are_sorted_and_mapped(trading_db):
    operations = get_performance("all", db_path=trading_db)["operations"]

    assert [op["market_title"] for op in operations[:3]] == ["BTC", "BTC", "SOL above 200?"]

    open_op = operations[1]
    assert open_op["status"] == "En Curso"
    assert open_op["side_label"] == "Posición Bajista"
    assert open_op["stake_usdc"] == pytest.approx(15.0)
    assert open_op["pnl_usdc"] == pytest.approx(0.0)
    assert open_op["whale_alignment"] == "ALIGNED"

    sol_op = operations[2]
    assert sol_op["side_label"] == "Posición Alcista"
    assert sol_op["status"] == "Concluida"
    assert sol_op["timeframe"] == "1h"
    assert sol_op["whale_alignment"] == "NEUTRAL"
    assert sol_op["pnl_usdc"] == pytest.approx(6.0)


def test_invalid_window_raises_value_error(trading_db):
    with pytest.raises(ValueError):
        get_performance("30d", db_path=trading_db)


@pytest.fixture
def dashboard_server(trading_db):
    server = create_server("127.0.0.1", 0, db_path=trading_db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    yield base_url

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_server_api_performance_returns_200(dashboard_server):
    with urllib.request.urlopen(
        f"{dashboard_server}/api/performance?window=all", timeout=5
    ) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))

    assert payload["closed_count"] == 4
    assert payload["operations"][0]["status"] in ("Concluida", "En Curso")


def test_server_root_returns_dashboard_html(dashboard_server):
    with urllib.request.urlopen(f"{dashboard_server}/", timeout=5) as response:
        assert response.status == 200
        html = response.read().decode("utf-8")

    assert "Panel de Rendimiento" in html
    assert "/api/performance" in html
    assert "24 Horas" in html
    assert "Histórico Total" in html


def test_server_invalid_window_returns_400(dashboard_server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(
            f"{dashboard_server}/api/performance?window=bad", timeout=5
        )

    assert exc_info.value.code == 400
