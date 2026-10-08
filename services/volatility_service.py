import numpy as np
import requests

class VolatilityService:
    def __init__(self, binance_api_url: str = "https://api.binance.com"):
        self.api_url = binance_api_url
        self.fallback_vol = {"BTCUSDT": 0.035, "ETHUSDT": 0.045, "SOLUSDT": 0.060, "XRPUSDT": 0.055}

    def fetch_realized_daily_volatility(self, symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 24) -> float:
        try:
            endpoint = f"{self.api_url}/api/v3/klines"
            params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
            response = requests.get(endpoint, params=params, timeout=5)
            if response.status_code != 200:
                return self.fallback_vol.get(symbol.upper(), 0.035)

            klines = response.json()
            close_prices = np.array([float(k[4]) for k in klines])
            if len(close_prices) < 2:
                return self.fallback_vol.get(symbol.upper(), 0.035)

            log_returns = np.log(close_prices[1:] / close_prices[:-1])
            std_period = np.std(log_returns, ddof=1)
            periods_per_day = 24 if interval == "1h" else 96
            daily_volatility = std_period * np.sqrt(periods_per_day)

            return round(max(0.01, min(0.25, float(daily_volatility))), 4)
        except Exception:
            return self.fallback_vol.get(symbol.upper(), 0.035)
