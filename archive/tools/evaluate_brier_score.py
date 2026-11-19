"""
tools/evaluate_brier_score.py
Calcula cuantitativamente la calibración del modelo vs el mercado.
"""

import os
import pandas as pd
import numpy as np

def run_evaluation(csv_path: str = 'data/candidate_journal.csv'):
    if not os.path.exists(csv_path):
        print(f'[!] Archivo {csv_path} no encontrado para evaluación.')
        return

    df = pd.read_csv(csv_path)
    if 'actual_outcome' not in df.columns or df['actual_outcome'].isna().all():
        print('[!] No hay suficientes datos con resolución real (actual_outcome) para evaluar.')
        return

    df_valid = df.dropna(subset=['actual_outcome']).copy()
    y_true = df_valid['actual_outcome'].astype(float).values
    
    p_model = df_valid['fair_probability'].astype(float).values if 'fair_probability' in df.columns else df_valid['score'].astype(float).values
    p_market = df_valid['best_ask'].astype(float).values if 'best_ask' in df.columns else df_valid['price'].astype(float).values

    brier_model = np.mean((p_model - y_true) ** 2)
    brier_market = np.mean((p_market - y_true) ** 2)

    print('====================================================')
    print('          EVALUACIÓN DE BRIER SCORE (FASE 2)        ')
    print('====================================================')
    print(f' Muestras Evaluadas         : {len(df_valid)}')
    print(f' Brier Score Modelo         : {brier_model:.4f}')
    print(f' Brier Score Mercado        : {brier_market:.4f}')
    diff = brier_market - brier_model
    print(f' Mejora vs Mercado (Diff)   : {diff:+.4f} (Positivo = Modelo Superior)')
    print('====================================================')

if __name__ == '__main__':
    run_evaluation()
