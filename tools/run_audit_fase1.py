import os
import pandas as pd
import numpy as np

journal_path = 'data/candidate_journal.csv'

if not os.path.exists(journal_path):
    print(f"[!] Error: No se encontró {journal_path}")
    exit(1)

print("================================================================================")
print("             REPORT DE AUDITORÍA CUANTITATIVA - FASE 1 (EVALUACIÓN DE EDGE)     ")
print("================================================================================")

df = pd.read_csv(journal_path)
total_records = len(df)
print(f"[i] Muestra procesada: {total_records:,} candidatos/ticks")

# 1. Filtro de Datos Válidos
if 'binance_spot_price' in df.columns:
    df_valid = df[df['binance_spot_price'] > 0].copy()
else:
    df_valid = df.copy()

print(f"[i] Muestra válida (Spot Price > 0): {len(df_valid):,} registros")

# 2. Métricas de Distribución de Edge y Probabilidades
print("\n--------------------------------------------------------------------------------")
print("1. DISTRIBUCIÓN DE PROBABILIDADES Y EDGE CALCULADO")
print("--------------------------------------------------------------------------------")

if 'fair_probability' in df_valid.columns:
    mean_fair_prob = df_valid['fair_probability'].mean()
    std_fair_prob = df_valid['fair_probability'].std()
    print(f"* Probabilidad Fair Value (Modelo) Promedio : {mean_fair_prob:.4f} (± {std_fair_prob:.4f})")

if 'best_ask' in df_valid.columns:
    mean_ask = df_valid['best_ask'].mean()
    print(f"* Precio Implícito de Mercado (Best Ask)    : ${mean_ask:.4f}")

if 'fair_edge_to_ask' in df_valid.columns:
    edges = df_valid['fair_edge_to_ask'].dropna()
    positive_edges = edges[edges > 0]
    print(f"* Edge Teórico Promedio (fair_edge_to_ask)   : {edges.mean():.4f}")
    print(f"* Proporción con Edge Positivo (> 0)        : {(len(positive_edges)/len(edges))*100:.2f}%")
    print(f"* Percentiles de Edge [25%, 50%, 75%, 95%]   : {np.percentile(edges, [25, 50, 75, 95])}")

# 3. Análise de Decisiones por Criterio de Riesgo / Router
print("\n--------------------------------------------------------------------------------")
print("2. DESGLOSE DE DECISIONES DE TRADING (ROUTER & RISK)")
print("--------------------------------------------------------------------------------")

if 'crypto_decision' in df_valid.columns:
    decisions = df_valid['crypto_decision'].value_counts()
    print("Distribución por 'crypto_decision':")
    for dec, count in decisions.items():
        pct = (count / len(df_valid)) * 100
        print(f"  - {dec:<25}: {count:>8,} ({pct:>5.2f}%)")

if 'crypto_decision_reasons' in df_valid.columns:
    print("\nPrincipales Razones de Rechazo / No-Trade:")
    reasons = df_valid['crypto_decision_reasons'].value_counts().head(5)
    for reason, count in reasons.items():
        pct = (count / len(df_valid)) * 100
        print(f"  - {reason:<45}: {count:>8,} ({pct:>5.2f}%)")

# 4. Evaluación de Calidad de Entrada por Símbolo
print("\n--------------------------------------------------------------------------------")
print("3. DESGLOSE POR ACTIVO CRIPTO")
print("--------------------------------------------------------------------------------")

if 'crypto_symbol' in df_valid.columns:
    by_symbol = df_valid.groupby('crypto_symbol').agg(
        total_ticks=('observed_at', 'count'),
        mean_edge=('fair_edge_to_ask', 'mean'),
        avg_spread=('spread', 'mean')
    ).reset_index()
    
    print(f"{'SIMBOLO':<10} | {'SAMPLES':<10} | {'MEAN EDGE':<12} | {'AVG SPREAD':<10}")
    print("-" * 52)
    for _, row in by_symbol.iterrows():
        print(f"{str(row['crypto_symbol']):<10} | {row['total_ticks']:>10,} | {row['mean_edge']:>12.4f} | ${row['avg_spread']:>9.4f}")

print("\n================================================================================")
print("                     FIN DE DIAGNÓSTICO FASE 1                                 ")
print("================================================================================")
