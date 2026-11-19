"""
services/whale_tracker.py
Rastreador de Ballenas (Smart Money) para Polymarket.
Usa el filtro nativo CASH de Polymarket Data API y cruza con el Top 50 del leaderboard.
"""

import os
import time
import logging
import requests
from datetime import datetime
from typing import List, Dict, Any, Set

logger = logging.getLogger("whale_tracker")

WHALE_MIN_USD = float(os.getenv("WHALE_MIN_USD", 2500.0))
CACHE_TTL = 60
WINDOW_4H = 4 * 3600

# Umbrales de alineación usados por el ejecutor direccional.
WHALE_ALIGNED_FLOW = 2500.0
WHALE_CONFLICT_FLOW = -5000.0
WHALE_STRONG_OPPOSITION_FLOW = -10000.0

# Ignora arbitrajes de cierre (precios extremos) que distorsionan el flujo direccional.
MIN_VALID_WHALE_PRICE = 0.08
MAX_VALID_WHALE_PRICE = 0.85


class WhaleTracker:
    def __init__(self):
        self.last_fetch_time = 0.0
        self.cached_trades: List[Dict[str, Any]] = []
        self.top_wallets: Set[str] = set()
        self.last_leaderboard_fetch = 0.0
        self._warned_api = False

    def _extract_list(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "trades", "results", "items"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
        return []

    def _parse_timestamp(self, raw_ts: Any) -> float:
        if raw_ts is None:
            return 0.0
        try:
            val = float(raw_ts)
            return val / 1000.0 if val > 1e12 else val
        except (ValueError, TypeError):
            try:
                return datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

    def _fetch_top_wallets(self) -> None:
        if time.time() - self.last_leaderboard_fetch < 3600:
            return
        try:
            url = "https://data-api.polymarket.com/v2/leaderboard?limit=50&window=all"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                data = self._extract_list(resp.json())
                self.top_wallets = {
                    (item.get("proxyWallet") or item.get("proxyAddress") or "").lower()
                    for item in data
                    if (item.get("proxyWallet") or item.get("proxyAddress"))
                }
                self.last_leaderboard_fetch = time.time()
        except Exception as e:
            logger.debug(f"Error fetching leaderboard: {e}")

    def _fetch_recent_trades(self) -> None:
        if time.time() - self.last_fetch_time < CACHE_TTL:
            return
        url = "https://data-api.polymarket.com/v2/trades"
        # Intentar primero filtro nativo de ballenas en USD; fallback a feed general
        param_sets = [
            {"limit": 500, "filterType": "CASH", "filterAmount": int(WHALE_MIN_USD)},
            {"limit": 500},
        ]
        for params in param_sets:
            try:
                resp = requests.get(url, params=params, timeout=8)
                payload = resp.json()
                if isinstance(payload, dict) and "error" in payload:
                    if not self._warned_api:
                        logger.warning(f"[WHALE-API] Aviso endpoint {params}: {payload}")
                        self._warned_api = True
                    continue
                trades = self._extract_list(payload)
                if trades or resp.status_code == 200:
                    self.cached_trades = trades
                    self.last_fetch_time = time.time()
                    return
            except Exception as e:
                logger.debug(f"Error fetching trades ({params}): {e}")

    def _aggregate_trades(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = time.time()
        whale_yes_usd = 0.0
        whale_no_usd = 0.0
        top_whale_trades: List[Dict[str, Any]] = []
        seen_tx: Set[str] = set()

        for t in trades:
            try:
                tx_hash = t.get("transactionHash")
                if tx_hash:
                    if tx_hash in seen_tx:
                        continue
                    seen_tx.add(tx_hash)

                ts = self._parse_timestamp(t.get("timestamp", 0))
                if ts > 0 and (now - ts) > WINDOW_4H:
                    continue

                price = float(t.get("price", 0))
                if price < MIN_VALID_WHALE_PRICE or price > MAX_VALID_WHALE_PRICE:
                    continue
                size = float(t.get("size", 0))
                notional = price * size
                if notional < WHALE_MIN_USD:
                    continue

                wallet = (t.get("proxyWallet") or t.get("proxyAddress") or t.get("user") or "").lower()
                outcome = str(t.get("outcome", "")).upper()
                if not outcome:
                    outcome = "YES" if str(t.get("outcomeIndex")) == "0" else "NO"

                trade_side = str(t.get("side", "BUY")).upper()
                effective_side = outcome if trade_side == "BUY" else ("NO" if outcome == "YES" else "YES")

                if effective_side == "YES":
                    whale_yes_usd += notional
                else:
                    whale_no_usd += notional

                top_whale_trades.append({
                    "wallet": (wallet[:8] + "...") if wallet else "unknown",
                    "side": effective_side,
                    "amount": round(notional, 2),
                    "price": price,
                    "timestamp": int(ts),
                    "is_top_50": wallet in self.top_wallets,
                })
            except Exception:
                continue

        return self._format_result(whale_yes_usd, whale_no_usd, top_whale_trades)

    def get_whale_activity(self, condition_id: str) -> Dict[str, Any]:
        self._fetch_top_wallets()
        self._fetch_recent_trades()
        matched = [t for t in self.cached_trades if (t.get("conditionId") or t.get("market")) == condition_id]
        return self._aggregate_trades(matched)

    def get_asset_whale_activity(self, symbol: str) -> Dict[str, Any]:
        self._fetch_top_wallets()
        self._fetch_recent_trades()
        symbol_upper = symbol.upper()
        aliases = {
            "BTC": ("BTC", "BITCOIN"),
            "ETH": ("ETH", "ETHEREUM"),
            "SOL": ("SOL", "SOLANA"),
            "XRP": ("XRP", "RIPPLE"),
        }.get(symbol_upper, (symbol_upper,))

        matched = []
        for t in self.cached_trades:
            meta = t.get("metadata") or {}
            text = f"{meta.get('title', '')} {t.get('title', '')} {t.get('slug', '')}".upper()
            if any(k in text for k in aliases):
                matched.append(t)
        return self._aggregate_trades(matched)

    def evaluate_whale_alignment(
        self,
        side: str,
        net_flow_usd: float,
        size_usd: float,
        max_risk_usd: float = 15.0,
    ) -> Dict[str, Any]:
        """
        Ajusta el tamaño de la orden según el flujo neto de ballenas en la misma
        dirección operada. Devuelve tamaño final, alineación, acción y veto.
        """
        same_side_flow = net_flow_usd if str(side).upper() == "YES" else -net_flow_usd

        whale_alignment = "NEUTRAL"
        action_taken = "PROCEED"
        rejected = False
        final_size = size_usd

        if same_side_flow >= WHALE_ALIGNED_FLOW:
            whale_alignment = "ALIGNED"
            final_size = min(size_usd * 1.25, max_risk_usd * 1.25)
            action_taken = "BOOSTED_SIZE"
        elif same_side_flow <= WHALE_CONFLICT_FLOW:
            whale_alignment = "CONFLICT"
            if same_side_flow <= WHALE_STRONG_OPPOSITION_FLOW:
                whale_alignment = "REJECTED: WHALE_STRONG_OPPOSITION"
                action_taken = whale_alignment
                rejected = True
                final_size = 0.0
            else:
                final_size = size_usd * 0.5
                action_taken = "REDUCED_SIZE"

        return {
            "size_usd": round(final_size, 2),
            "whale_alignment": whale_alignment,
            "action_taken": action_taken,
            "rejected": rejected,
            "same_side_flow_usd": round(same_side_flow, 2),
        }

    def _format_result(self, yes_usd: float, no_usd: float, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        net_flow = yes_usd - no_usd
        consensus = "NEUTRAL"
        if net_flow >= WHALE_MIN_USD:
            consensus = "YES"
        elif net_flow <= -WHALE_MIN_USD:
            consensus = "NO"

        total_vol = yes_usd + no_usd
        conviction = 0
        if total_vol > 0:
            conviction = min(100, int((abs(net_flow) / total_vol) * 100))
            if abs(net_flow) >= WHALE_MIN_USD * 4:
                conviction = min(100, conviction + 20)

        trades.sort(key=lambda x: x["amount"], reverse=True)
        return {
            "whale_yes_usd": round(yes_usd, 2),
            "whale_no_usd": round(no_usd, 2),
            "whale_net_flow_usd": round(net_flow, 2),
            "whale_consensus": consensus,
            "whale_conviction_score": conviction,
            "top_whale_trades": trades[:5],
        }
