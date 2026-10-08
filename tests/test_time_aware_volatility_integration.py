import pytest
from unittest.mock import patch, MagicMock
from services.volatility_service import VolatilityService
from services.time_aware_fair_value import estimate_time_aware_fair_value


def test_volatility_integration_dynamic_adjustment():
    """
    Verifica que el aumento de volatilidad modere las probabilidades extremas.
    """
    vs = VolatilityService()
    
    # 1. Escenario con volatilidad baja (0.015 = 1.5%)
    res_low_vol = estimate_time_aware_fair_value(
        spot_price=66000.0,
        threshold_price=64000.0,
        end_date_str="2026-10-10T12:00:00Z",
        daily_volatility=0.015,
        direction="ABOVE"
    )

    # 2. Escenario con volatilidad alta (0.060 = 6.0%)
    res_high_vol = estimate_time_aware_fair_value(
        spot_price=66000.0,
        threshold_price=64000.0,
        end_date_str="2026-10-10T12:00:00Z",
        daily_volatility=0.060,
        direction="ABOVE"
    )

    # A mayor volatilidad (mayor incertidumbre), la probabilidad debe ser más conservadora (más cercana al 50%)
    assert res_low_vol["fair_probability"] > res_high_vol["fair_probability"]
    assert abs(res_high_vol["z_score"]) < abs(res_low_vol["z_score"])


def test_volatility_service_fallback_on_network_error():
    """
    Verifica que el servicio devuelva el valor de fallback si la llamada API a Binance falla.
    """
    vs = VolatilityService()
    
    with patch("requests.get") as mock_get:
        # Simular fallo de conexión HTTP 500
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        vol = vs.fetch_realized_daily_volatility("BTCUSDT")
        
        # Debe devolver el fallback configurado de 0.035 para BTC
        assert vol == 0.035
