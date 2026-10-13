import pytest
from models.predictive_filter import calculate_dynamic_ev, evaluate_candidate

def test_dynamic_ev_calculation():
    # Probabilidad 70%, precio 50%, costos 1.5% -> Edge neto = +18.5%
    ev = calculate_dynamic_ev(fair_prob=0.70, executable_price=0.50, spread=0.01, fee=0.0, slippage=0.005)
    assert ev == 0.185

    # Probabilidad 50%, precio 55%, costos 1.5% -> Edge neto = -6.5%
    ev_neg = calculate_dynamic_ev(fair_prob=0.50, executable_price=0.55, spread=0.01, fee=0.0, slippage=0.005)
    assert ev_neg == -0.065

def test_evaluate_candidate_dynamic_filter():
    candidate_good = {
        "condition_id": "0x_test_good",
        "limit_price": 0.40,
        "binance_spot_price": 68000.0,
        "target_strike": 65000.0,
        "end_date": "2026-10-20T12:00:00Z"
    }
    passed, reason, metrics = evaluate_candidate(candidate_good)
    assert passed is True
    assert metrics["ev_net"] > 0.02
