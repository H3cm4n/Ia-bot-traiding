"""
tools/evaluate_calibration.py
Evaluador de Brier Score y Log Loss sobre las muestras liquidadas en SQLite.
"""
import sys
from pathlib import Path

# Añadir raíz del proyecto al sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
import numpy as np
import pandas as pd
from config import DB_PATH, DEFAULT_FAIR_PROB

def evaluate_calibration():
    if not DB_PATH.exists():
        print(f"[!] No se encontró la base de datos en: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        query = """
            SELECT symbol, side, price, action, pnl
            FROM orders_history
            WHERE action IN ('WIN', 'LOSS')
        """
        df = pd.read_sql_query(query, conn)

    if df.empty:
        print("[!] No hay suficientes operaciones liquidadas en la base de datos.")
        return

    df['y_true'] = df['action'].apply(lambda x: 1.0 if x == 'WIN' else 0.0)
    df['p_model'] = DEFAULT_FAIR_PROB
    df['p_market'] = df['price'].astype(float)

    y_true = df['y_true'].values
    p_model = df['p_model'].values
    p_market = df['p_market'].values

    # 1. Brier Score
    brier_model = np.mean((p_model - y_true) ** 2)
    brier_market = np.mean((p_market - y_true) ** 2)

    # 2. Log Loss
    eps = 1e-15
    p_m_clipped = np.clip(p_model, eps, 1 - eps)
    log_loss_model = -np.mean(y_true * np.log(p_m_clipped) + (1 - y_true) * np.log(1 - p_m_clipped))

    print("=================================================================")
    print("         EVALUADOR DE CALIBRACIÓN PROBABILÍSTICA (ML)            ")
    print("=================================================================")
    print(f" Muestras Liquidadas Evaluadas : {len(df)}")
    print(f" Brier Score Modelo           : {brier_model:.4f}")
    print(f" Brier Score Mercado          : {brier_market:.4f}")
    print(f" Ventaja vs Mercado (Diff)    : {(brier_market - brier_model):+.4f} (Positivo = Modelo Superior)")
    print(f" Log Loss Modelo              : {log_loss_model:.4f}")
    print("=================================================================")

if __name__ == "__main__":
    evaluate_calibration()