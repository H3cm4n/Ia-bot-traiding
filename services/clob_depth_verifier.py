"""
services/clob_depth_verifier.py
Módulo de Verificación de Profundidad de Mercado (CLOB Orderbook Depth & Liquidity Analyzer)
Fase 2: Medición de spread, profundidad acumulada, VWAP ejecutable y slippage real.
"""

from typing import List, Dict, Any

class CLOBDepthVerifier:
    def __init__(
        self,
        max_allowed_spread_pct: float = 0.02,
        min_depth_usdc: float = 10.0,
        max_allowed_slippage_pct: float = 0.03,
        max_allowed_spread_usd: float = 0.02,
    ):
        self.max_allowed_spread_pct = max_allowed_spread_pct
        self.min_depth_usdc = min_depth_usdc
        self.max_allowed_slippage_pct = max_allowed_slippage_pct
        self.max_allowed_spread_usd = max_allowed_spread_usd

    def analyze_orderbook(self, asks: List[Dict[str, Any]], bids: List[Dict[str, Any]], target_size_usdc: float = 10.0, side: str = "BUY") -> Dict[str, Any]:
        """
        Analiza el orderbook para verificar spread, liquidez acumulada y calcular el VWAP ejecutable.
        """
        side_upper = side.upper()
        levels = asks if side_upper == "BUY" else bids
        reverse = False if side_upper == "BUY" else True

        if not levels or not isinstance(levels, list):
            return {
                "is_valid": False,
                "rejection_reason": "EMPTY_ORDERBOOK",
                "spread_pct": 1.0,
                "executable_vwap": 0.0,
                "slippage_pct": 1.0,
                "available_depth_usdc": 0.0
            }

        # Parsear niveles válidos
        parsed_levels = []
        for lvl in levels:
            try:
                price = float(lvl.get("price", 0.0))
                size = float(lvl.get("size", 0.0))
                if price > 0 and size > 0:
                    parsed_levels.append((price, size))
            except (ValueError, TypeError):
                continue

        if not parsed_levels:
            return {
                "is_valid": False,
                "rejection_reason": "INVALID_LEVELS_DATA",
                "spread_pct": 1.0,
                "executable_vwap": 0.0,
                "slippage_pct": 1.0,
                "available_depth_usdc": 0.0
            }

        # Ordenar niveles de precios
        parsed_levels.sort(key=lambda x: x[0], reverse=reverse)
        best_price = parsed_levels[0][0]

        # Calcular Spread Bid-Ask si ambos lados están presentes
        best_bid = 0.0
        best_ask = 0.0
        if bids and isinstance(bids, list):
            for b in bids:
                try:
                    p = float(b.get("price", 0.0))
                    if p > best_bid:
                        best_bid = p
                except (ValueError, TypeError):
                    pass

        if asks and isinstance(asks, list):
            asks_prices = []
            for a in asks:
                try:
                    p = float(a.get("price", 0.0))
                    if p > 0:
                        asks_prices.append(p)
                except (ValueError, TypeError):
                    pass
            if asks_prices:
                best_ask = min(asks_prices)

        spread_pct = 0.0
        spread_abs = 0.0
        if best_bid > 0 and best_ask > 0 and best_ask >= best_bid:
            spread_abs = best_ask - best_bid
            mid_price = (best_bid + best_ask) / 2.0
            spread_pct = spread_abs / mid_price if mid_price > 0 else 1.0

        # Calcular VWAP y profundidad acumulada
        accumulated_cost = 0.0
        accumulated_shares = 0.0
        remaining_target_usdc = target_size_usdc
        total_depth_usdc = sum(p * s for p, s in parsed_levels)

        for price, size in parsed_levels:
            level_value_usdc = price * size
            if remaining_target_usdc <= 0:
                break

            fill_usdc = min(remaining_target_usdc, level_value_usdc)
            fill_shares = fill_usdc / price
            accumulated_cost += fill_usdc
            accumulated_shares += fill_shares
            remaining_target_usdc -= fill_usdc

        if accumulated_shares <= 0 or remaining_target_usdc > 0.01:
            return {
                "is_valid": False,
                "rejection_reason": "INSUFFICIENT_DEPTH",
                "spread_pct": spread_pct,
                "executable_vwap": 0.0,
                "slippage_pct": 1.0,
                "available_depth_usdc": total_depth_usdc
            }

        executable_vwap = accumulated_cost / accumulated_shares
        slippage_pct = abs(executable_vwap - best_price) / best_price if best_price > 0 else 0.0

        # Reglas de rechazo por liquidez/spread/slippage
        # Un spread absoluto de hasta $0.02 es aceptable aunque el porcentual
        # sea alto en mercados de precio bajo. El veto porcentual solo aplica
        # cuando además se supera el tope absoluto en USD.
        if (
            spread_pct > self.max_allowed_spread_pct
            and spread_abs > self.max_allowed_spread_usd
        ):
            return {
                "is_valid": False,
                "rejection_reason": f"EXCESSIVE_SPREAD_{spread_pct*100:.2f}%",
                "spread_pct": spread_pct,
                "executable_vwap": executable_vwap,
                "slippage_pct": slippage_pct,
                "available_depth_usdc": total_depth_usdc
            }

        if slippage_pct > self.max_allowed_slippage_pct:
            return {
                "is_valid": False,
                "rejection_reason": f"EXCESSIVE_SLIPPAGE_{slippage_pct*100:.2f}%",
                "spread_pct": spread_pct,
                "executable_vwap": executable_vwap,
                "slippage_pct": slippage_pct,
                "available_depth_usdc": total_depth_usdc
            }

        return {
            "is_valid": True,
            "rejection_reason": None,
            "spread_pct": spread_pct,
            "executable_vwap": round(executable_vwap, 4),
            "slippage_pct": round(slippage_pct, 4),
            "available_depth_usdc": round(total_depth_usdc, 2)
        }
