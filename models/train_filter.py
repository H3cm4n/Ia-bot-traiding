"""
models/train_filter.py
Entrena un modelo de clasificación con el historial guardado en SQLite
para actualizar dinámicamente los criterios de filtrado de trades.
"""

import sqlite3
import joblib
from pathlib import Path

# Intentar importar scikit-learn
try:
    from sklearn.tree import DecisionTreeClassifier
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_bot.db"
MODEL_SAVE_PATH = Path(__file__).resolve().parent / "model_filter.pkl"

def train_model():
    if not HAS_SKLEARN:
        print("[-] scikit-learn no está instalado. Ejecuta: pip install scikit-learn")
        return False

    if not DB_PATH.exists():
        print("[-] No se encontró la base de datos.")
        return False

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT price, amount, pnl FROM orders_history WHERE pnl IS NOT NULL AND pnl != 0.0")
        rows = cursor.fetchall()

    if len(rows) < 5:
        print(f"[-] Insuficientes datos para entrenar (Se encontraron {len(rows)} registros con PnL). Mínimo 5 requeridos.")
        return False

    X = []
    y = []

    for price, amount, pnl in rows:
        # Característica clave: precio de entrada (fallback a 0.5 si es nulo)
        p_val = price if price is not None else 0.50
        X.append([p_val, amount])
        # Etiqueta: 1 si PnL positivo (Ganador), 0 si PnL negativo/cero (Perdedor)
        y.append(1 if pnl > 0 else 0)

    # Entrenar árbol de decisión simple para extraer reglas
    clf = DecisionTreeClassifier(max_depth=3)
    clf.fit(X, y)

    # Guardar modelo entrenado
    joblib.dump(clf, MODEL_SAVE_PATH)
    print(f"[+] Modelo entrenado con éxito sobre {len(rows)} ejemplos.")
    print(f"[+] Guardado en: {MODEL_SAVE_PATH}")
    return True

if __name__ == "__main__":
    train_model()
