import os
import sqlite3
import pandas as pd
from config import DB_PATH

def generate_report():
    if not os.path.exists(DB_PATH):
        print(f"[!] Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # 1. Resumen de datos sintéticos/históricos
    df_synthetic = pd.read_sql_query(
        "SELECT action, COUNT(*) as cantidad, SUM(pnl) as pnl_total FROM orders_history WHERE action = 'SYNTHETIC_SETTLED' GROUP BY action;",
        conn
    )
    
    # 2. Resumen de paper trading real
    df_real = pd.read_sql_query(
        "SELECT action, COUNT(*) as cantidad, SUM(pnl) as pnl_total FROM orders_history WHERE action IN ('WIN', 'LOSS') GROUP BY action;",
        conn
    )
    
    # 3. Órdenes abiertas/pendientes
    df_pending = pd.read_sql_query(
        "SELECT action, COUNT(*) as cantidad FROM orders_history WHERE action IN ('PENDING_CREATED', 'OPEN') GROUP BY action;",
        conn
    )

    print("=" * 55)
    print("       REPORTE DE RENDIMIENTO REAL POLYMARKET (v2.0)")
    print("=" * 55)
    
    print("\n--- POSICIONES EN PAPER TRADING REAL ---")
    if df_real.empty:
        print("No hay trades reales liquidados aún (esperando resolución de mercado).")
    else:
        print(df_real.to_string(index=False))
        wins = df_real[df_real['action'] == 'WIN']['cantidad'].sum() if 'WIN' in df_real['action'].values else 0
        total = df_real['cantidad'].sum()
        win_rate = (wins / total * 100) if total > 0 else 0.0
        pnl_real = df_real['pnl_total'].sum()
        print(f"\nWin Rate Real: {win_rate:.2f}%")
        print(f"PnL Real Acumulado: ${pnl_real:.2f} USDC")

    print("\n--- ÓRDENES PENDIENTES / ABIERTAS ---")
    if df_pending.empty:
        print("0 órdenes pendientes.")
    else:
        print(df_pending.to_string(index=False))

    print("\n--- HISTORIAL SINTÉTICO (ARCHIVADO) ---")
    if not df_synthetic.empty:
        print(f"Órdenes sintéticas previa desinfección: {df_synthetic['cantidad'].sum()}")
    print("=" * 55)
    conn.close()

if __name__ == '__main__':
    generate_report()


from tools.compare_whales import print_whale_comparison_report
print_whale_comparison_report()
