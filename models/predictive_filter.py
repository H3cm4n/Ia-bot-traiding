"""
models/predictive_filter.py
Filtro Predictivo Cuantitativo Multiactivo (BTC, ETH, SOL, XRP, S&P 500, ORO).
Ajusta la volatilidad anualizada según la clase de activo.
"""

import sys
import json
import re
import math
import urllib.request
from datetime import datetime, timezone
from scipy.stats import norm

MIN_REQUIRED_EV = 0.05          # Margen neto mínimo de +5.0%
MIN_EXECUTABLE_PRICE = 0.15     # Suelo de precio
MAX_EXECUTABLE_PRICE = 0.70     # Techo de precio

ASSET_VOLATILITY = {
    "BTC": 0.55,
    "ETH": 0.60,
    "SOL": 0.75,
    "XRP": 0.70,
    "SP500": 0.16,
    "GOLD": 0.18,
    "DEFAULT": 0.50
}

def get_binance_spot(symbol_pair: str) -> float:
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol_pair}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                return float(data.get("price", 0.0))
    except Exception:
        pass
    return 0.0

def get_sp500_spot() -> float:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1m&range=1d"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                meta = data['chart']['result'][0]['meta']
                return float(meta.get('regularMarketPrice', 0.0))
    except Exception:
        pass
    return 0.0

def resolve_spot_price(asset: str) -> float:
    if asset == "BTC":
        return get_binance_spot("BTCUSDT")
    elif asset == "ETH":
        return get_binance_spot("ETHUSDT")
    elif asset == "SOL":
        return get_binance_spot("SOLUSDT")
    elif asset == "XRP":
        return get_binance_spot("XRPUSDT")
    elif asset == "GOLD":
        return get_binance_spot("PAXGUSDT")
    elif asset == "SP500":
        return get_sp500_spot()
    return 0.0

def parse_strike_and_expiry(question: str, end_date_iso: str = None) -> tuple[float, float]:
    strike = 0.0
    m_strike = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)\s*k?\b', question, re.IGNORECASE)
    if m_strike:
        val_str = m_strike.group(1).replace(',', '')
        try:
            val = float(val_str)
            if 'k' in m_strike.group(0).lower() and val < 1000:
                val *= 1000.0
            if val > 5.0:
                strike = val
        except ValueError:
            pass

    t_years = 0.0
    if end_date_iso:
        try:
            end_dt = datetime.fromisoformat(end_date_iso.replace('Z', '+00:00'))
            now_dt = datetime.now(timezone.utc)
            delta_seconds = (end_dt - now_dt).total_seconds()
            if delta_seconds > 0:
                t_years = delta_seconds / (365.25 * 86400.0)
        except Exception:
            pass

    return strike, t_years

def calculate_time_aware_fair_value(spot: float, strike: float, t_years: float, vol: float) -> float:
    if spot <= 0 or strike <= 0 or t_years <= 1e-6 or vol <= 0:
        return 0.0
    
    vol = max(vol, 0.10)
    sqrt_t = math.sqrt(t_years)
    try:
        d2 = (math.log(spot / strike) - 0.5 * (vol ** 2) * t_years) / (vol * sqrt_t)
        prob = float(norm.cdf(d2))
    except (ValueError, ZeroDivisionError):
        return 0.0
        
    return max(0.01, min(0.99, prob))


def evaluate_candidate(cand: dict) -> tuple[bool, str, dict]:
    symbol = str(cand.get("symbol", "")).upper()
    question = str(cand.get("question", ""))
    price = float(cand.get("limit_price", cand.get("price", 0.0)))
    
    # Descartar falsos positivos explícitos
    if "GOLD CARD" in question.upper() or "GOLD MEDAL" in question.upper():
        return False, "NON_FINANCIAL_GOLD_ASSET", {"fair_prob": 0.0, "ev_net": 0.0}

    if not (MIN_EXECUTABLE_PRICE <= price <= MAX_EXECUTABLE_PRICE):
        return False, "INVALID_EXECUTABLE_PRICE", {"fair_prob": 0.0, "ev_net": 0.0}

    asset = symbol if symbol in ASSET_VOLATILITY else "DEFAULT"
    spot = resolve_spot_price(asset)
    if spot <= 0:
        return False, f"MISSING_SPOT_PRICE_FOR_{asset}", {"fair_prob": 0.0, "ev_net": 0.0}

    strike, t_years = parse_strike_and_expiry(question, cand.get("end_date"))
    if strike <= 0 or t_years <= 0:
        return False, "MISSING_STRIKE_OR_EXPIRY", {"fair_prob": 0.0, "ev_net": 0.0}

    vol = ASSET_VOLATILITY.get(asset, ASSET_VOLATILITY["DEFAULT"])
    fair_prob = calculate_time_aware_fair_value(spot, strike, t_years, vol)

    outcome = str(cand.get("outcome", "YES")).upper()
    if outcome == "NO":
        fair_prob = 1.0 - fair_prob

    costs = float(cand.get("spread", 0.01)) + float(cand.get("slippage", 0.005))
    ev_net = fair_prob - price - costs

    if ev_net < MIN_REQUIRED_EV:
        return False, f"INSUFFICIENT_EDGE_({ev_net:.4f}_<_{MIN_REQUIRED_EV:.4f})", {"fair_prob": fair_prob, "ev_net": ev_net}

    return True, "PASSED_DYNAMIC_EDGE", {"fair_prob": fair_prob, "ev_net": ev_net}

if __name__ == '__main__':
    try:
        try:
            raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        except (OSError, ValueError):
            raw = ""

        if raw.strip():
            candidates = json.loads(raw)
            approved = []
            for cand in candidates:
                passed, reason, metrics = evaluate_candidate(cand)
                if passed:
                    cand["fair_probability"] = metrics["fair_prob"]
                    cand["ev_net"] = metrics["ev_net"]
                    approved.append(cand)
                else:
                    sys.stderr.write(f"[ML-FILTER] Candidato {cand.get('symbol', 'UNK')} descartado: {reason}\n")
            print(json.dumps(approved))
        else:
            print("[]")
    except Exception as e:
        sys.stderr.write(f"[ML-FILTER] Error en ejecución: {e}\n")
        print("[]")
