"""
connectors/polymarket_ingest.py
Ingesta orientada a búsquedas de alto volumen con:

  - Rotación aleatoria de tags (crypto, pop-culture, business, science, politics).
  - Offset aleatorio para evitar estancamiento en los mismos contratos.
  - Soporte dinámico YES/NO a partir de `outcomePrices`.
  - Fallback por queries temáticas (bitcoin, crypto, ethereum, fed) si el
    muestreo por tag no devuelve suficientes candidatos.

La idea es que cada corrida vea un "corte" distinto del universo de mercados
activos, reduciendo el sesgo de selección y la correlación entre snapshots.
"""

import urllib.request
import urllib.parse
import json
import random
import sys
import requests

# ---------------------------------------------------------------------- #
# Configuración
# ---------------------------------------------------------------------- #
SEARCH_QUERIES = ["bitcoin", "crypto", "ethereum", "fed"]
TAGS_POOL = ["crypto", "pop-culture", "business", "science", "politics"]
OFFSET_POOL = [0, 15, 30, 45]

MAX_CANDIDATES = 15
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"


# ---------------------------------------------------------------------- #
# Capa 1: muestreo diversificado por tag + offset
# ---------------------------------------------------------------------- #
def fetch_diversified_markets(limit: int = MAX_CANDIDATES) -> list:
    """
    Consulta mercados activos variando el tag y el offset para evitar
    estancamiento en los mismos 15 contratos.

    - `tag_slug` se elige al azar de TAGS_POOL.
    - `offset` se elige al azar de OFFSET_POOL para barrer distintas páginas.
    - En caso de fallo de red devuelve [] (nunca lanza).
    """
    selected_tag = random.choice(TAGS_POOL)
    offset = random.choice(OFFSET_POOL)

    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "tag_slug": selected_tag,
    }

    try:
        res = requests.get(GAMMA_EVENTS_URL, params=params, timeout=10)
        if res.status_code == 200:
            return res.json() or []
    except requests.RequestException as e:
        sys.stderr.write(
            f"[ERROR Ingesta] Fallo consultando tag='{selected_tag}' "
            f"offset={offset}: {e}\n"
        )

    return []


# ---------------------------------------------------------------------- #
# Capa 2: fallback por queries temáticas
# ---------------------------------------------------------------------- #
def fetch_markets_by_query(query: str) -> list:
    """
    Fallback: consulta /events por una query de texto libre (bitcoin, crypto...).
    Útil cuando el muestreo por tag no devuelve suficientes candidatos válidos.
    """
    encoded_query = urllib.parse.quote(query)
    url = f"{GAMMA_EVENTS_URL}?closed=false&limit=10&q={encoded_query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write(
            f"[ERROR Ingesta] Error consultando query '{query}': {e}\n"
        )
    return []


# ---------------------------------------------------------------------- #
# Parseo de eventos -> candidatos con lados YES/NO
# ---------------------------------------------------------------------- #
def parse_candidates_multi_side(
    events: list, max_candidates: int = MAX_CANDIDATES
) -> list:
    """
    Convierte eventos crudos de Gamma en candidatos operables.

    Reglas:
      - YES se emite si `price_yes >= 0.20`.
      - NO  se emite si `price_no  >= 0.40` (evita duplicar mercados
        dominados por YES).
      - Se corta al alcanzar `max_candidates`.
    """
    candidates = []

    for event in events or []:
        for market in event.get("markets", []) or []:
            condition_id = market.get("conditionId")
            clob_token_ids = market.get("clobTokenIds")
            outcome_prices = market.get("outcomePrices")

            if not condition_id or not clob_token_ids or not outcome_prices:
                continue

            try:
                prices = (
                    json.loads(outcome_prices)
                    if isinstance(outcome_prices, str)
                    else outcome_prices
                )
                price_yes = float(prices[0])
                price_no = (
                    float(prices[1]) if len(prices) > 1 else (1.0 - price_yes)
                )
            except (ValueError, TypeError, IndexError):
                continue

            question = market.get("question", "Sin título")

            # Lado YES si el precio es razonable
            if price_yes >= 0.20:
                candidates.append(
                    {
                        "condition_id": condition_id,
                        "outcome": "YES",
                        "limit_price": round(price_yes, 2),
                        "amount": 10.0,
                        "question": question,
                    }
                )

            # Lado NO si el precio de NO es suficientemente alto
            if price_no >= 0.40:
                candidates.append(
                    {
                        "condition_id": condition_id,
                        "outcome": "NO",
                        "limit_price": round(price_no, 2),
                        "amount": 10.0,
                        "question": f"{question} [LADO NO]",
                    }
                )

            if len(candidates) >= max_candidates:
                return candidates

    return candidates


# ---------------------------------------------------------------------- #
# Snapshot completo: diversificado + fallback
# ---------------------------------------------------------------------- #
def get_live_snapshot(max_candidates: int = MAX_CANDIDATES) -> list:
    """
    Snapshot en dos etapas:

      1. Muestreo diversificado (tag + offset aleatorios).
      2. Si no se alcanzan `max_candidates`, se completa con queries temáticas
         (SEARCH_QUERIES), deduplicando por (condition_id, outcome).
    """
    # --- Etapa 1: diversificado ---
    events = fetch_diversified_markets(limit=max_candidates)
    candidates = parse_candidates_multi_side(events, max_candidates)

    # --- Etapa 2: fallback si hace falta ---
    if len(candidates) < max_candidates:
        seen = {(c["condition_id"], c["outcome"]) for c in candidates}

        for q in SEARCH_QUERIES:
            if len(candidates) >= max_candidates:
                break
            extra_events = fetch_markets_by_query(q)
            extra = parse_candidates_multi_side(extra_events, max_candidates)

            for c in extra:
                key = (c["condition_id"], c["outcome"])
                if key not in seen:
                    seen.add(key)
                    candidates.append(c)
                    if len(candidates) >= max_candidates:
                        break

    return candidates[:max_candidates]


if __name__ == "__main__":
    snapshot = get_live_snapshot()
    print(json.dumps(snapshot))