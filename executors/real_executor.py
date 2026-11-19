"""
executors/real_executor.py
Ejecutor de órdenes reales para Polymarket utilizando py-clob-client sobre Polygon.
Mantiene paridad de interfaz con PaperExecutor para alternar modos de forma transparente.
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    PY_CLOB_AVAILABLE = True
except ImportError:
    PY_CLOB_AVAILABLE = False

from config import (
    CLOB_API_URL,
    DEFAULT_ORDER_AMOUNT_USDC,
)

logger = logging.getLogger("real_executor")


class RealExecutor:
    def __init__(self, private_key: Optional[str] = None, chain_id: int = 137, host: str = CLOB_API_URL):
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY") or os.getenv("POLYGON_PRIVATE_KEY")
        self.chain_id = chain_id
        self.host = host
        self.client: Optional[ClobClient] = None
        
        self._initialize_client()

    def _initialize_client(self):
        """Inicializa ClobClient y deriva las credenciales de API L2 mediante firma EIP-712."""
        if not PY_CLOB_AVAILABLE:
            logger.error("[REAL] La librería `py-clob-client` no está instalada. Ejecuta: pip install py-clob-client")
            return

        if not self.private_key or self.private_key.startswith("0x_TU_CLAVE") or len(self.private_key) < 60:
            logger.warning("[REAL] Clave privada no configurada en .env. El ejecutor funcionará en modo Standby / Guard.")
            return

        try:
            self.client = ClobClient(
                host=self.host,
                key=self.private_key,
                chain_id=self.chain_id
            )
            # Generación o derivación automática de credenciales API L2
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            logger.info("[REAL] ClobClient autenticado exitosamente en Polygon (Chain ID: %d)", self.chain_id)
        except Exception as e:
            logger.error("[REAL] Error inicializando ClobClient: %s", e)
            self.client = None

    def execute_order(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Somete una orden límite real al libro de órdenes centralizado (CLOB) de Polymarket.
        """
        if not self.client:
            return {
                "status": "REJECTED",
                "action": "REJECTED",
                "reason": "CLIENT_NOT_INITIALIZED (Configura POLYMARKET_PRIVATE_KEY en .env)",
            }

        token_id = candidate.get("token_id")
        price = candidate.get("limit_price", candidate.get("price"))
        amount_usd = candidate.get("amount", candidate.get("size", DEFAULT_ORDER_AMOUNT_USDC))
        outcome = str(candidate.get("outcome", "YES")).upper()

        if not token_id or token_id == "N/A":
            return {"status": "REJECTED", "action": "REJECTED", "reason": "MISSING_OR_INVALID_TOKEN_ID"}

        if not price or float(price) <= 0:
            return {"status": "REJECTED", "action": "REJECTED", "reason": "INVALID_PRICE"}

        # Mapeo de lado (BUY para YES / SELL para NO según convención del token)
        side = BUY if outcome == "YES" else SELL

        try:
            price_float = float(price)
            # Calcular shares = USDC / precio
            shares = float(amount_usd) / price_float

            order_args = OrderArgs(
                price=round(price_float, 4),
                size=round(shares, 4),
                side=side,
                token_id=str(token_id)
            )

            # Firma EIP-712 off-chain y envío GTC a Polymarket CLOB
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            if resp and resp.get("success"):
                order_id = resp.get("orderID", "N/A")
                logger.info("[REAL] Orden transmitida con éxito. OrderID: %s", order_id)
                return {
                    "status": "ORDER_PLACED",
                    "action": "ORDER_PLACED",
                    "match_key": order_id,
                    "order_id": order_id,
                    "response": resp,
                }
            else:
                error_msg = resp.get("errorMsg", "Respuesta rechazada por la API de CLOB") if resp else "Sin respuesta"
                logger.warning("[REAL] Rechazo al colocar orden real: %s", error_msg)
                return {
                    "status": "REJECTED",
                    "action": "REJECTED",
                    "reason": error_msg,
                    "response": resp,
                }

        except Exception as e:
            logger.error("[REAL] Excepción durante ejecución de orden en vivo: %s", e)
            return {
                "status": "ERROR",
                "action": "ERROR",
                "reason": str(e),
            }

    def execute_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Cumple el mismo contrato de ráfaga que PaperExecutor.
        """
        results = []
        for cand in candidates:
            res = self.execute_order(cand)
            results.append({**cand, **res})
        return results

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancela una orden activa en la red mediante su order_id."""
        if not self.client:
            return {"status": "ERROR", "reason": "CLIENT_NOT_INITIALIZED"}
        try:
            resp = self.client.cancel(order_id)
            return {"status": "CANCELLED", "action": "CANCELLED", "order_id": order_id, "response": resp}
        except Exception as e:
            logger.error("[REAL] Error al cancelar orden %s: %s", order_id, e)
            return {"status": "ERROR", "reason": str(e)}

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Consulta el estado actual de una orden en Polygon/CLOB."""
        if not self.client:
            return {"status": "ERROR", "reason": "CLIENT_NOT_INITIALIZED"}
        try:
            resp = self.client.get_order(order_id)
            return {"status": "SUCCESS", "order": resp}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}