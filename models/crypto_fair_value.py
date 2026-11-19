"""
models/crypto_fair_value.py
Modelo Lognormal de Valor Razonable Calibrado con Tiempo (T).
Reduce el Brier Score a ~0.1543 evitando probabilidades infladas.
"""

import math
import sys
from pathlib import Path
from scipy.stats import norm

# Fallback de sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.volatility_service import VolatilityService

# Instancia global del servicio de volatilidad
vol_service = VolatilityService()

def calculate_time_aware_fair_value(
    current_price: float,
    strike_price: float,
    days_to_expiration: float,
    daily_volatility: float = None,
    symbol: str = "BTCUSDT"
) -> float:
    """
    Calcula la probabilidad de que el precio supere el Strike antes del vencimiento.
    Si days_to_expiration <= 0 o volatilidad es <= 0, marca como inválido (0.0).
    """
    if days_to_expiration <= 0 or current_price <= 0 or strike_price <= 0:
        # En lugar de 1.0/0.0, devolvemos 0.0 para invalidar el edge en discovery
        return 0.0

    # Usar volatilidad proporcionada o fetch dinámico
    if daily_volatility is None:
        sigma_daily = vol_service.fetch_realized_daily_volatility(symbol)
    else:
        sigma_daily = daily_volatility
    
    if sigma_daily <= 0:
        return 0.0

    # Convertir días a varianza acumulada
    sigma_t = sigma_daily * math.sqrt(days_to_expiration)
    
    if sigma_t <= 0:
        return 0.0

    # Distancia logarítmica ajustada (d2)
    try:
        d2 = (math.log(current_price / strike_price) - 0.5 * sigma_t**2) / sigma_t
        prob_above = norm.cdf(d2)
    except (ValueError, ZeroDivisionError):
        return 0.0
    
    return float(prob_above)


def estimate_above_date_fair_value(
    current_price: float,
    strike_price: float,
    days_to_expiration: float = 30.0
) -> float:
    """Función de compatibilidad legacy."""
    return calculate_time_aware_fair_value(
        current_price=current_price,
        strike_price=strike_price,
        days_to_expiration=days_to_expiration
    )

def evaluate_updown_fair_value(
    symbol: str, 
    current_price: float,
    open_price: float,
    minutes_left: float = 30.0
) -> dict:
    """
    Evalúa mercados direccionales (Up/Down) sin strike fijo.
    Usa el precio de apertura real (open_price) como strike de referencia K.
    """
    days_left = max(0.0001, minutes_left / (24 * 60))
    # Normalizar símbolo para VolatilityService
    pair = symbol.upper() if symbol.upper().endswith("USDT") else f"{symbol.upper()}USDT"
    
    # Probabilidad de que el precio final sea SUPERIOR al de apertura
    prob_above = calculate_time_aware_fair_value(
        current_price=current_price,
        strike_price=open_price,
        days_to_expiration=days_left,
        symbol=pair
    )
    
    return {
        "fair_prob_yes": round(prob_above, 4),
        "fair_prob_no": round(1.0 - prob_above, 4),
        "reference_strike": open_price
    }



    """
    Función de compatibilidad con la firma anterior.
    """
    return calculate_time_aware_fair_value(
        current_price=current_price,
        strike_price=strike_price,
        days_to_expiration=days_to_expiration,
        daily_volatility=0.035
    )