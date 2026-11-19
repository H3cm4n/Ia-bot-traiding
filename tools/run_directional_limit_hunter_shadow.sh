#!/usr/bin/env bash
# tools/run_directional_limit_hunter_shadow.sh
# Runner v3.2: Liquidación -> Ejecución Integrada (Discovery, Guard, Kelly, Execution)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH=".:${PYTHONPATH:-}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python3}"

export DIRECTIONAL_DB_PATH="${DIRECTIONAL_DB_PATH:-$PROJECT_ROOT/data/trading_bot.db}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"

mkdir -p logs data database

echo "=== INICIANDO BOT DIRECTIONAL LIMIT HUNTER v3.2 (INTEGRADO) ==="
echo "DB_PATH=$DIRECTIONAL_DB_PATH | INTERVAL_SECONDS=$INTERVAL_SECONDS"

while true; do
    TIMESTAMP=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
    echo "--------------------------------------------------"
    echo "[$TIMESTAMP] Iniciando ciclo..."

    # PASO 1: Simulación de liquidación de órdenes abiertas
    echo "[$TIMESTAMP] [PASO 1/2] Liquidación de órdenes pendientes..."
    if $PYTHON_BIN tools/simulate_settlement.py; then
        echo "[$TIMESTAMP] Liquidación OK."
    else
        echo "[$TIMESTAMP] [WARN] Falló la liquidación; se continúa el ciclo."
    fi

    # PASO 2: Ejecución Integrada E2E
    # V3.2 ya no depende de stdin, realiza su propio Discovery y filtrado.
    echo "[$TIMESTAMP] [PASO 2/2] Ejecución integrada Discovery -> Guard -> Kelly -> Execution..."
    if $PYTHON_BIN tools/directional_limit_hunter_executor.py; then
        echo "[$TIMESTAMP] Ciclo de ejecución completado OK."
    else
        echo "[$TIMESTAMP] [ERROR] Falló la ejecución del ciclo integrado."
    fi

    echo "[$TIMESTAMP] Esperando ${INTERVAL_SECONDS} s..."
    sleep "$INTERVAL_SECONDS"
done
