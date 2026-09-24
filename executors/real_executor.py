"""
executors/real_executor.py
Ejecutor de órdenes reales para Polymarket utilizando py-clob-client.
"""

import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

load_dotenv()


class RealExecutor:
    def __init__(self):
        self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        self.chain_id = int(os.getenv("POLYMARKET_CHAIN_ID", 137))
        self.host = os.getenv("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com/")

        if not self.private_key or self.private_key == "0x_TU_CLAVE_PRIVADA_AQUI":
            raise ValueError("POLYMARKET_PRIVATE_KEY no está configurada correctamente en el archivo .env")

        self.client = ClobClient(
            host=self.host,
            key=self.private_key,
            chain_id=self.chain_id
        )
        # Generar o derivar credenciales L2 de la API de Polymarket
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def execute_order(self, candidate: dict) -> dict:
        """Somete una orden límite al libro de órdenes de Polymarket."""
        token_id = candidate.get("token_id")
        price = candidate.get("price")
        size = candidate.get("size", 10.0)
        outcome = str(candidate.get("outcome", "YES")).upper()
        side = BUY if outcome == "YES" else SELL

        if not token_id or not price:
            return {
                "action": "REJECTED",
                "reason": "Falta token_id o precio en la señal"
            }

        try:
            order_args = OrderArgs(
                price=float(price),
                size=float(size),
                side=side,
                token_id=str(token_id)
            )

            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            if resp.get("success"):
                order_id = resp.get("orderID", "N/A")
                sys.stderr.write(f"[REAL] Orden colocada: {order_id}\n")
                return {
                    "action": "ORDER_PLACED",
                    "match_key": order_id,
                    "status": "LIVE",
                    "response": resp
                }
            else:
                reason = resp.get("errorMsg", "Error de respuesta del servidor")
                sys.stderr.write(f"[REAL] Error en orden: {reason}\n")
                return {
                    "action": "REJECTED",
                    "reason": reason,
                    "status": "FAILED"
                }

        except Exception as e:
            sys.stderr.write(f"[REAL] Excepción al ejecutar orden: {e}\n")
            return {
                "action": "ERROR",
                "reason": str(e),
                "status": "FAILED"
            }

    def cancel_order(self, order_id: str) -> dict:
        """Cancela una orden activa usando su order_id."""
        try:
            resp = self.client.cancel(order_id)
            return {"action": "CANCELLED", "order_id": order_id, "response": resp}
        except Exception as e:
            return {"action": "ERROR", "reason": str(e)}
