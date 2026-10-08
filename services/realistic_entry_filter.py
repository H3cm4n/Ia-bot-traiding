import math
from typing import Dict, Any, Tuple

class RealisticEntryFilter:
    def __init__(
        self,
        base_commission_pct: float = 0.0,
        model_error_margin: float = 0.02,
        kelly_fraction: float = 0.25,
        max_position_usdc: float = 50.0
    ):
        self.base_commission_pct = base_commission_pct
        self.model_error_margin = model_error_margin
        self.kelly_fraction = kelly_fraction
        self.max_position_usdc = max_position_usdc

    def calculate_minimum_required_edge(self, ask_price: float, bid_price: float) -> float:
        if ask_price <= 0 or bid_price <= 0 or ask_price <= bid_price:
            spread = 0.02
        else:
            spread = ask_price - bid_price
        return round(spread + self.base_commission_pct + self.model_error_margin, 4)

    def calculate_fractional_kelly_sizing(
        self,
        model_prob: float,
        ask_price: float,
        portfolio_balance_usd: float
    ) -> float:
        if model_prob <= 0 or ask_price <= 0 or ask_price >= 1.0 or portfolio_balance_usd <= 0:
            return 0.0

        p = model_prob
        q = 1.0 - p
        b = (1.0 - ask_price) / ask_price

        kelly_f = (p * b - q) / b
        if kelly_f <= 0:
            return 0.0

        fractional_f = kelly_f * self.kelly_fraction
        raw_sizing_usd = portfolio_balance_usd * fractional_f
        return round(max(0.0, min(raw_sizing_usd, self.max_position_usdc)), 2)

    def evaluate_entry(
        self,
        candidate: Dict[str, Any],
        portfolio_balance_usd: float = 1000.0
    ) -> Tuple[bool, str, Dict[str, Any]]:
        spot_price = float(candidate.get("binance_spot_price", 0.0))
        fair_prob = float(candidate.get("fair_probability", 0.50))
        ask_price = float(candidate.get("best_ask", 0.0))
        bid_price = float(candidate.get("best_bid", 0.0))

        if spot_price <= 0:
            return False, "DISCARDED_INVALID_SPOT_PRICE", {"size_usd": 0.0}

        if ask_price <= 0 or ask_price >= 1.0:
            return False, "DISCARDED_INCOMPLETE_ORDERBOOK", {"size_usd": 0.0}

        raw_edge = fair_prob - ask_price
        min_required_edge = self.calculate_minimum_required_edge(ask_price, bid_price)

        if raw_edge < min_required_edge:
            return False, f"REJECTED_EDGE_TOO_LOW (Raw: {raw_edge:.4f} < Min: {min_required_edge:.4f})", {"size_usd": 0.0}

        sizing_usd = self.calculate_fractional_kelly_sizing(fair_prob, ask_price, portfolio_balance_usd)
        if sizing_usd <= 0:
            return False, "REJECTED_KELLY_SIZING_ZERO", {"size_usd": 0.0}

        return True, "PASSED_REALISTIC_FILTERS", {
            "size_usd": sizing_usd,
            "raw_edge": round(raw_edge, 4),
            "min_required_edge": min_required_edge
        }
