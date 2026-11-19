import os
import pandas as pd

input_path = 'data/candidate_journal.csv'
output_path = 'data/candidate_journal_summary.csv'

if not os.path.exists(input_path):
    print(f'[!] No se encontró {input_path}')
    exit(1)

print('[i] Procesando candidate_journal.csv...')
df = pd.read_csv(input_path)

if 'binance_spot_price' in df.columns:
    df = df[df['binance_spot_price'] > 0]

col_question = 'question' if 'question' in df.columns else df.columns[0]
col_model = 'fair_probability' if 'fair_probability' in df.columns else 'score'
col_market = 'best_ask' if 'best_ask' in df.columns else 'best_bid'
col_outcome = 'actual_outcome' if 'actual_outcome' in df.columns else 'outcome'

agg_dict = {'total_samples': (col_question, 'count')}
if col_model in df.columns:
    agg_dict['mean_model_prob'] = (col_model, 'mean')
if col_market in df.columns:
    agg_dict['mean_market_prob'] = (col_market, 'mean')
if col_outcome in df.columns:
    agg_dict['actual_resolution'] = (col_outcome, 'last')

summary = df.groupby(col_question).agg(**agg_dict).reset_index()
summary.to_csv(output_path, index=False)
print(f'[✓] Resumen actualizado en {output_path} ({os.path.getsize(output_path) / 1024:.2f} KB)')
