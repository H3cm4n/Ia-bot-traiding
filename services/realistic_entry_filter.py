"""
services/realistic_entry_filter.py
Filtro de entrada realista basado en la Expectativa Matemática Neta (EV_net).
Aplica descarte por margen insuficiente y datos inválidos.
"""

import logging

logger = logging.getLogger("entry_filter")

class RealisticEntryFilter:
    def __init__(self, min_edge_margin: float = 0.02, base_commission_pct: float = 0.0):
        self.min_edge_margin = min_edge_margin  # Margen de seguridad mínimo (+2.0%)
        self.base_commission_pct = base_commission_pct

    def evaluate_opportunity(self, fair_probability: float, executable_price: float, spread: float = 0.0, slippage: float = 0.0) -> dict:
        """
        Calcula EV_net = q - p - costes.
        Operar solo si EV_net > min_edge_margin.
        """
        # 1. Sanitización de precios y probabilidades de entrada
        if fair_probability is None or executable_price is None:
            return {"approved": False, "reason": "MISSING_DATA", "edge": 0.0}

        if executable_price <= 0 or executable_price >= 1.0:
            return {"approved": False, "reason": "INVALID_EXECUTABLE_PRICE", "edge": 0.0}

        if fair_probability <= 0 or fair_probability > 1.0:
            return {"approved": False, "reason": "INVALID_FAIR_PROBABILITY", "edge": 0.0}

        # 2. Estimación de costes totales
        total_costs = (spread / 2.0) + slippage + self.base_commission_pct
        
        # 3. Expectativa neta (Edge)
        edge = fair_probability - executable_price - total_costs

        if edge >= self.min_edge_margin:
            return {
                "approved": True,
                "reason": "PASSED_EDGE_THRESHOLD",
                "edge": round(edge, 4),
                "fair_probability": round(fair_probability, 4),
                "executable_price": round(executable_price, 4),
                "total_costs": round(total_costs, 4)
            }
        else:
            return {
                "approved": False,
                "reason": "INSUFFICIENT_EDGE",
                "edge": round(edge, 4),
                "fair_probability": round(fair_probability, 4),
                "executable_price": round(executable_price, 4),
                "total_costs": round(total_costs, 4)
            }
