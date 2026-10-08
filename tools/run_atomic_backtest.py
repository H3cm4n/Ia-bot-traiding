"""
tools/run_atomic_backtest.py
Backtest Atómico directo sobre data/candidate_journal.csv
"""

import os
import pandas as pd
import numpy as np

def run_backtest(journal_path: str = 'data/candidate_journal.csv'):
    if not os.path.exists(journal_path):
        print(f'[!] Archivo {journal_path} no encontrado.')
        return

    print('[i] Cargando candidate_journal.csv sanitizado...')
    df = pd.read_csv(journal_path)
    
    if len(df) == 0:
        print('[!] El archivo de diario está vacío.')
        return

    # Muestreo representativo para procesamiento veloz
    df_sample = df.sample(n=min(100000, len(df)), random_state=42).copy()
    print(f'[✓] Evaluando {len(df_sample)} registros seleccionados...')

    # Asignación de fair_probability con la fórmula Time-Aware si no está presente
    if 'fair_probability' not in df_sample.columns and 'binance_spot_price' in df_sample.columns and 'target_strike' in df_sample.columns:
        from services.time_aware_fair_value import TimeAwareFairValueCalculator
        calc = TimeAwareFairValueCalculator()
        
        probs = []
        for _, row in df_sample.iterrows():
            p = calc.calculate_fair_probability(
                spot_price=row['binance_spot_price'],
                strike_price=row['target_strike'],
                time_to_expiry_years=0.001,
                volatility_annualized=0.25
            )
            probs.append(p)
        df_sample['fair_probability'] = probs

    # Cálculo del Edge
    ask_col = 'best_ask' if 'best_ask' in df_sample.columns else 'price'
    if ask_col in df_sample.columns and 'fair_probability' in df_sample.columns:
        df_sample['edge'] = df_sample['fair_probability'] - df_sample[ask_col]
        positive_edges = df_sample[df_sample['edge'] > 0.05]

        print('====================================================')
        print('          RESULTADOS DEL BACKTEST ATÓMICO          ')
        print('====================================================')
        print(f' Total Registros Muestreados   : {len(df_sample)}')
        print(f' Oportunidades con Edge (> 5%) : {len(positive_edges)}')
        if len(positive_edges) > 0:
            print(f' Promedio de Edge Detectado    : {positive_edges["edge"].mean():.4f}')
        print('====================================================')

if __name__ == '__main__':
    run_backtest()
