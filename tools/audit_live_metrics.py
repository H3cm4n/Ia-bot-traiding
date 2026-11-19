"""
tools/audit_live_metrics.py
Reporte de auditoría y monitoreo de riesgo en vivo.

En lugar de filtrar por ventana temporal (WHERE ... >= datetime('now', '-1 day')),
se auditan las últimas 500 muestras registradas en cada tabla, usando
subconsultas con ORDER BY id DESC LIMIT 500. Esto evita falsos negativos
cuando no hay actividad reciente y garantiza un tamaño de muestra acotado.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/trading_bot.db")

# Tamaño de la ventana de muestreo (últimos N registros por tabla)
SAMPLE_LIMIT = 500


def audit_live_metrics():
    if not DB_PATH.exists():
        print(f"[!] No se encontró la base de datos en: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # 1. Candidatos rechazados: últimas SAMPLE_LIMIT muestras por razón
        cursor.execute(
            """
            SELECT reason, COUNT(*)
            FROM (
                SELECT reason
                FROM rejected_candidates
                ORDER BY id DESC
                LIMIT ?
            )
            GROUP BY reason
            """,
            (SAMPLE_LIMIT,),
        )
        rejected_summary = cursor.fetchall()

        # 2. Órdenes: últimas SAMPLE_LIMIT órdenes agrupadas por acción
        cursor.execute(
            """
            SELECT action, COUNT(*), SUM(pnl)
            FROM (
                SELECT action, pnl
                FROM orders_history
                ORDER BY id DESC
                LIMIT ?
            )
            GROUP BY action
            """,
            (SAMPLE_LIMIT,),
        )
        orders_summary = cursor.fetchall()

        # 3. Edge: últimas SAMPLE_LIMIT muestras con prob y precio válidos
        cursor.execute(
            """
            SELECT fair_probability, executable_price
            FROM (
                SELECT fair_probability, executable_price
                FROM rejected_candidates
                WHERE fair_probability IS NOT NULL
                  AND executable_price IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (SAMPLE_LIMIT,),
        )
        samples = cursor.fetchall()

    print("================================================================================")
    print(f"      REPORTE DE MONITOREO Y AUDITORÍA DE RIESGO (ÚLTIMAS {SAMPLE_LIMIT} MUESTRAS)   ")
    print("================================================================================")

    print(f"1. CANDIDATOS RECHAZADOS POR FILTRO (ÚLTIMAS {SAMPLE_LIMIT} MUESTRAS):")
    if not rejected_summary:
        print("  * Sin descartes registrados.")
    else:
        for reason, count in rejected_summary:
            print(f"  * {reason:<35}: {count} orden(es)")

    print(f"\n2. RESUMEN DE ÓRDENES Y PnL (ÚLTIMAS {SAMPLE_LIMIT} ÓRDENES):")
    if not orders_summary:
        print("  * Sin actividad de órdenes en la base de datos.")
    else:
        for action, count, total_pnl in orders_summary:
            pnl_val = total_pnl if total_pnl is not None else 0.0
            print(
                f"  * Estado: {action:<18} | Cantidad: {count:<4} | "
                f"PnL Acumulado: ${pnl_val:+.2f} USDC"
            )

    print(f"\n3. EVALUACIÓN DE DISCREPANCIA (EDGE) EN ÚLTIMAS {SAMPLE_LIMIT} MUESTRAS:")
    total_samples = len(samples)
    if total_samples == 0:
        print("  * Sin muestras de discrepancia evaluadas.")
    else:
        avg_prob = sum(s[0] for s in samples) / total_samples
        avg_price = sum(s[1] for s in samples) / total_samples
        avg_edge = avg_prob - avg_price

        print(f"  * Muestras Evaluadas                 : {total_samples}")
        print(f"  * Probabilidad Media Modelo          : {avg_prob:.4f}")
        print(f"  * Precio Ejecutable Medio (Mercado)  : {avg_price:.4f}")
        print(f"  * Edge Promedio Capturado            : {avg_edge:+.4f}")
    print("================================================================================")


if __name__ == "__main__":
    audit_live_metrics()