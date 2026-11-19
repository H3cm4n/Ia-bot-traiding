"""
services/order_settlement_resolver.py
Modulo de Liquidación Contable Honesta (Real PnL Engine)
"""
from typing import Dict, Any

def calculate_honest_pnl(entry_price: float, amount_usdc: float, is_win: bool) -> float:
    if entry_price <= 0 or amount_usdc <= 0:
        return 0.0
    
    shares = amount_usdc / entry_price
    if is_win:
        # Pago por contrato a resolución es .00 USD
        pnl = shares * (1.0 - entry_price)
    else:
        # Pérdida total del capital invertido
        pnl = -amount_usdc
    return round(pnl, 4)
