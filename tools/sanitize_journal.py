"""
tools/sanitize_journal.py
Filtra y remueve registros corruptos o incompletos del candidate_journal.csv.
"""

import os
import pandas as pd

def sanitize_journal(file_path: str = 'data/candidate_journal.csv'):
    if not os.path.exists(file_path):
        print(f'[!] Archivo {file_path} no existe.')
        return

    df = pd.read_csv(file_path)
    initial_count = len(df)

    # Reglas de sanitización
    # 1. Spot de Binance debe ser válido (> 0)
    if 'binance_spot_price' in df.columns:
        df = df[df['binance_spot_price'] > 0]

    # 2. Strike o objetivo debe ser válido (> 0)
    if 'target_strike' in df.columns:
        df = df[df['target_strike'] > 0]

    # 3. Eliminar duplicados exactos por timestamp y mercado
    subset_cols = [c for c in ['timestamp', 'condition_id', 'match_key'] if c in df.columns]
    if subset_cols:
        df = df.drop_duplicates(subset=subset_cols)

    final_count = len(df)
    df.to_csv(file_path, index=False)
    print(f'[✓] Sanitización completada: {initial_count - final_count} filas descartadas. {final_count} registros válidos.')

if __name__ == '__main__':
    sanitize_journal()
