import sys
import json
import logging
import requests
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import MAX_HOURS_TO_EXPIRY, MIN_MARKET_DURATION_MINUTES
from services.market_resolver import MarketResolver

_DURATION_SLUG_RE = re.compile(r"updown-(\d+)([mhd])\b", re.IGNORECASE)
_WINDOW_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*([AP]M)\s*-\s*(\d{1,2}):(\d{2})\s*([AP]M)",
    re.IGNORECASE,
)


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _slug_duration_minutes(slug):
    match = _DURATION_SLUG_RE.search(slug or "")
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    return amount * {"m": 1, "h": 60, "d": 1440}[unit]


def _title_window_minutes(question):
    match = _WINDOW_RE.search(question or "")
    if not match:
        return None

    start_hour, start_min, start_ampm, end_hour, end_min, end_ampm = match.groups()

    def _to_minutes(hour, minute, ampm):
        hour = int(hour) % 12
        if ampm.upper() == "PM":
            hour += 12
        return hour * 60 + int(minute)

    start = _to_minutes(start_hour, start_min, start_ampm)
    end = _to_minutes(end_hour, end_min, end_ampm)
    if end <= start:
        end += 24 * 60
    return end - start


def _market_duration_minutes(market, question, slug, end_date):
    for duration in (
        _slug_duration_minutes(slug),
        _title_window_minutes(question),
    ):
        if duration is not None:
            return duration

    start_date = _parse_datetime(market.get("startDate"))
    if start_date and end_date:
        return (end_date - start_date).total_seconds() / 60

    return None

logger = logging.getLogger("updown_discovery")

class CryptoUniverseDiscovery:
    def __init__(self, gamma_api_url: str = "https://gamma-api.polymarket.com/events"):
        self.gamma_api_url = gamma_api_url
        self.resolver = MarketResolver()
        self.target_symbols = ["BTC", "ETH", "SOL", "XRP"]
        self.intraday_keywords = ["15m", "1h", "HOURLY", "UP OR DOWN", "UP/DOWN"]

    def discover_updown_markets(self) -> List[Dict[str, Any]]:
        """
        Descubre mercados 'Up/Down' (Price Above/Below) para los símbolos objetivo.
        Filtra contratos con vencimiento en la ventana de MAX_HOURS_TO_EXPIRY.
        Implementa pre-filtrado de mercados zombis.
        """
        discovered = []
        try:
            now = datetime.now(timezone.utc)
            max_expiry = now + timedelta(hours=MAX_HOURS_TO_EXPIRY)

            # Consultar eventos activos dentro de la ventana corta de vencimiento
            params = {
                "closed": "false",
                "active": "true",
                "limit": 200,
                "order": "endDate",
                "ascending": "true",
                "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": max_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            resp = requests.get(self.gamma_api_url, params=params, timeout=10)
            if resp.status_code != 200:
                return []

            events = resp.json()

            for event in events:
                markets = event.get("markets", [])
                for market in markets:
                    question = market.get("question", "")
                    q_upper = question.upper()
                    
                    # 1. Identificar símbolo
                    symbol = None
                    for s in self.target_symbols:
                        if s in q_upper or (s == "BTC" and "BITCOIN" in q_upper) or (s == "ETH" and "ETHEREUM" in q_upper):
                            symbol = s
                            break
                    
                    if not symbol:
                        continue

                    # 2. Pre-filtro de Expiración
                    end_date_str = market.get("endDate")
                    if not end_date_str:
                        continue
                    try:
                        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                        if end_date <= now or end_date > max_expiry:
                            continue

                        duration_minutes = _market_duration_minutes(
                            market, question, market.get("slug"), end_date
                        )
                        if (
                            duration_minutes is not None
                            and duration_minutes < MIN_MARKET_DURATION_MINUTES
                        ):
                            continue
                    except Exception:
                        continue

                    # 3. Pre-filtro de Precios y Spread (Zombis)
                    outcome_prices = market.get("outcomePrices")
                    if not outcome_prices:
                        continue
                    
                    try:
                        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                        if len(prices) < 2: continue
                        
                        price_yes = float(prices[0])
                        price_no = float(prices[1])
                        
                        # Filtro de precios extremos [0.08, 0.92]
                        if price_yes < 0.08 or price_yes > 0.92:
                            continue
                        
                        # Filtro de Spread > 10%
                        # En Gamma, price_yes + price_no suele ser >= 1.0. El exceso es el spread.
                        spread = (price_yes + price_no) - 1.0
                        if spread > 0.10:
                            continue
                            
                        if price_yes <= 0 or price_no <= 0:
                            continue

                    except Exception:
                        continue

                    # 4. Identificar si es Up/Down o Strike fijo
                    is_updown = any(pat in q_upper for pat in self.intraday_keywords)
                    is_strike = "ABOVE" in q_upper or "BELOW" in q_upper or "$" in q_upper
                    
                    if not (is_updown or is_strike):
                        continue

                    # 5. Extraer Strike si existe
                    strike_price = None
                    strike_match = re.search(r"\$(\d+(?:,\d+)*(?:\.\d+)?)", question)
                    if strike_match:
                        strike_price = float(strike_match.group(1).replace(",", ""))

                    # 6. Extraer IDs directamente del payload de Gamma
                    clob_token_ids = []
                    raw_clob_ids = market.get("clobTokenIds")
                    if raw_clob_ids:
                        if isinstance(raw_clob_ids, str):
                            try:
                                clob_token_ids = json.loads(raw_clob_ids)
                            except: pass
                        else:
                            clob_token_ids = raw_clob_ids

                    if not clob_token_ids or len(clob_token_ids) < 2:
                        continue

                    discovered.append({
                        "symbol": symbol,
                        "question": question,
                        "condition_id": market.get("conditionId"),
                        "token_id_yes": clob_token_ids[0],
                        "token_id_no": clob_token_ids[1],
                        "end_date": end_date_str,
                        "strike_price": strike_price,
                        "is_updown": is_updown,
                        "slug": market.get("slug"),
                        "outcome_prices": prices
                    })
        except Exception as e:
            logger.error(f"Error discovering markets: {e}")

        return discovered

import re

if __name__ == "__main__":
    discovery = CryptoUniverseDiscovery()
    markets = discovery.discover_updown_markets()
    print(f"Discovered {len(markets)} target markets.")
    for m in markets[:5]:
        print(f"- [{m['symbol']}] {m['question']} (Up/Down: {m['is_updown']})")
