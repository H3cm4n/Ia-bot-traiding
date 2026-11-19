import sys
import json
import logging
import requests
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.market_resolver import MarketResolver

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
        Filtra contratos horarios, diarios y semanales.
        Implementa pre-filtrado de mercados zombis.
        """
        discovered = []
        try:
            # Consultar eventos activos con keywords ampliadas
            params = {
                "closed": "false",
                "active": "true",
                "limit": 200, # Aumentado para mayor cobertura
                "order": "volume",
                "ascending": "false"
            }
            resp = requests.get(self.gamma_api_url, params=params, timeout=10)
            if resp.status_code != 200:
                return []

            events = resp.json()
            now = datetime.now(timezone.utc)

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
                        if end_date <= now:
                            continue
                    except:
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
