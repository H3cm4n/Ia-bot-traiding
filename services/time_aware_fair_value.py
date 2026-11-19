"""
services/time_aware_fair_value.py
Calculador de Probabilidad Justa con Conciencia Temporal y Volatilidad Dinámica.

Versión calibrada:
  - Min volatilidad subida a 0.35 (antes 0.25).
  - Umbral de expiración a 5 segundos (time_to_expiry_years <= 1.5e-7).
  - Clipping de probabilidad a [0.01, 0.99] para evitar sesgos extremos.
  - `calculate_time_to_expiration_years` devuelve años (no horas) y 0.0 en
    expiración o entradas inválidas.

Incluye una sección final de shims de compatibilidad para que
`models/predictive_filter.py` (que importa `calculate_time_to_expiration_hours`
y `estimate_time_aware_fair_value`) siga funcionando sin cambios. Si migras
ese consumidor a la nueva API, puedes borrar el bloque "Shims" completo.
"""

import math
from datetime import datetime, timezone


# ---------------------------------------------------------------------- #
# Núcleo calibrado
# ---------------------------------------------------------------------- #
def normal_cdf(x: float) -> float:
    """Calcula la Distribución Normal Acumulada N(x) usando math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def calculate_time_to_expiration_years(
    end_date_iso: str, current_time: datetime = None
) -> float:
    """Calcula los años restantes (T) hasta la expiración ISO 8601."""
    if not end_date_iso:
        return 0.0
    try:
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        clean_iso = end_date_iso.replace("Z", "+00:00")
        end_dt = datetime.fromisoformat(clean_iso)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        diff_seconds = (end_dt - current_time).total_seconds()
        if diff_seconds <= 0:
            return 0.0
        return diff_seconds / 31_536_000.0  # Convertir segundos a años
    except Exception:
        return 0.0


class TimeAwareFairValueCalculator:
    def __init__(self, min_volatility: float = 0.35):
        self.min_volatility = min_volatility

    def calculate_fair_probability(
        self,
        spot_price: float,
        strike_price: float,
        time_to_expiry_years: float,
        volatility_annualized: float = 0.50,
    ) -> float:
        if spot_price <= 0 or strike_price <= 0:
            return 0.50

        sigma = max(volatility_annualized, self.min_volatility)

        # Si expiró o le quedan menos de 5 segundos
        if time_to_expiry_years <= 1.5e-7:
            return 1.0 if spot_price >= strike_price else 0.0

        sqrt_t = math.sqrt(time_to_expiry_years)
        d2 = (
            math.log(spot_price / strike_price)
            - 0.5 * (sigma ** 2) * time_to_expiry_years
        ) / (sigma * sqrt_t)

        prob = normal_cdf(d2)
        # Acotar probabilidad entre 0.01 y 0.99 para evitar sesgos extremos
        return max(0.01, min(0.99, prob))


# ---------------------------------------------------------------------- #
# Shims de compatibilidad (consumidores legacy: models/predictive_filter.py)
# ---------------------------------------------------------------------- #
def calculate_time_to_expiration_hours(
    end_date_iso: str, current_time: datetime = None
) -> float:
    """
    Alias en horas sobre `calculate_time_to_expiration_years`.
    Mantiene el contrato anterior (horas) para no romper llamadas existentes.
    Devuelve 0.0 si la expiración ya pasó o la entrada es inválida.
    """
    return calculate_time_to_expiration_years(end_date_iso, current_time) * 8760.0


def estimate_time_aware_fair_value(
    spot_price: float,
    threshold_price: float = None,
    end_date_str: str = None,
    daily_volatility: float = None,
    *args,
    **kwargs,
) -> dict:
    """
    Shim de compatibilidad.

    Acepta la firma nueva (`threshold_price`, `end_date_str`, `daily_volatility`)
    y la antigua (`strike_price`, `time_to_expiry_years`, `volatility`) sin
    romper a ningún consumidor. Devuelve un `dict` con `fair_probability`
    y `z_score`, que `models/predictive_filter.py` ya sabe desempaquetar.
    """
    # --- Resolver strike (K) ---
    strike = threshold_price
    if strike is None:
        strike = kwargs.get("strike_price", kwargs.get("K"))
    if strike is None and len(args) >= 1:
        strike = args[0]
    if strike is None:
        strike = spot_price  # Neutral si no hay referencia
    strike = float(strike)

    # --- Resolver T (años) ---
    # Prioridad: kwarg explícito 'time_to_expiry_years' > end_date_str > args > default 5s
    t = None
    if kwargs.get("time_to_expiry_years") is not None:
        try:
            t = float(kwargs["time_to_expiry_years"])
        except (TypeError, ValueError):
            t = None

    if t is None and end_date_str is not None:
        if isinstance(end_date_str, (int, float)):
            t = float(end_date_str)
        else:
            t = calculate_time_to_expiration_years(str(end_date_str))

    if t is None and len(args) >= 2 and isinstance(args[1], (int, float)):
        t = float(args[1])

    if t is None:
        t = 1.5e-7  # 5 segundos por defecto

    # --- Resolver volatilidad anualizada ---
    if kwargs.get("volatility") is not None:
        try:
            vol_ann = float(kwargs["volatility"])
        except (TypeError, ValueError):
            vol_ann = 0.50
    elif daily_volatility is not None:
        dv = float(daily_volatility)
        vol_ann = dv * math.sqrt(365.0) if dv < 0.15 else dv
    else:
        vol_ann = 0.50

    # --- Cálculo ---
    calc = TimeAwareFairValueCalculator()
    prob = calc.calculate_fair_probability(
        spot_price=float(spot_price),
        strike_price=strike,
        time_to_expiry_years=float(t),
        volatility_annualized=float(vol_ann),
    )

    # z_score derivado de d2 (mismo numerador/denominador que el cálculo de p)
    z_score = 0.0
    if spot_price > 0 and strike > 0 and t > 1.5e-7:
        sigma = max(vol_ann, calc.min_volatility)
        try:
            z_score = (
                math.log(float(spot_price) / strike)
                - 0.5 * (sigma ** 2) * float(t)
            ) / (sigma * math.sqrt(float(t)))
        except (ValueError, ZeroDivisionError):
            z_score = 0.0

    return {
        "status": "CALCULATED",
        "fair_probability": float(prob),
        "z_score": float(z_score),
        "spot_price": float(spot_price),
        "strike_price": strike,
        "time_to_expiry_years": float(t),
        "volatility_annualized": float(vol_ann),
    }