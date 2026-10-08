"""
services/time_aware_fair_value.py
Calculador de Probabilidad Justa con Conciencia Temporal y Volatilidad Dinámica.
Módulo completamente compatible con la suite de tests (incluye 'z_score' y diccionario enriquecido).
"""

import math
from datetime import datetime, timezone

def normal_cdf(x: float) -> float:
    """Calcula la Distribución Normal Acumulada N(x) usando math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_time_to_expiration_hours(end_date_iso: str, current_time: datetime = None) -> float:
    """Calcula las horas restantes hasta el vencimiento en formato ISO 8601."""
    if not end_date_iso:
        return 0.001
    try:
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        
        clean_iso = end_date_iso.replace('Z', '+00:00')
        end_dt = datetime.fromisoformat(clean_iso)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
            
        diff_seconds = (end_dt - current_time).total_seconds()
        hours = diff_seconds / 3600.0
        return max(0.001, hours)
    except Exception:
        return 0.001

class TimeAwareFairValueCalculator:
    def __init__(self, min_volatility: float = 0.25):
        self.min_volatility = min_volatility

    def calculate_fair_probability(
        self,
        spot_price: float,
        strike_price: float,
        time_to_expiry_years: float,
        volatility_annualized: float,
        direction: str = 'ABOVE'
    ) -> tuple[float, float]:
        """Calcula la probabilidad justa p_model y el z_score asociado."""
        if spot_price <= 0 or strike_price <= 0:
            return 0.50, 0.0

        sigma = max(volatility_annualized, self.min_volatility)
        
        if time_to_expiry_years <= 1e-6:
            if direction.upper() == 'ABOVE':
                prob = 1.0 if spot_price >= strike_price else 0.0
            else:
                prob = 1.0 if spot_price <= strike_price else 0.0
            return prob, 0.0

        sqrt_t = math.sqrt(time_to_expiry_years)
        d2 = (math.log(spot_price / strike_price) - 0.5 * (sigma ** 2) * time_to_expiry_years) / (sigma * sqrt_t)
        
        prob_above = normal_cdf(d2)
        
        if direction.upper() == 'BELOW':
            prob = 1.0 - prob_above
            z_val = -d2
        else:
            prob = prob_above
            z_val = d2

        return max(0.01, min(0.99, prob)), float(z_val)

def estimate_time_aware_fair_value(
    spot_price: float,
    threshold_price: float = None,
    end_date_str: str = None,
    daily_volatility: float = 0.03,
    direction: str = 'ABOVE',
    *args,
    **kwargs
) -> dict:
    """
    Adaptador compatible con los test cases. Retorna un diccionario con 'status', 'fair_probability' y 'z_score'.
    """
    if spot_price <= 0:
        return {
            "status": "INVALID_INPUT",
            "fair_probability": 0.50,
            "z_score": 0.0,
            "reason": "Spot price must be > 0"
        }

    strike = threshold_price
    if strike is None and len(args) >= 1:
        strike = args[0]
    if strike is None:
        strike = kwargs.get('strike_price', spot_price)

    time_years = 0.001 / 8760.0
    if end_date_str is not None:
        if isinstance(end_date_str, (int, float)):
            time_years = float(end_date_str)
        elif isinstance(end_date_str, str):
            if 'T' in end_date_str or 'Z' in end_date_str:
                hours = calculate_time_to_expiration_hours(end_date_str)
                time_years = hours / 8760.0
            else:
                try:
                    time_years = float(end_date_str)
                except ValueError:
                    time_years = 0.001 / 8760.0
    elif len(args) >= 2 and isinstance(args[1], (int, float)):
        time_years = float(args[1])

    vol_ann = daily_volatility * math.sqrt(365.0) if daily_volatility < 0.15 else daily_volatility
    if len(args) >= 3 and isinstance(args[2], (int, float)):
        v_arg = float(args[2])
        vol_ann = v_arg * math.sqrt(365.0) if v_arg < 0.15 else v_arg

    if len(args) >= 4 and isinstance(args[3], str):
        direction = args[3]

    calc = TimeAwareFairValueCalculator()
    prob, z_score = calc.calculate_fair_probability(
        spot_price=spot_price,
        strike_price=strike,
        time_to_expiry_years=time_years,
        volatility_annualized=vol_ann,
        direction=direction
    )

    return {
        "status": "CALCULATED",
        "fair_probability": prob,
        "z_score": z_score,
        "spot_price": spot_price,
        "strike_price": strike,
        "direction": direction
    }
