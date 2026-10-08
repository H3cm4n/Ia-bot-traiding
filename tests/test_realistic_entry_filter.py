import pytest
from services.realistic_entry_filter import RealisticEntryFilter

def test_discard_invalid_spot_price():
    entry_filter = RealisticEntryFilter()
    candidate = {"binance_spot_price": 0.0, "fair_probability": 0.70, "best_ask": 0.50}
    passed, reason, metrics = entry_filter.evaluate_entry(candidate)
    assert passed is False
    assert reason == "DISCARDED_INVALID_SPOT_PRICE"

def test_reject_insufficient_edge():
    entry_filter = RealisticEntryFilter(model_error_margin=0.05)
    candidate = {"binance_spot_price": 65000.0, "fair_probability": 0.52, "best_ask": 0.50, "best_bid": 0.48}
    passed, reason, metrics = entry_filter.evaluate_entry(candidate)
    assert passed is False
    assert "REJECTED_EDGE_TOO_LOW" in reason

def test_fractional_kelly_sizing_calculation():
    entry_filter = RealisticEntryFilter(kelly_fraction=0.25, max_position_usdc=50.0)
    sizing = entry_filter.calculate_fractional_kelly_sizing(0.70, 0.50, 1000.0)
    assert sizing == 50.0

def test_passed_realistic_entry():
    entry_filter = RealisticEntryFilter(model_error_margin=0.01)
    candidate = {"binance_spot_price": 65000.0, "fair_probability": 0.75, "best_ask": 0.50, "best_bid": 0.48}
    passed, reason, metrics = entry_filter.evaluate_entry(candidate, 1000.0)
    assert passed is True
    assert reason == "PASSED_REALISTIC_FILTERS"
    assert metrics["size_usd"] > 0
