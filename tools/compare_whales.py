"""
tools/compare_whales.py
Reporte comparativo entre señales del bot y actividad de ballenas (Smart Money).
"""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"


def print_whale_comparison_report(db_path: str | None = None) -> None:
    target_db = Path(db_path or os.getenv("DIRECTIONAL_DB_PATH", DEFAULT_DB))
    print("\n" + "=" * 78)
    print("WHALE INTELLIGENCE: COMPARACIÓN BOT VS. PECES GORDOS")
    print("=" * 78)

    if not target_db.exists():
        print(f"Base de datos no encontrada en {target_db}")
        return

    with sqlite3.connect(target_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whale_comparisons'")
        if not cur.fetchone():
            print("Tabla whale_comparisons aún no inicializada (sin ejecuciones registradas).")
            return

        cur.execute("""
            SELECT timestamp, symbol, bot_side, bot_fair_prob, market_price,
                   whale_yes_usd, whale_no_usd, whale_alignment, action_taken
            FROM whale_comparisons
            ORDER BY id DESC
            LIMIT 15
        """)
        recent = cur.fetchall()

        if not recent:
            print("Sin comparaciones de ballenas registradas todavía.")
            return

        print("\nÚltimas 15 evaluaciones Bot vs. Ballenas:")
        print(f"{'Timestamp':<20} {'Sym':<5} {'Side':<4} {'Fair':>6} {'Ask':>6} {'Whale YES':>10} {'Whale NO':>10} {'Alignment':<12} {'Action'}")
        print("-" * 92)
        for r in recent:
            ts = str(r[0])[:19]
            print(f"{ts:<20} {str(r[1]):<5} {str(r[2]):<4} {r[3]:>6.3f} {r[4]:>6.3f} ${r[5]:>9,.0f} ${r[6]:>9,.0f} {str(r[7]):<12} {r[8]}")

        cur.execute("""
            WITH latest_signal AS (
                SELECT condition_id, bot_side, whale_alignment
                FROM whale_comparisons
                WHERE IFNULL(action_taken, '') != 'REJECTED: WHALE_STRONG_OPPOSITION'
                  AND id IN (
                    SELECT MAX(id)
                    FROM whale_comparisons
                    WHERE IFNULL(action_taken, '') != 'REJECTED: WHALE_STRONG_OPPOSITION'
                    GROUP BY condition_id, bot_side
                )
            ),
            signal_counts AS (
                SELECT whale_alignment, COUNT(*) AS total_signals
                FROM whale_comparisons
                GROUP BY whale_alignment
            ),
            settled_pnl AS (
                SELECT
                    ls.whale_alignment,
                    COUNT(DISTINCT CASE WHEN o.action = 'WIN' THEN o.id END) AS wins,
                    COUNT(DISTINCT CASE WHEN o.action = 'LOSS' THEN o.id END) AS losses,
                    COALESCE(SUM(o.pnl), 0.0) AS net_pnl
                FROM latest_signal ls
                JOIN orders_history o
                    ON o.match_key LIKE (ls.condition_id || '_' || ls.bot_side || '_%')
                    AND o.action IN ('WIN', 'LOSS')
                GROUP BY ls.whale_alignment
            )
            SELECT
                sc.whale_alignment,
                sc.total_signals,
                COALESCE(sp.wins, 0) AS wins,
                COALESCE(sp.losses, 0) AS losses,
                COALESCE(sp.net_pnl, 0.0) AS net_pnl
            FROM signal_counts sc
            LEFT JOIN settled_pnl sp ON sp.whale_alignment = sc.whale_alignment
            ORDER BY sc.total_signals DESC
        """)
        summary = cur.fetchall()

        print("\nRendimiento Histórico por Alineación con Ballenas:")
        print(f"{'Alineación':<32} {'Señales':>8} {'Wins':>6} {'Losses':>7} {'WinRate':>9} {'PnL Neto':>11}")
        print("-" * 78)
        for align, total, wins, losses, pnl in summary:
            settled = (wins or 0) + (losses or 0)
            wr = f"{(wins / settled * 100):.1f}%" if settled > 0 else "N/A"
            print(f"{str(align):<32} {total:>8} {wins or 0:>6} {losses or 0:>7} {wr:>9} ${pnl:>10.2f}")


if __name__ == "__main__":
    print_whale_comparison_report()
