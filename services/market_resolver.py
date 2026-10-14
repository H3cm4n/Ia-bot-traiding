"""
services/market_resolver.py
Resolver de token_ids de Polymarket.

Combina:
  - Normalización del nombre del mercado (aliases ETH/ethereum, BTC/bitcoin).
  - Extracción del lado real (YES/NO) a partir de la pregunta o del parámetro `side`,
    contemplando marcadores tipo '[LADO NO]', '[NO]', '[LADO YES]', '[YES]'.
  - Validación por regex con blacklist para evitar falsos positivos (megaeth,
    ethernet, bitcoincash, hegseth...).
  - Búsqueda directa en /events por alias y fallback ordenado por volumen en /markets.
  - Devolución del `token_id` correcto para el lado solicitado (YES o NO),
    del `condition_id`, la `question` y el `side`.
"""

import sys
import json
import re
import logging
import requests

logger = logging.getLogger("market_resolver")


class MarketResolver:
    def __init__(
        self,
        gamma_events_url: str = "https://gamma-api.polymarket.com/events",
        gamma_markets_url: str = "https://gamma-api.polymarket.com/markets",
        timeout: float = 10.0,
    ):
        self.gamma_events_url = gamma_events_url
        self.gamma_markets_url = gamma_markets_url
        self.timeout = timeout
        self.blacklist = ["megaeth", "ethernet", "bitcoincash", "hegseth"]

    # ------------------------------------------------------------------ #
    # Normalización de entrada
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_side_and_clean_question(question: str, side: str):
        """
        Extrae el lado real (YES/NO) a partir de marcadores en la pregunta
        y devuelve la pregunta limpia sin los marcadores.

        Prioridad: marcadores explícitos en la pregunta > parámetro `side`.
        """
        target_side = (side or "YES").upper()
        clean_question = question or ""

        # Marcadores NO
        if re.search(r"\[LADO NO\]|\[NO\]", clean_question, flags=re.IGNORECASE):
            target_side = "NO"
            clean_question = re.sub(
                r"\[LADO NO\]|\[NO\]", "", clean_question, flags=re.IGNORECASE
            ).strip()
        # Marcadores YES
        elif re.search(r"\[LADO YES\]|\[YES\]", clean_question, flags=re.IGNORECASE):
            target_side = "YES"
            clean_question = re.sub(
                r"\[LADO YES\]|\[YES\]", "", clean_question, flags=re.IGNORECASE
            ).strip()

        return target_side, clean_question

    @staticmethod
    def _build_aliases(query_lower: str) -> list:
        """Expande el término de búsqueda en una lista de aliases."""
        aliases = [query_lower]
        if query_lower in ("ethereum", "eth"):
            aliases = ["eth", "ethereum"]
        elif query_lower in ("bitcoin", "btc"):
            aliases = ["btc", "bitcoin"]
        return aliases

    def _is_valid_match(self, text: str, aliases: list) -> bool:
        """Valida con regex y blacklist que el texto corresponda al mercado buscado."""
        if not text:
            return False
        t = text.lower()
        if any(b in t for b in self.blacklist):
            return False
        for a in aliases:
            pattern = r"(^|[^a-z0-9])" + re.escape(a) + r"($|[^a-z0-9])"
            if re.search(pattern, t):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Utilidades de payload
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_list_field(value):
        """Parsea un campo que puede venir como lista ya, o como string JSON."""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return None
        return value

    def _extract_token_for_side(self, market: dict, target_side: str):
        """
        Extrae el token_id correspondiente al `target_side` a partir de un dict
        de mercado de Polymarket.

        Usa `outcomes` para localizar el índice del lado solicitado y luego
        indexa `clobTokenIds`. Si `outcomes` no está disponible, cae a la
        convención [YES, NO] de Polymarket.
        """
        clob_ids = self._parse_list_field(market.get("clobTokenIds"))
        outcomes = self._parse_list_field(market.get("outcomes"))

        if not clob_ids:
            return None

        if outcomes:
            for idx, outcome_name in enumerate(outcomes):
                if (
                    isinstance(outcome_name, str)
                    and outcome_name.strip().upper() == target_side
                    and idx < len(clob_ids)
                ):
                    return clob_ids[idx]
            # Si no se encontró el lado exacto, caemos al fallback
        # Fallback: convención de Polymarket -> [YES, NO]
        if target_side == "YES" and len(clob_ids) >= 1:
            return clob_ids[0]
        if target_side == "NO" and len(clob_ids) >= 2:
            return clob_ids[1]
        return None

    def _build_result(self, market: dict, target_side: str, token_id: str) -> dict:
        return {
            "token_id": token_id,
            "YES": (
                self._parse_list_field(market.get("clobTokenIds")) or [None, None]
            )[0]
            if self._parse_list_field(market.get("clobTokenIds"))
            else None,
            "NO": (
                self._parse_list_field(market.get("clobTokenIds")) or [None, None]
            )[1]
            if self._parse_list_field(market.get("clobTokenIds"))
            and len(self._parse_list_field(market.get("clobTokenIds"))) >= 2
            else None,
            "question": market.get("question"),
            "side": target_side,
            "condition_id": market.get("conditionId"),
        }

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    def resolve_token_id(self, question: str, side: str = "YES") -> dict:
        """
        Resuelve el token_id para un mercado y un lado (YES/NO) determinados.

        Devuelve un dict con:
          - token_id      : id del token del lado solicitado
          - YES / NO      : ids de ambos lados (si están disponibles)
          - question      : pregunta del mercado en Polymarket
          - side          : lado resuelto (YES/NO)
          - condition_id  : conditionId del mercado
        Devuelve `None` si no se encuentra un mercado válido.
        """
        target_side, clean_question = self._extract_side_and_clean_question(
            question, side
        )
        search_query = clean_question.split("?")[0].strip().lower()
        if not search_query:
            logger.warning("[RESOLVER] Pregunta vacía tras normalización.")
            return None

        aliases = self._build_aliases(search_query)

        # -------- Búsqueda directa en /events por alias --------
        for alias in aliases:
            try:
                res = requests.get(
                    self.gamma_events_url,
                    params={
                        "limit": 50,
                        "active": "true",
                        "closed": "false",
                        "q": alias,
                    },
                    timeout=self.timeout,
                )
                if res.status_code != 200:
                    continue
                for ev in res.json() or []:
                    for m in ev.get("markets", []) or []:
                        q_text = m.get("question", "")
                        s_text = m.get("slug", "")
                        if self._is_valid_match(q_text, aliases) or self._is_valid_match(
                            s_text, aliases
                        ):
                            token_id = self._extract_token_for_side(m, target_side)
                            if token_id:
                                return self._build_result(m, target_side, token_id)
            except requests.RequestException as e:
                logger.error(
                    f"[RESOLVER] Error en /events para alias '{alias}': {e}"
                )

        # -------- Fallback en /markets ordenado por volumen --------
        try:
            res_m = requests.get(
                self.gamma_markets_url,
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 200,
                    "order": "volume",
                    "ascending": "false",
                },
                timeout=self.timeout,
            )
            if res_m.status_code == 200:
                for m in res_m.json() or []:
                    q_text = m.get("question", "")
                    s_text = m.get("slug", "")
                    if self._is_valid_match(q_text, aliases) or self._is_valid_match(
                        s_text, aliases
                    ):
                        token_id = self._extract_token_for_side(m, target_side)
                        if token_id:
                            return self._build_result(m, target_side, token_id)
        except requests.RequestException as e:
            logger.error(f"[RESOLVER] Error en /markets fallback: {e}")

        sys.stderr.write(
            f"[RESOLVER] No se encontro mercado para: '{question}' "
            f"[LADO {target_side}]\n"
        )
        return None

    # ------------------------------------------------------------------ #
    # Compatibilidad con el consumidor original (get_market_tokens)
    # ------------------------------------------------------------------ #
    def get_market_tokens(self, query_text: str) -> dict:
        """
        Compatibilidad con la versión previa.

        Devuelve {"YES": <token_id>, "NO": <token_id>, "question": ..., "condition_id": ...}
        o {} si no se encuentra mercado. Internamente usa `resolve_token_id` para
        resolver el lado YES (y de paso obtiene ambos ids del payload).
        """
        res = self.resolve_token_id(query_text, side="YES")
        if not res:
            return {}
        return {
            "YES": res.get("YES"),
            "NO": res.get("NO"),
            "question": res.get("question"),
            "condition_id": res.get("condition_id"),
        }