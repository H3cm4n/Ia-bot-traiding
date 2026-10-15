#!/usr/bin/env bash
# tools/run_directional_limit_hunter_shadow.sh
# Runner en bucle v2.2: Liquidación -> Auto-entrenamiento ML (SQLite)
#                      -> Ingesta + Inferencia (snapshot por archivo temporal)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH=".:${PYTHONPATH:-}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python3}"

# --- Ruta única de la DB para liquidación, entrenamiento e inferencia ---
export DIRECTIONAL_DB_PATH="${DIRECTIONAL_DB_PATH:-$PROJECT_ROOT/database/trading.db}"

# --- Guardas del ciclo de entrenamiento ---
MIN_SETTLED_SAMPLES="${MIN_SETTLED_SAMPLES:-50}"   # muestras mínimas para re-entrenar
INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"

mkdir -p logs
LOG_FILE="logs/directional_limit_hunter_v2_$(date -u +'%Y%m%dT%H%M%SZ').log"
echo "=== INICIANDO BOT DIRECTIONAL LIMIT HUNTER v2.2 ===" | tee -a "$LOG_FILE"
echo "DB_PATH=$DIRECTIONAL_DB_PATH | MIN_SETTLED_SAMPLES=$MIN_SETTLED_SAMPLES" | tee -a "$LOG_FILE"

# Cuenta órdenes liquidadas (WIN/LOSS) directamente en SQLite.
count_settled_orders() {
    DIRECTIONAL_DB_PATH="$DIRECTIONAL_DB_PATH" "$PYTHON_BIN" - <<'PY'
import os, sqlite3, sys
db = os.environ["DIRECTIONAL_DB_PATH"]
if not os.path.exists(db):
    print(0); sys.exit(0)
try:
    con = sqlite3.connect(db)
    cur = con.cursor()
    # Ajusta tabla/columna si tu schema usa otros nombres.
    cur.execute("SELECT COUNT(*) FROM orders WHERE status IN ('WIN','LOSS')")
    row = cur.fetchone()
    print(int(row[0]) if row and row[0] is not None else 0)
except Exception as e:
    print(0, file=sys.stderr)
    print(0)
PY
}

while true; do
    TIMESTAMP=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
    echo "--------------------------------------------------" | tee -a "$LOG_FILE"
    echo "[$TIMESTAMP] Iniciando ciclo..." | tee -a "$LOG_FILE"

    # -------------------------------------------------------------------
    # 1. LIQUIDACIÓN: PENDING_CREATED -> WIN/LOSS en SQLite
    # -------------------------------------------------------------------
    echo "[$TIMESTAMP] [PASO 1/3] Liquidación de órdenes pendientes..." | tee -a "$LOG_FILE"
    if DIRECTIONAL_DB_PATH="$DIRECTIONAL_DB_PATH" \
       "$PYTHON_BIN" tools/simulate_settlement.py >> "$LOG_FILE" 2>&1; then
        echo "[$TIMESTAMP] Liquidación OK." | tee -a "$LOG_FILE"
    else
        echo "[$TIMESTAMP] [WARN] Falló la liquidación; se continúa el ciclo." | tee -a "$LOG_FILE"
    fi

    # -------------------------------------------------------------------
    # 2. AUTO-ENTRENAMIENTO ML sobre las órdenes liquidadas en SQLite
    # -------------------------------------------------------------------
    SETTLED_COUNT="$(count_settled_orders || echo 0)"
    echo "[$TIMESTAMP] [PASO 2/3] Muestras liquidadas en SQLite: $SETTLED_COUNT (mínimo $MIN_SETTLED_SAMPLES)" \
        | tee -a "$LOG_FILE"

    if [ "${SETTLED_COUNT:-0}" -ge "$MIN_SETTLED_SAMPLES" ]; then
        if DIRECTIONAL_DB_PATH="$DIRECTIONAL_DB_PATH" \
           "$PYTHON_BIN" models/train_directional_model.py \
                --db "$DIRECTIONAL_DB_PATH" \
                --only-settled \
                >> "$LOG_FILE" 2>&1; then
            echo "[$TIMESTAMP] Modelo ML re-entrenado con $SETTLED_COUNT muestras liquidadas." \
                | tee -a "$LOG_FILE"
        else
            echo "[$TIMESTAMP] [WARN] Falló el re-entrenamiento; se conserva el modelo previo." \
                | tee -a "$LOG_FILE"
        fi
    else
        echo "[$TIMESTAMP] [INFO] Datos insuficientes para re-entrenar; se conserva el modelo actual." \
            | tee -a "$LOG_FILE"
    fi

    # -------------------------------------------------------------------
    # 3. INGESTA + INFERENCIA ML + FILTRADO EV/PRECIO
    # -------------------------------------------------------------------
    echo "[$TIMESTAMP] [PASO 3/3] Snapshot + inferencia + filtrado..." | tee -a "$LOG_FILE"

    # Snapshot intermedio en archivo temporal único (evita colisiones entre
    # instancias y garantiza atomicidad entre el filtro y el ejecutor).
    TEMP_SNAPSHOT_JSON="$(mktemp -t directional_snapshot_XXXXXX.json)"
    trap 'rm -f "$TEMP_SNAPSHOT_JSON"' EXIT

    # 3.a) Generar el snapshot filtrado en el archivo temporal.
    if "$PYTHON_BIN" tools/filter_directional_limit_hunter_snapshot.py \
           > "$TEMP_SNAPSHOT_JSON" 2>> "$LOG_FILE"; then

        # 3.b) Sólo ejecutar si el snapshot contiene datos.
        if [ -s "$TEMP_SNAPSHOT_JSON" ]; then
            if DIRECTIONAL_DB_PATH="$DIRECTIONAL_DB_PATH" \
               "$PYTHON_BIN" tools/directional_limit_hunter_executor.py \
                   < "$TEMP_SNAPSHOT_JSON" >> "$LOG_FILE" 2>&1; then
                echo "[$TIMESTAMP] Snapshot procesado correctamente." | tee -a "$LOG_FILE"
            else
                echo "[$TIMESTAMP] [ERROR] Falló la ejecución del snapshot." | tee -a "$LOG_FILE"
            fi
        else
            echo "[$TIMESTAMP] [INFO] Snapshot vacío tras filtrado; se omite el ejecutor." | tee -a "$LOG_FILE"
        fi
    else
        echo "[$TIMESTAMP] [ERROR] Falló la generación del snapshot filtrado." | tee -a "$LOG_FILE"
    fi

    rm -f "$TEMP_SNAPSHOT_JSON"
    trap - EXIT

    echo "[$TIMESTAMP] Esperando $INTERVAL_SECONDS s..." | tee -a "$LOG_FILE"
    sleep "$INTERVAL_SECONDS"
done