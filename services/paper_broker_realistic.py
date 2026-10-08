from typing import Dict, Any, List, Tuple

class RealisticPaperBroker:
    def __init__(
        self,
        initial_balance_usdc: float = 1000.0,
        max_daily_loss_usdc: float = 100.0,
        max_position_per_market_usdc: float = 50.0,
        commission_pct: float = 0.00
    ):
        self.balance_usdc = initial_balance_usdc
        self.initial_balance = initial_balance_usdc
        self.max_daily_loss_usdc = max_daily_loss_usdc
        self.max_position_per_market_usdc = max_position_per_market_usdc
        self.commission_pct = commission_pct
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.realized_pnl = 0.0
        self.circuit_breaker_triggered = False
        self.shutdown_reason = ""

    def check_circuit_breaker(self) -> bool:
        daily_loss = self.initial_balance - (self.balance_usdc + self.unrealized_pnl)
        if daily_loss >= self.max_daily_loss_usdc:
            self.circuit_breaker_triggered = True
            self.shutdown_reason = f"MAX_DAILY_LOSS_EXCEEDED (Loss: ${daily_loss:.2f} >= Limit: ${self.max_daily_loss_usdc:.2f})"
            return True
        return False

    @property
    def unrealized_pnl(self) -> float:
        pnl = 0.0
        for pos in self.positions.values():
            pnl += pos.get("current_unrealized_pnl", 0.0)
        return pnl

    def execute_fill_through_orderbook(
        self,
        orderbook_asks: List[Tuple[float, float]],
        requested_size_usd: float
    ) -> Tuple[float, float, float]:
        if not orderbook_asks or requested_size_usd <= 0:
            return 0.0, 0.0, 0.0

        remaining_usd = requested_size_usd
        total_tokens = 0.0
        total_cost_usd = 0.0
        l1_price = orderbook_asks[0][0]

        for price, available_qty in orderbook_asks:
            level_max_usd = price * available_qty
            if remaining_usd <= level_max_usd:
                qty_to_buy = remaining_usd / price
                total_tokens += qty_to_buy
                total_cost_usd += remaining_usd
                remaining_usd = 0.0
                break
            else:
                total_tokens += available_qty
                total_cost_usd += level_max_usd
                remaining_usd -= level_max_usd

        if total_tokens <= 0:
            return 0.0, 0.0, 0.0

        avg_executed_price = total_cost_usd / total_tokens
        slippage_pct = (avg_executed_price - l1_price) / l1_price if l1_price > 0 else 0.0

        return round(avg_executed_price, 4), round(total_cost_usd, 2), round(slippage_pct, 4)

    def execute_stop_loss_with_gap(
        self,
        token_id: str,
        observed_market_bid: float
    ) -> Tuple[bool, float, str]:
        if token_id not in self.positions:
            return False, 0.0, "POSITION_NOT_FOUND"

        pos = self.positions[token_id]
        entry_price = pos["entry_price"]
        size_usd = pos["size_usd"]
        tokens = size_usd / entry_price

        gross_return_usd = tokens * observed_market_bid
        commission = gross_return_usd * self.commission_pct
        net_return_usd = gross_return_usd - commission

        trade_pnl = net_return_usd - size_usd
        self.realized_pnl += trade_pnl
        self.balance_usdc += trade_pnl

        del self.positions[token_id]
        self.check_circuit_breaker()

        return True, round(trade_pnl, 2), f"STOP_LOSS_EXECUTED_AT_BID_${observed_market_bid:.4f}"
