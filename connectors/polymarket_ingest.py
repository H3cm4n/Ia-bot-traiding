"""
connectors/polymarket_ingest.py
Módulo para la ingesta de datos en vivo desde la API de Polymarket (Gamma/CLOB API).
"""

import urllib.request
import json
import sys
from pathlib import Path

# Configuración de URLs públicas de Polymarket
GAMMA_API_URL = "https://gamma-api.polymarket.com/events?closed=false&limit=10&volume_num_min=1000"

def fetch_active_markets():
    """Consulta eventos y mercados activos desde Gamma API."""
    try:
        req = urllib.request.Request(
            GAMMA_API_URL, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                return data
    except Exception as e:
        sys.stderr.write(f"[ERROR Ingesta] Fallo al consultar API Polymarket: {e}\n")
        return []

def parse_candidates(events: list) -> list:
    """Transforma los eventos de Polymarket al esquema de candidatos del bot."""
    candidates = []
    
    for event in events:
        markets = event.get("markets", [])
        for market in markets:
            condition_id = market.get("conditionId")
            clob_token_ids = market.get("clobTokenIds")
            
            # Validar que el mercado esté activo y tenga identificadores
            if not condition_id or not clob_token_ids:
                continue

            # Obtener precios/probabilidades aproximadas si están disponibles
            outcome_prices = market.get("outcomePrices")
            price_yes = float(json.loads(outcome_prices)[0]) if outcome_prices else 0.50

            # Estructurar candidato compatible con el ejecutor
            candidate = {
                "condition_id": condition_id,
                "outcome": "YES",
                "limit_price": round(price_yes, 2),
                "amount": 10.0,
                "question": market.get("question", "Sin título"),
                "slug": market.get("slug", "")
            }
            candidates.append(candidate)
            
            # Limitar a máximo 5 candidatos por ciclo para mantener la eficiencia
            if len(candidates) >= 5:
                break
        if len(candidates) >= 5:
            break
            
    return candidates

def get_live_snapshot():
    events = fetch_active_markets()
    return parse_candidates(events)

if __name__ == "__main__":
    # Si se ejecuta directamente, imprime los candidatos parseados en JSON para la tubería (stdout)
    snapshot = get_live_snapshot()
    print(json.dumps(snapshot))
