import pytest
from services.paper_broker_realistic import RealisticPaperBroker

def test_orderbook_fill_with_slippage():
    broker = RealisticPaperBroker()
    orderbook = [(0.40, 100.0), (0.45, 100.0)]
    avg_price, total_cost, slippage = broker.execute_fill_through_orderbook(orderbook, 50.0)
    assert total_cost == 50.0
    assert avg_price > 0.40
    assert slippage > 0.0

def test_circuit_breaker_trigger():
    broker = RealisticPaperBroker(1000.0, 100.0)
    broker.balance_usdc = 895.0
    assert broker.check_circuit_breaker() is True
    assert "MAX_DAILY_LOSS_EXCEEDED" in broker.shutdown_reason

def test_stop_loss_executed_at_gap_bid():
    broker = RealisticPaperBroker(1000.0)
    broker.positions["token_123"] = {"entry_price": 0.50, "size_usd": 50.0}
    success, pnl, reason = broker.execute_stop_loss_with_gap("token_123", 0.38)
    assert success is True
    assert pnl == -12.0
    assert "token_123" not in broker.positions
