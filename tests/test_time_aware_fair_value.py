import pytest
from services.time_aware_fair_value import (
    calculate_time_to_expiration_hours,
    estimate_time_aware_fair_value
)

def test_time_aware_fair_value_above():
    res = estimate_time_aware_fair_value(65000.0, 64000.0, "2026-10-10T12:00:00Z", 0.03, "ABOVE")
    assert res["status"] == "CALCULATED"
    assert res["fair_probability"] > 0.50

def test_time_aware_fair_value_below():
    res = estimate_time_aware_fair_value(65000.0, 64000.0, "2026-10-10T12:00:00Z", 0.03, "BELOW")
    assert res["fair_probability"] < 0.50

def test_invalid_spot_price():
    res = estimate_time_aware_fair_value(0.0, 64000.0)
    assert res["status"] == "INVALID_INPUT"
    assert res["fair_probability"] == 0.50

def test_expired_market_hours():
    hours = calculate_time_to_expiration_hours("2020-01-01T00:00:00Z")
    assert hours == 0.001
