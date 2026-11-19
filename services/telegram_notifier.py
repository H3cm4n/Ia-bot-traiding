"""
services/telegram_notifier.py
Servicio de notificaciones en tiempo real vía Telegram API.
"""

import logging
import sqlite3
import urllib.error
import urllib.request
import json
from pathlib import Path
from typing import Dict, Any, Optional

from config import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("telegram_notifier")


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id and not self.token.startswith("123456789"))

        if not self.enabled:
            logger.info("[TELEGRAM] Notificador desactivado (Faltan credenciales).")

    def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            logger.warning(f"[TELEGRAM] Error HTTP al enviar mensaje: {e}")
        except Exception as e:
            logger.warning(f"[TELEGRAM] Error inesperado: {e}")

        return False

    @staticmethod
    def _format_whale_status(order_data: Dict[str, Any]) -> str:
        alignment = str(order_data.get("whale_alignment") or "NEUTRAL").upper()
        yes_usd = float(order_data.get("whale_yes_usd") or 0.0)
        no_usd = float(order_data.get("whale_no_usd") or 0.0)
        net_flow = order_data.get("whale_net_flow_usd")
        net_flow = float(net_flow) if net_flow is not None else (yes_usd - no_usd)

        if net_flow > 0:
            flow = "YES"
        elif net_flow < 0:
            flow = "NO"
        else:
            flow = "NEUTRAL"

        return (
            f"Ballenas: {alignment} | Flujo: {flow} | "
            f"YES ${yes_usd:,.0f} / NO ${no_usd:,.0f}"
        )

    def notify_order_placed(self, order_data: Dict[str, Any]) -> bool:
        match_key = str(order_data.get("match_key", "N/A"))
        symbol = str(order_data.get("symbol", "CRYPTO"))
        outcome = str(order_data.get("outcome", "YES"))
        price = float(order_data.get("limit_price", order_data.get("price", 0.0)))
        amount = float(order_data.get("amount", 10.0))
        prob = float(order_data.get("fair_probability", 0.0))

        potential_return = order_data.get("potential_return")
        if potential_return is None:
            potential_return = amount * (1.0 - price) / price if price > 0 else 0.0
        potential_return = float(potential_return)
        roi_pct = (potential_return / amount * 100.0) if amount > 0 else 0.0

        lines = [
            "🟢 NUEVA ORDEN EJECUTADA",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Activo: {symbol} [{outcome}]",
            f"Ask: ${price:.4f}",
            f"Fair: {prob * 100:.1f}%",
            f"Size USD: ${amount:.2f}",
            f"Retorno potencial: +${potential_return:.2f} ({roi_pct:.1f}%)",
            self._format_whale_status(order_data),
            f"ID: {match_key[:16]}...",
        ]
        return self.send_message("\n".join(lines))

    def notify_exit_triggered(self, exit_data: Dict[str, Any]) -> bool:
        reason = str(exit_data.get("reason", "EXIT"))
        match_key = str(exit_data.get("match_key", "N/A"))
        pnl_pct = float(exit_data.get("pnl_pct", 0.0))
        pnl_usdc = float(exit_data.get("pnl_usdc", 0.0))

        emoji = "🎯" if "PROFIT" in reason else ("🛑" if "LOSS" in reason else "⚠️")

        lines = [
            f"{emoji} SALIDA DE POSICION ({reason})",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🔑 ID Orden: {match_key[:16]}...",
            f"📈 Retorno (%): {pnl_pct:+.2f}%",
            f"💵 PnL Realizado: ${pnl_usdc:+.2f} USDC",
        ]
        return self.send_message("\n".join(lines))

    def get_stats(self, db_path: Optional[str | Path] = None) -> Dict[str, Any]:
        """Resumen de rendimiento calculado directamente desde SQLite."""
        target = Path(db_path) if db_path else DB_PATH
        stats = {
            "net_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "win_rate": 0.0,
            "day_net_pnl": 0.0,
            "open_positions": 0,
            "settled_trades": 0,
        }

        if not target.exists():
            return stats

        try:
            with sqlite3.connect(target) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN action = 'WIN' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN action = 'LOSS' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN action = 'WIN' THEN pnl ELSE 0 END), 0.0),
                        COALESCE(SUM(CASE WHEN action = 'LOSS' THEN pnl ELSE 0 END), 0.0),
                        COALESCE(SUM(CASE WHEN action IN ('WIN', 'LOSS') THEN pnl ELSE 0 END), 0.0)
                    FROM orders_history
                    """
                )
                wins, losses, gross_profit, gross_loss, net_pnl = cur.fetchone()

                cur.execute(
                    """
                    SELECT COALESCE(SUM(pnl), 0.0)
                    FROM orders_history
                    WHERE action IN ('WIN', 'LOSS')
                      AND date(timestamp) = date('now')
                    """
                )
                day_net_pnl = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT COUNT(DISTINCT o.match_key)
                    FROM orders_history o
                    WHERE o.action IN ('ENTRY', 'FILL', 'PENDING_CREATED', 'PAPER_FILLED')
                      AND NOT EXISTS (
                        SELECT 1 FROM orders_history c
                        WHERE c.match_key = o.match_key
                          AND c.action IN ('CLOSED', 'EXIT', 'WIN', 'LOSS', 'CANCELED')
                      )
                    """
                )
                open_positions = cur.fetchone()[0]

                settled = wins + losses
                stats.update(
                    {
                        "net_pnl": float(net_pnl or 0.0),
                        "wins": int(wins or 0),
                        "losses": int(losses or 0),
                        "gross_profit": float(gross_profit or 0.0),
                        "gross_loss": float(gross_loss or 0.0),
                        "win_rate": (wins / settled * 100.0) if settled else 0.0,
                        "day_net_pnl": float(day_net_pnl or 0.0),
                        "open_positions": int(open_positions or 0),
                        "settled_trades": int(settled),
                    }
                )
        except Exception as e:
            logger.warning(f"[TELEGRAM] No se pudieron calcular estadísticas: {e}")

        return stats

    def notify_trade_settled(
        self,
        settlement_data: Dict[str, Any],
        db_path: Optional[str | Path] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> bool:
        action = str(
            settlement_data.get("action") or settlement_data.get("result") or "SETTLED"
        ).upper()
        pnl = float(settlement_data.get("pnl", 0.0))
        symbol = str(settlement_data.get("symbol", "CRYPTO"))
        side = str(settlement_data.get("side", "N/A"))
        match_key = str(settlement_data.get("match_key", "N/A"))
        stats = stats or self.get_stats(db_path)

        lines = [
            f"LIQUIDACION {action}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Activo: {symbol} [{side}]",
            f"ID: {match_key[:16]}...",
            f"PnL Operacion: ${pnl:+.2f} USDC",
            f"Win Rate acumulado: {stats['win_rate']:.2f}% "
            f"({stats['wins']}W/{stats['losses']}L)",
            f"PnL Neto Hoy: ${stats['day_net_pnl']:+.2f} USDC",
        ]
        return self.send_message("\n".join(lines))

    def build_stats_message(self, db_path: Optional[str | Path] = None) -> str:
        stats = self.get_stats(db_path)
        return "\n".join(
            [
                "ESTADO DEL BOT",
                "━━━━━━━━━━━━━━━━━━━━",
                f"PnL Neto: ${stats['net_pnl']:+.2f} USDC",
                f"Ganadas vs. Perdidas: {stats['wins']}W / {stats['losses']}L",
                f"Ganancia Bruta: ${stats['gross_profit']:+.2f} USDC",
                f"Perdida Bruta: ${abs(stats['gross_loss']):.2f} USDC",
                f"Win Rate: {stats['win_rate']:.2f}%",
                f"Posiciones abiertas: {stats['open_positions']}",
                f"PnL Neto Hoy: ${stats['day_net_pnl']:+.2f} USDC",
            ]
        )

    def handle_command(
        self, text: str, db_path: Optional[str | Path] = None
    ) -> Optional[str]:
        if not text:
            return None

        command = text.strip().split()[0].lower()
        if command in ("/stats", "/balance", "/status"):
            return self.build_stats_message(db_path)
        return None

    def send_command_response(
        self, text: str, db_path: Optional[str | Path] = None
    ) -> bool:
        response = self.handle_command(text, db_path)
        if response is None:
            return False
        return self.send_message(response)
