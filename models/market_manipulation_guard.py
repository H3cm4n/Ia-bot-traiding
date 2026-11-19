import sys
import time
import requests
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Fallback de sys.path para importaciones relativas
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.volatility_service import VolatilityService
from services.clob_depth_verifier import CLOBDepthVerifier

logger = logging.getLogger("manipulation_guard")

class MarketManipulationGuard:
    def __init__(self, binance_api_url: str = "https://api.binance.com"):
        self.vol_service = VolatilityService(binance_api_url)
        self.depth_verifier = CLOBDepthVerifier()
        self.binance_api_url = binance_api_url
        self.data_api_url = "https://data-api.polymarket.com"

    def get_binance_price(self, symbol: str) -> float:
        """Obtiene el precio actual de Binance Spot (último cierre)."""
        klines = self.get_binance_klines(symbol, limit=1)
        if klines:
            return float(klines[0][4])
        return 0.0

    def get_binance_klines(self, symbol: str, interval: str = "5m", limit: int = 6) -> List[List]:

        """Obtiene velas recientes de Binance Spot."""
        try:
            target = f"{symbol.upper()}USDT" if "USDT" not in symbol.upper() else symbol.upper()
            resp = requests.get(
                f"{self.binance_api_url}/api/v3/klines", 
                params={"symbol": target, "interval": interval, "limit": limit}, 
                timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Error fetching Binance klines for {symbol}: {e}")
        return []

    def check_spot_divergence(self, symbol: str, poly_price: float, prev_poly_price: float = None) -> Tuple[float, str]:
        """
        Compara la variación en Binance Spot (últimas 6 velas 5m) contra Polymarket.
        Si Poly se mueve >5% sin respaldo en Binance (<0.3%), penaliza.
        """
        klines = self.get_binance_klines(symbol)
        if not klines or len(klines) < 2:
            return 50.0, "SPOT_DATA_UNAVAILABLE"

        # Variación Spot (Precio Cierre actual vs Cierre inicial de la ventana)
        start_price = float(klines[0][4])
        end_price = float(klines[-1][4])
        spot_change = abs(end_price - start_price) / start_price

        # Si no hay precio previo de Polymarket, no podemos medir salto brusco instantáneo
        if prev_poly_price is None or prev_poly_price == 0:
            return 100.0, "SAFE_INITIAL"

        poly_change = abs(poly_price - prev_poly_price)
        
        # Umbrales: Poly > 0.05 (5%) y Spot < 0.003 (0.3%)
        if poly_change > 0.05 and spot_change < 0.003:
            return 20.0, "DIVERGENCE_DETECTED"

        return 100.0, "SAFE"

    def calculate_obi(self, asks: List[Dict], bids: List[Dict]) -> float:
        """
        Calcula el Order Book Imbalance (OBI).
        """
        total_bid_size = sum(float(b.get("size", 0)) for b in bids[:5])
        total_ask_size = sum(float(a.get("size", 0)) for a in asks[:5])

        if total_bid_size + total_ask_size == 0:
            return 0.0

        imbalance = abs(total_bid_size - total_ask_size) / (total_bid_size + total_ask_size)
        return max(0.0, 100.0 * (1.0 - imbalance))

    def detect_wash_trading(self, condition_id: str) -> Tuple[float, str]:
        """
        Analiza trades recientes: repetición de tamaños y concentración de billeteras.
        """
        try:
            url = f"{self.data_api_url}/trades"
            resp = requests.get(url, params={"conditionId": condition_id, "limit": 20}, timeout=5)
            if resp.status_code != 200:
                return 70.0, "DATA_API_UNAVAILABLE"

            trades = resp.json()
            if not trades:
                return 100.0, "NO_RECENT_TRADES"

            # 1. Repetición de tamaños
            sizes = [t.get("size") for t in trades]
            unique_sizes = len(set(sizes))
            size_ratio = unique_sizes / len(sizes)

            # 2. Concentración de billeteras (proxyWallet)
            wallets = [t.get("proxyWallet") for t in trades if t.get("proxyWallet")]
            if wallets:
                from collections import Counter
                most_common_wallet, count = Counter(wallets).most_common(1)[0]
                wallet_concentration = count / len(wallets)
                if wallet_concentration > 0.40:
                    return 20.0, f"WALLET_CONCENTRATION_DETECTED_{wallet_concentration:.2%}"

            if size_ratio < 0.3:
                return 30.0, "REPETITIVE_SIZE_DETECTED"

            return 100.0, "ORGANIC_FLOW"
        except Exception as e:
            logger.warning(f"Error checking wash trading: {e}")
            return 50.0, "WASH_CHECK_ERROR"

    def evaluate(
        self, 
        symbol: str, 
        condition_id: str, 
        asks: List[Dict], 
        bids: List[Dict], 
        poly_price: float,
        prev_poly_price: float = None,
        order_size_usd: float = 10.0
    ) -> Dict[str, Any]:
        """
        Evalúa integralmente la microestructura del mercado.
        """
        # 1. OBI Score
        obi_score = self.calculate_obi(asks, bids)
        
        # 2. Wash Trading Score
        wash_score, wash_reason = self.detect_wash_trading(condition_id)
        
        # 3. Spot Divergence Score
        spot_score, spot_reason = self.check_spot_divergence(symbol, poly_price, prev_poly_price)

        # 4. Profundidad real via CLOBDepthVerifier
        depth_res = self.depth_verifier.analyze_orderbook(asks, bids, target_size_usdc=order_size_usd)
        depth_valid = depth_res.get("is_valid", False)
        depth_reason = depth_res.get("rejection_reason", "INSUFFICIENT_DEPTH")

        # Score ponderado: 30% OBI, 40% Wash, 30% Spot
        final_score = (obi_score * 0.3) + (wash_score * 0.4) + (spot_score * 0.3)

        status = "SAFE_TO_TRADE"
        reasons = []

        if not depth_valid:
            status = "LOW_LIQUIDITY_TRAP"
            reasons.append(depth_reason)
        
        if final_score < 40:
            status = "MANIPULATION_SUSPECTED"
            reasons.append("LOW_MICROSTRUCTURE_SCORE")
        
        if spot_score < 40:
            reasons.append(spot_reason)
        
        if wash_score < 40:
            reasons.append(wash_reason)

        return {
            "microstructure_score": round(final_score, 2),
            "status": status,
            "reasons": reasons,
            "details": {
                "obi_score": round(obi_score, 2),
                "wash_score": round(wash_score, 2),
                "spot_score": round(spot_score, 2),
                "depth_valid": depth_valid
            }
        }

if __name__ == "__main__":
    # Prueba rápida
    guard = MarketManipulationGuard()
    # Mock data
    mock_asks = [{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "200"}]
    mock_bids = [{"price": "0.49", "size": "1000"}, {"price": "0.48", "size": "2000"}]
    
    # Simular divergencia brusca
    result = guard.evaluate(
        symbol="BTC", 
        condition_id="test_id", 
        asks=mock_asks, 
        bids=mock_bids, 
        poly_price=0.60, 
        prev_poly_price=0.50,
        order_size_usd=50.0
    )
    import json
    print(json.dumps(result, indent=2))

