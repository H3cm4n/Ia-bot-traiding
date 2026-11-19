""""
services/clob_client.py
Cliente de conexión con la API pública de CLOB de Polymarket.

Provee:
- Lectura del libro de órdenes (bids/asks) en vivo.
- Extracción de los niveles de profundidad L1–L5 por cada lado.
- Cálculo del VWAP de entrada (ask, en USDC) y de salida (bid, en shares).
- Filtro de profundidad reutilizable (check_executable_liquidity).
"""

import json
import logging
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Si no está instalado python-dotenv, el sistema lee del entorno directamente
    pass

from config import CLOB_API_URL  # fallback: "https://clob.polymarket.com"

try:
    from config import DEFAULT_ORDER_AMOUNT_USDC
except ImportError:  # valor por defecto si no está en config
    DEFAULT_ORDER_AMOUNT_USDC = 10.0

logger = logging.getLogger("clob_client")

class PolymarketCLOBClient:
    def __init__(self, base_url: str = CLOB_API_URL):
        self.base_url = base_url

    # ------------------------------------------------------------------ #
    # 1. Lectura del libro
    # ------------------------------------------------------------------ #
    def get_order_book(self, token_id: str) -> dict:
        """Obtiene bids y asks del token. Normaliza el orden:
        - asks ascendentes (mejor = más barato primero)
        - bids descendentes (mejor = más caro primero)
        """
        if not token_id or token_id == "N/A":
            return {"bids": [], "asks": []}

        url = f"{self.base_url}/book?token_id={token_id}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    asks = data.get("asks", []) or []
                    bids = data.get("bids", []) or []
                    asks = sorted(asks, key=lambda x: float(x.get("price", 0)))
                    bids = sorted(bids, key=lambda x: float(x.get("price", 0)), reverse=True)
                    return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.debug(
                f"[CLOB Client] Error consultando libro {token_id[:10]}...: {e}"
            )
        return {"bids": [], "asks": []}

    # ------------------------------------------------------------------ #
    # 2. Niveles de profundidad L1–L5
    # ------------------------------------------------------------------ #
    def get_depth_levels(
        self,
        side: str,
        n: int = 5,
        token_id: str | None = None,
        book: dict | None = None,
    ) -> list[dict]:
        """
        Devuelve hasta `n` niveles del libro (L1..Ln) para `side` ∈ {'ask','bid'}.

        Se puede pasar `book` ya obtenido (evita un segundo fetch) o `token_id`
        para consultarlo internamente.

        Retorna: [{"level": "L1", "price": p, "size": s, "usdc": p*s}, ...]
        """
        if book is None:
            if not token_id:
                return []
            book = self.get_order_book(token_id)

        side_l = side.lower()
        if side_l.startswith("a"):
            levels = book.get("asks", [])  # ya viene ascendente
        elif side_l.startswith("b"):
            levels = book.get("bids", [])  # ya viene descendente
        else:
            raise ValueError(f"side inválido: {side!r} (use 'ask' o 'bid')")

        out = []
        for i, lvl in enumerate(levels[:n], start=1):
            price = float(lvl.get("price", 0))
            size = float(lvl.get("size", 0))
            if price <= 0 or size <= 0:
                continue
            out.append(
                {
                    "level": f"L{i}",
                    "price": round(price, 4),
                    "size": round(size, 4),
                    "usdc": round(price * size, 2),
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # 3. VWAP de COMPRA (recorremos asks, target en USDC)
    # ------------------------------------------------------------------ #
    def calculate_vwap_ask(self, asks: list, target_usdc: float) -> dict:
        """VWAP para comprar `target_usdc` USDC de shares consumiendo asks."""
        if not asks:
            return {"valid": False, "reason": "EMPTY_ORDERBOOK"}
        if target_usdc <= 0:
            return {"valid": False, "reason": "INVALID_TARGET"}

        sorted_asks = sorted(asks, key=lambda x: float(x.get("price", 0)))
        remaining = float(target_usdc)
        total_shares = 0.0
        total_cost = 0.0

        for level in sorted_asks:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
            if price <= 0 or size <= 0:
                continue

            level_usdc = price * size
            if remaining <= level_usdc:
                shares = remaining / price
                total_shares += shares
                total_cost += remaining
                remaining = 0.0
                break
            else:
                total_shares += size
                total_cost += level_usdc
                remaining -= level_usdc

        if remaining > 1e-9:
            return {"valid": False, "reason": "INSUFFICIENT_LIQUIDITY_DEPTH"}

        vwap = total_cost / total_shares if total_shares > 0 else 0.0
        return {
            "valid": True,
            "side": "BUY",
            "vwap_price": round(vwap, 4),
            "total_shares": round(total_shares, 4),
            "total_cost_usdc": round(total_cost, 2),
        }

    # ------------------------------------------------------------------ #
    # 4. VWAP de VENTA (recorremos bids, target en shares)
    # ------------------------------------------------------------------ #
    def calculate_vwap_bid(self, bids: list, target_shares: float) -> dict:
        """VWAP para vender `target_shares` consumiendo bids (de mejor a peor)."""
        if not bids:
            return {"valid": False, "reason": "EMPTY_ORDERBOOK"}
        if target_shares <= 0:
            return {"valid": False, "reason": "INVALID_TARGET"}

        sorted_bids = sorted(
            bids, key=lambda x: float(x.get("price", 0)), reverse=True
        )
        remaining = float(target_shares)
        total_shares = 0.0
        total_usdc = 0.0

        for level in sorted_bids:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
            if price <= 0 or size <= 0:
                continue

            take = min(remaining, size)
            total_shares += take
            total_usdc += take * price
            remaining -= take
            if remaining <= 1e-9:
                break

        if remaining > 1e-9:
            return {"valid": False, "reason": "INSUFFICIENT_LIQUIDITY_DEPTH"}

        vwap = total_usdc / total_shares if total_shares > 0 else 0.0
        return {
            "valid": True,
            "side": "SELL",
            "vwap_price": round(vwap, 4),
            "total_shares": round(total_shares, 4),
            "total_received_usdc": round(total_usdc, 2),
        }

    # ------------------------------------------------------------------ #
    # 5. Filtro de profundidad + VWAP ejecutable (helper)
    #    Encapsula los pasos 1–3 del snippet para reutilizarlo desde
    #    cualquier módulo que evalúe candidatos en un bucle.
    # ------------------------------------------------------------------ #
    def check_executable_liquidity(
        self,
        token_id: str,
        target_usdc: float | None = None,
    ) -> dict:
        """
        Valida que exista liquidez suficiente para ejecutar una compra por
        `target_usdc` USDC y devuelve el VWAP real de entrada.

        Retorna:
          {"valid": True,  "vwap_price": p, "real_executable_price": p,
           "total_shares": s, "total_cost_usdc": c, ...}
          {"valid": False, "reason": "EMPTY_ORDERBOOK" | "INVALID_TARGET" |
                                     "INSUFFICIENT_LIQUIDITY_DEPTH"}
        """
        if target_usdc is None:
            target_usdc = DEFAULT_ORDER_AMOUNT_USDC

        # 1. Obtener libro de órdenes en vivo para el token
        book = self.get_order_book(token_id)
        asks = book.get("asks", [])

        # 2. Validar si existe liquidez suficiente para el target en USDC
        vwap_res = self.calculate_vwap_ask(asks, target_usdc=target_usdc)

        if not vwap_res.get("valid", False):
            reason = vwap_res.get("reason", "INSUFFICIENT_LIQUIDITY_DEPTH")
            logger.debug(f"[DEPTH-FILTER] Candidato rechazado: {reason}")
            return {"valid": False, "reason": reason}

        # 3. Exponer el VWAP real de entrada para calcular EV_net
        vwap_res["real_executable_price"] = vwap_res["vwap_price"]
        return vwap_res


# ---------------------------------------------------------------------- #
# Demo de integración
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = PolymarketCLOBClient()

    try:
        url_gamma = (
            "https://gamma-api.polymarket.com/markets"
            "?active=true&closed=false&limit=1"
        )
        req = urllib.request.Request(
            url_gamma, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            markets = json.loads(resp.read().decode("utf-8"))

        if markets and "clobTokenIds" in markets[0]:
            raw = markets[0]["clobTokenIds"]
            token_ids = json.loads(raw) if isinstance(raw, str) else raw
            active_token = token_ids[0]
            print(f"[✓] Token ID activo detectado: {active_token[:20]}...")

            book = client.get_order_book(active_token)
            print(
                f"[✓] Libro obtenido -> asks: {len(book['asks'])} | "
                f"bids: {len(book['bids'])}"
            )

            # L1–L5 de cada lado
            print("\n[ASK] L1–L5 (lado compra):")
            for lvl in client.get_depth_levels("ask", n=5, book=book):
                print(
                    f"  {lvl['level']}  price={lvl['price']}  "
                    f"size={lvl['size']}  usdc={lvl['usdc']}"
                )

            print("\n[BID] L1–L5 (lado venta):")
            for lvl in client.get_depth_levels("bid", n=5, book=book):
                print(
                    f"  {lvl['level']}  price={lvl['price']}  "
                    f"size={lvl['size']}  usdc={lvl['usdc']}"
                )

            # Ejemplos de VWAP (montos ilustrativos)
            print("\n[VWAP] Comprar 100 USDC:")
            print("  ", client.calculate_vwap_ask(book["asks"], 100))
            print("[VWAP] Vender 100 shares:")
            print("  ", client.calculate_vwap_bid(book["bids"], 100))

            # ----------------------------------------------------------
            # INTEGRACIÓN DEL FILTRO DE PROFUNDIDAD
            # (patrón típico dentro de un loop de evaluación de candidatos)
            # ----------------------------------------------------------
            print("\n[DEPTH-FILTER] Evaluando candidatos:")
            candidate_tokens = [active_token]  # uno o más tokens candidatos

            for token_id in candidate_tokens:
                # 1. Obtener libro de órdenes en vivo para el token
                book = client.get_order_book(token_id)
                asks = book.get("asks", [])

                # 2. Validar si existe liquidez suficiente para $10.00 USDC
                vwap_res = client.calculate_vwap_ask(
                    asks, target_usdc=DEFAULT_ORDER_AMOUNT_USDC
                )

                if not vwap_res.get("valid", False):
                    reason = vwap_res.get("reason", "INSUFFICIENT_LIQUIDITY_DEPTH")
                    logger.debug(
                        f"[DEPTH-FILTER] Candidato rechazado: {reason}"
                    )
                    # Se descarta la orden antes de comprometer capital
                    continue

                # 3. Usar el VWAP real de entrada para calcular EV_net
                real_executable_price = vwap_res["vwap_price"]
                print(
                    f"  ✓ {token_id[:12]}... | VWAP ejecutable: "
                    f"{real_executable_price} USDC/share | "
                    f"shares={vwap_res['total_shares']} | "
                    f"cost={vwap_res['total_cost_usdc']} USDC"
                )

            # Variante equivalente usando el helper encapsulado
            print("\n[DEPTH-FILTER] Vía helper check_executable_liquidity():")
            for token_id in candidate_tokens:
                res = client.check_executable_liquidity(token_id)
                if not res.get("valid", False):
                    continue
                print(
                    f"  ✓ real_executable_price={res['real_executable_price']}"
                )
        else:
            print("[!] No se encontró token activo en Gamma para la prueba.")
    except Exception as err:
        print(f"[!] Error ejecutando prueba de integración: {err}")