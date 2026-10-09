"""
connectors/polymarket_ingest.py
Ingesta orientada a búsquedas de alto volumen (Crypto/Fed) con soporte dinámico para YES/NO.
"""

import urllib.request
import urllib.parse
import json
import sys

SEARCH_QUERIES = ["bitcoin", "crypto", "ethereum", "fed"]

def fetch_markets_by_query(query: str) -> list:
    encoded_query = urllib.parse.quote(query)
    url = f"https://gamma-api.polymarket.com/events?closed=false&limit=10&q={encoded_query}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        sys.stderr.write(f"[ERROR Ingesta] Error consultando query '{query}': {e}\n")
    return []

def parse_candidates_multi_side(events: list) -> list:
    candidates = []
    
    for event in events:
        markets = event.get("markets", [])
        for market in markets:
            condition_id = market.get("conditionId")
            clob_token_ids = market.get("clobTokenIds")
            
            if not condition_id or not clob_token_ids:
                continue

            outcome_prices = market.get("outcomePrices")
            if not outcome_prices:
                continue

            try:
                prices = json.loads(outcome_prices)
                price_yes = float(prices[0])
                price_no = float(prices[1]) if len(prices) > 1 else (1.0 - price_yes)
            except Exception:
                continue

            # Evaluar lado YES si el precio es razonable (> 0.20)
            if price_yes >= 0.20:
                candidates.append({
                    "condition_id": condition_id,
                    "outcome": "YES",
                    "limit_price": round(price_yes, 2),
                    "amount": 10.0,
                    "question": market.get("question", "Sin título")
                })

            # Evaluar lado NO si el precio de YES es bajo (lo que implica un precio de NO alto)
            if price_no >= 0.40:
                candidates.append({
                    "condition_id": condition_id,
                    "outcome": "NO",
                    "limit_price": round(price_no, 2),
                    "amount": 10.0,
                    "question": f"{market.get('question', 'Sin título')} [LADO NO]"
                })

            if len(candidates) >= 15:
                break
        if len(candidates) >= 15:
            break
            
    return candidates

def get_live_snapshot():
    all_events = []
    for q in SEARCH_QUERIES:
        events = fetch_markets_by_query(q)
        all_events.extend(events)
        if len(all_events) >= 20:
            break
            
    return parse_candidates_multi_side(all_events)

if __name__ == "__main__":
    snapshot = get_live_snapshot()
    print(json.dumps(snapshot))
