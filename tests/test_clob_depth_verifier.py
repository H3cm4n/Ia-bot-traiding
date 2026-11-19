import pytest
from services.clob_depth_verifier import CLOBDepthVerifier

def test_clob_depth_verifier_valid():
    verifier = CLOBDepthVerifier(max_allowed_spread_pct=0.05, max_allowed_slippage_pct=0.05)
    asks = [{"price": "0.25", "size": "50"}, {"price": "0.26", "size": "100"}]
    bids = [{"price": "0.24", "size": "50"}]
    res = verifier.analyze_orderbook(asks, bids, target_size_usdc=10.0, side="BUY")
    
    assert res["is_valid"] is True
    assert res["executable_vwap"] == 0.25
    assert res["rejection_reason"] is None

def test_clob_depth_verifier_insufficient_depth():
    verifier = CLOBDepthVerifier()
    asks = [{"price": "0.25", "size": "10"}]  # Solo .5 USDC disponibles
    bids = [{"price": "0.24", "size": "10"}]
    res = verifier.analyze_orderbook(asks, bids, target_size_usdc=10.0, side="BUY")
    
    assert res["is_valid"] is False
    assert res["rejection_reason"] == "INSUFFICIENT_DEPTH"

def test_clob_depth_verifier_empty():
    verifier = CLOBDepthVerifier()
    res = verifier.analyze_orderbook([], [], target_size_usdc=10.0, side="BUY")
    
    assert res["is_valid"] is False
    assert res["rejection_reason"] == "EMPTY_ORDERBOOK"


def test_accepts_absolute_spread_up_to_two_cents_even_if_pct_exceeds_threshold():
    verifier = CLOBDepthVerifier(max_allowed_spread_pct=0.02, max_allowed_spread_usd=0.02)
    asks = [{"price": "0.30", "size": "100"}]
    bids = [{"price": "0.29", "size": "100"}]
    res = verifier.analyze_orderbook(asks, bids, target_size_usdc=10.0, side="BUY")

    assert res["is_valid"] is True
    assert res["rejection_reason"] is None
    assert res["spread_pct"] > verifier.max_allowed_spread_pct


def test_rejects_spread_only_when_pct_and_abs_exceed_thresholds():
    verifier = CLOBDepthVerifier(max_allowed_spread_pct=0.02, max_allowed_spread_usd=0.02)
    asks = [{"price": "0.25", "size": "100"}]
    bids = [{"price": "0.20", "size": "100"}]
    res = verifier.analyze_orderbook(asks, bids, target_size_usdc=10.0, side="BUY")

    assert res["is_valid"] is False
    assert res["rejection_reason"].startswith("EXCESSIVE_SPREAD_")


def test_accepts_wide_absolute_spread_when_pct_below_threshold():
    verifier = CLOBDepthVerifier(max_allowed_spread_pct=0.05, max_allowed_spread_usd=0.02)
    asks = [{"price": "0.91", "size": "100"}]
    bids = [{"price": "0.88", "size": "100"}]
    res = verifier.analyze_orderbook(asks, bids, target_size_usdc=10.0, side="BUY")

    assert res["is_valid"] is True
    assert res["rejection_reason"] is None
    assert res["spread_pct"] <= verifier.max_allowed_spread_pct
