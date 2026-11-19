"""
Pruebas de riesgo asimétrico y seguimiento de ballenas (v3.3).
"""

import time

import pytest

import services.risk_manager as risk_module
from services.risk_manager import RiskManager
from services.whale_tracker import (
    MAX_VALID_WHALE_PRICE,
    MIN_VALID_WHALE_PRICE,
    WhaleTracker,
)
from tools.directional_limit_hunter_executor import decide_whale_adjustment


@pytest.fixture
def isolated_risk_manager(tmp_path, monkeypatch):
    """Aísla RiskManager de la base de datos real y fija MAX_RISK_USD=15."""
    monkeypatch.setattr(risk_module, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(risk_module, "MAX_RISK_USD", 15.0)
    return RiskManager(initial_capital_usdc=1000.0)


@pytest.mark.parametrize("price", [0.0, 0.05, 0.08, 0.119, 0.581, 0.60, 0.75, 1.0])
def test_risk_manager_rejects_price_outside_asymmetric_range(isolated_risk_manager, price):
    is_valid, reason, size = isolated_risk_manager.validate_trade(fair_prob=0.9, price=price)

    assert is_valid is False
    assert reason == "PRICE_OUT_OF_ASYMMETRIC_RANGE"
    assert size == 0.0


def test_risk_manager_accepts_price_at_range_boundaries(isolated_risk_manager):
    for price in (0.12, 0.58):
        is_valid, reason, size = isolated_risk_manager.validate_trade(fair_prob=0.9, price=price)
        assert is_valid is True, reason
        assert size <= risk_module.MAX_RISK_USD


def test_risk_manager_caps_size_at_max_risk_usd(isolated_risk_manager):
    size = isolated_risk_manager.calculate_fractional_kelly_size(fair_prob=0.6, price=0.30)

    assert risk_module.MAX_RISK_USD == 15.0
    assert size == pytest.approx(15.0)
    assert size <= risk_module.MAX_RISK_USD


def test_risk_manager_risk_based_size_is_max_risk_usd(isolated_risk_manager):
    assert isolated_risk_manager.calculate_risk_based_size(0.30) == pytest.approx(15.0)
    assert isolated_risk_manager.calculate_risk_based_size(0.0) == 0.0


def test_whale_tracker_extracts_v2_data_payload():
    tracker = WhaleTracker()

    assert tracker._extract_list({"data": [{"id": 1}, {"id": 2}, "invalid"]}) == [
        {"id": 1},
        {"id": 2},
    ]
    assert tracker._extract_list([{"id": 3}]) == [{"id": 3}]
    assert tracker._extract_list({"error": "no data"}) == []


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_whale_tracker_fetches_v2_trades(monkeypatch):
    tracker = WhaleTracker()
    now_ms = int(time.time() * 1000)
    payload = {
        "data": [
            {
                "conditionId": "cond-v2",
                "proxyWallet": "0xabc123",
                "outcome": "Yes",
                "side": "BUY",
                "price": "0.50",
                "size": "12000",
                "timestamp": now_ms,
                "transactionHash": "0xtx1",
            }
        ]
    }

    monkeypatch.setattr(tracker, "_fetch_top_wallets", lambda: None)
    monkeypatch.setattr(
        "services.whale_tracker.requests.get",
        lambda *args, **kwargs: _FakeResponse(payload),
    )

    result = tracker.get_whale_activity("cond-v2")

    assert result["whale_yes_usd"] == pytest.approx(6000.0)
    assert result["whale_no_usd"] == pytest.approx(0.0)
    assert result["whale_consensus"] == "YES"
    assert len(tracker.cached_trades) == 1


def _trade(condition_id, outcome, notional, tx_suffix, side="BUY", price=0.5):
    return {
        "conditionId": condition_id,
        "proxyWallet": f"0xwallet{tx_suffix}",
        "outcome": outcome,
        "side": side,
        "price": str(price),
        "size": str(notional / price),
        "timestamp": int(time.time() * 1000),
        "transactionHash": f"0xtx{tx_suffix}",
    }


@pytest.fixture
def whale_tracker(monkeypatch):
    tracker = WhaleTracker()
    monkeypatch.setattr(tracker, "_fetch_top_wallets", lambda: None)
    monkeypatch.setattr(tracker, "_fetch_recent_trades", lambda: None)
    return tracker


@pytest.mark.parametrize(
    "trades,expected",
    [
        ([_trade("c1", "YES", 6000, "a")], "YES"),
        ([_trade("c1", "NO", 6000, "b")], "NO"),
        ([_trade("c1", "YES", 3000, "c"), _trade("c1", "NO", 3000, "d")], "NEUTRAL"),
    ],
)
def test_whale_tracker_consensus(whale_tracker, trades, expected):
    whale_tracker.cached_trades = trades

    result = whale_tracker.get_whale_activity("c1")

    assert result["whale_consensus"] == expected


@pytest.mark.parametrize("price", [0.05, 0.079, 0.851, 0.95])
def test_whale_tracker_ignores_extreme_prices(whale_tracker, price):
    whale_tracker.cached_trades = [
        _trade("c1", "YES", 6000, "valid"),
        _trade("c1", "YES", 6000, "noise", price=price),
    ]

    result = whale_tracker.get_whale_activity("c1")

    assert result["whale_yes_usd"] == pytest.approx(6000.0)


@pytest.mark.parametrize("price", [MIN_VALID_WHALE_PRICE, MAX_VALID_WHALE_PRICE])
def test_whale_tracker_keeps_boundary_prices(whale_tracker, price):
    whale_tracker.cached_trades = [_trade("c1", "YES", 6000, "boundary", price=price)]

    result = whale_tracker.get_whale_activity("c1")

    assert result["whale_yes_usd"] == pytest.approx(6000.0)


def test_whale_aligned_flow_boosts_size_1_25x():
    tracker = WhaleTracker()

    result = tracker.evaluate_whale_alignment(
        side="YES", net_flow_usd=3000.0, size_usd=10.0, max_risk_usd=15.0
    )

    assert result["whale_alignment"] == "ALIGNED"
    assert result["action_taken"] == "BOOSTED_SIZE"
    assert result["rejected"] is False
    assert result["size_usd"] == pytest.approx(12.5)


def test_whale_aligned_flow_uses_no_side_direction():
    tracker = WhaleTracker()

    result = tracker.evaluate_whale_alignment(
        side="NO", net_flow_usd=-3000.0, size_usd=10.0, max_risk_usd=15.0
    )

    assert result["whale_alignment"] == "ALIGNED"
    assert result["size_usd"] == pytest.approx(12.5)


def test_whale_boost_is_capped_at_max_risk():
    tracker = WhaleTracker()

    result = tracker.evaluate_whale_alignment(
        side="YES", net_flow_usd=5000.0, size_usd=20.0, max_risk_usd=15.0
    )

    assert result["size_usd"] == pytest.approx(18.75)


def test_whale_moderate_conflict_halves_size():
    tracker = WhaleTracker()

    result = tracker.evaluate_whale_alignment(
        side="YES", net_flow_usd=-6000.0, size_usd=10.0, max_risk_usd=15.0
    )

    assert result["whale_alignment"] == "CONFLICT"
    assert result["action_taken"] == "REDUCED_SIZE"
    assert result["rejected"] is False
    assert result["size_usd"] == pytest.approx(5.0)


def test_whale_strong_opposition_vetoes_trade():
    tracker = WhaleTracker()

    result = tracker.evaluate_whale_alignment(
        side="YES", net_flow_usd=-11000.0, size_usd=10.0, max_risk_usd=15.0
    )

    assert result["whale_alignment"] == "REJECTED: WHALE_STRONG_OPPOSITION"
    assert result["action_taken"] == "REJECTED: WHALE_STRONG_OPPOSITION"
    assert result["rejected"] is True
    assert result["size_usd"] == 0.0


def test_aligned_boost_requires_condition_id_flow():
    tracker = WhaleTracker()

    result, source = decide_whale_adjustment(
        tracker,
        side="YES",
        condition_net_flow_usd=0.0,
        asset_net_flow_usd=3000.0,
        size_usd=10.0,
        max_risk_usd=15.0,
    )

    assert source == "asset"
    assert result["whale_alignment"] == "NEUTRAL"
    assert result["action_taken"] == "PROCEED"
    assert result["size_usd"] == pytest.approx(10.0)


def test_aligned_boost_uses_exact_condition_id_flow():
    tracker = WhaleTracker()

    result, source = decide_whale_adjustment(
        tracker,
        side="YES",
        condition_net_flow_usd=3000.0,
        asset_net_flow_usd=0.0,
        size_usd=10.0,
        max_risk_usd=15.0,
    )

    assert source == "condition"
    assert result["whale_alignment"] == "ALIGNED"
    assert result["action_taken"] == "BOOSTED_SIZE"
    assert result["size_usd"] == pytest.approx(12.5)


def test_asset_moderate_conflict_still_reduces_size():
    tracker = WhaleTracker()

    result, source = decide_whale_adjustment(
        tracker,
        side="YES",
        condition_net_flow_usd=0.0,
        asset_net_flow_usd=-6000.0,
        size_usd=10.0,
        max_risk_usd=15.0,
    )

    assert source == "asset"
    assert result["whale_alignment"] == "CONFLICT"
    assert result["action_taken"] == "REDUCED_SIZE"
    assert result["size_usd"] == pytest.approx(5.0)


def test_asset_strong_opposition_still_vetoes_condition_alignment():
    tracker = WhaleTracker()

    result, source = decide_whale_adjustment(
        tracker,
        side="YES",
        condition_net_flow_usd=3000.0,
        asset_net_flow_usd=-11000.0,
        size_usd=10.0,
        max_risk_usd=15.0,
    )

    assert source == "asset"
    assert result["rejected"] is True
    assert result["whale_alignment"] == "REJECTED: WHALE_STRONG_OPPOSITION"
    assert result["size_usd"] == 0.0
