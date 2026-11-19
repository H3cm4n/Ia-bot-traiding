"""
connectors/polymarket_ingest.py
Ingesta filtrada para mercados de Cripto (BTC, ETH, SOL, XRP), S&P 500 y Oro (Commodity).
"""

import sys
import json
import urllib.request

GAMMA_API_URL = "https://gamma-api.polymarket.com/events?closed=false&active=true&limit=100"
TARGET_KEYWORDS = [
    "BTC", "BITCOIN", 
    "ETH", "ETHEREUM", 
    "SOL", "SOLANA", 
    "XRP", "RIPPLE", 
    "S&P", "SP500", "500",
    "GOLD PRICE", "GOLD ABOVE", "GOLD HIT", "XAU"
]

def is_target_market(question: str) -> bool:
    q_upper = question.upper()
    # Evitar falsos positivos como Gold Cards o Medals
    if "GOLD CARD" in q_upper or "GOLD MEDAL" in q_upper:
        return False
    return any(kw in q_upper for kw in TARGET_KEYWORDS)

def fetch_diversified_markets(limit: int = 100) -> list:
    url = f"https://gamma-api.polymarket.com/events?closed=false&active=true&limit={limit}"
    candidates = []

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                return []
            events = json.loads(response.read().decode('utf-8'))
            
            for ev in events:
                markets = ev.get("markets", [])
                for market in markets:
                    question = market.get("question", "")
                    if not is_target_market(question):
                        continue

                    condition_id = market.get("conditionId") or market.get("condition_id")
                    if not condition_id:
                        continue

                    slug = market.get("slug", "")
                    end_date = market.get("endDate") or market.get("end_date")

                    # Identificación precisa del símbolo
                    q_upper = question.upper()
                    sym = "BTC" if ("BTC" in q_upper or "BITCOIN" in q_upper) else \
                          "ETH" if ("ETH" in q_upper or "ETHEREUM" in q_upper) else \
                          "SOL" if ("SOL" in q_upper or "SOLANA" in q_upper) else \
                          "XRP" if ("XRP" in q_upper or "RIPPLE" in q_upper) else \
                          "SP500" if ("S&P" in q_upper or "SP500" in q_upper) else \
                          "GOLD" if ("GOLD" in q_upper or "XAU" in q_upper) else "MACRO"

                    outcome_prices = market.get("outcomePrices")
                    price_yes = 0.50
                    if outcome_prices:
                        try:
                            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                            price_yes = float(prices[0])
                        except Exception:
                            pass

                    # Candidato YES
                    candidates.append({
                        "condition_id": condition_id,
                        "symbol": sym,
                        "question": question,
                        "outcome": "YES",
                        "limit_price": round(price_yes, 3),
                        "amount": 10.0,
                        "end_date": end_date,
                        "slug": slug
                    })
                    # Candidato NO
                    candidates.append({
                        "condition_id": condition_id,
                        "symbol": sym,
                        "question": f"{question} [LADO NO]",
                        "outcome": "NO",
                        "limit_price": round(max(0.01, 1.0 - price_yes), 3),
                        "amount": 10.0,
                        "end_date": end_date,
                        "slug": slug
                    })
    except Exception as e:
        sys.stderr.write(f"[ERROR Ingesta] Fallo al consultar API Polymarket: {e}\n")

    return candidates

def get_live_snapshot() -> list:
    return fetch_diversified_markets()

if __name__ == "__main__":
    snapshot = fetch_diversified_markets()
    sys.stderr.write(f"[INGEST] Candidatos Multiactivo Ingeridos: {len(snapshot)}\n")
    print(json.dumps(snapshot))
