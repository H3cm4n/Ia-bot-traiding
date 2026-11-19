"""
tools/filter_directional_limit_hunter_snapshot.py
Filtro v1.8 para candidatos Directional Limit Hunter.

- Modo dual: lee stdin si trae datos (pipe/archivo) o ingesta directa vía
  fetch_diversified_markets (+ filter_snapshot de predictive_filter si existe).
- Normalización defensiva de campos (condition_id, symbol, limit_price)
  antes de pasar los candidatos al filtro.
- Detección robusta de stdin: valida descriptor (fileno) antes de select()
  para tolerar fds cerrados en entornos no interactivos (cron/systemd).
- Filtra por spread relativo, score y fair_edge.
- Emisión defensiva de stdout/stderr: tolera fds cerrados, BrokenPipeError
  y ejecución bajo cron/systemd/redirección de subprocesos.
"""

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.orderbook import calculate_relative_spread

logger = logging.getLogger("directional_snapshot_filter")


# ---------------------------------------------------------------------------
# Emisión defensiva (Bad file descriptor / BrokenPipe / streams cerrados)
# ---------------------------------------------------------------------------
def _safe_write_stderr(msg: str) -> None:
    """Escribe en stderr ignorando fallos de fd cerrado o stream inválido."""
    try:
        if sys.stderr is None or getattr(sys.stderr, "closed", False):
            return
        sys.stderr.write(msg)
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        # OSError errno 9 (EBADF), ValueError si el stream ya fue cerrado,
        # AttributeError si sys.stderr es None.
        pass


def _safe_print_stdout(payload: str) -> None:
    """Escribe payload + newline en stdout, tolerando fds cerrados."""
    try:
        if sys.stdout is None or getattr(sys.stdout, "closed", False):
            return
        sys.stdout.write(payload)
        if not payload.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # El consumidor (ejecutor) cerró el pipe. Silenciar y desvincular
        # stdout para que el intérprete no intente hacer flush al salir.
        try:
            import os
            devnull = open("/dev/null", "w")
            os.dup2(devnull.fileno(), sys.stdout.fileno())
        except Exception:
            pass
    except (OSError, ValueError, AttributeError):
        pass


def _emit_empty() -> None:
    _safe_print_stdout("[]")


# ---------------------------------------------------------------------------
# Filtrado
# ---------------------------------------------------------------------------
def filter_candidates(
    snapshot_data: list,
    min_score: float = 65.0,
    min_edge: float = 0.20,
    max_rel_spread: float = 0.08,
) -> list:
    candidates = []

    for item in snapshot_data:
        if not isinstance(item, dict):
            continue

        best_bid = item.get("best_bid", 0.0)
        best_ask = item.get("best_ask", 1.0)
        score = item.get("score", 0.0)
        edge = item.get("fair_edge_to_ask", item.get("fair_edge", 0.0))
        decision = item.get("crypto_decision", item.get("decision", ""))

        if decision not in ["CRYPTO_BUY_FAIR_EDGE", "CRYPTO_WAIT_ENTRY_ASK_TOO_HIGH"]:
            continue

        if score < min_score or edge < min_edge:
            continue

        rel_spread = calculate_relative_spread(best_bid, best_ask)
        if rel_spread > max_rel_spread:
            continue

        item["limit_price"] = round(min(best_ask, 0.80), 4)
        item["relative_spread"] = rel_spread
        candidates.append(item)

    return candidates


# ---------------------------------------------------------------------------
# Ingesta (modo dual)
# ---------------------------------------------------------------------------
def _stdin_has_data() -> bool:
    """
    True sólo si stdin es un pipe/archivo válido con datos disponibles AHORA.
    """
    try:
        if sys.stdin is None or getattr(sys.stdin, "closed", False):
            return False
        if sys.stdin.isatty():
            return False

        # Verificar descriptor de archivo válido
        try:
            sys.stdin.fileno()
        except (OSError, ValueError):
            return False

        # Non-TTY: comprobar con select()
        import select
        r, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(r)
    except Exception:
        return False


def _load_raw_candidates() -> list:
    # --- Modo 1: stdin tiene datos (tests manuales/debugging) ---
    if _stdin_has_data():
        try:
            raw = sys.stdin.read()
            if raw and raw.strip():
                return json.loads(raw)
        except (OSError, ValueError):
            # Si el descriptor es inválido (Errno 9), silenciar e ir al modo directo
            pass
        except Exception as e:
            _safe_write_stderr(f"[FILTER] Error leyendo stdin: {e}\n")

    # --- Modo 2: ingesta directa (uso en el runner en segundo plano) ---
    from connectors.polymarket_ingest import fetch_diversified_markets

    raw_candidates = fetch_diversified_markets() or []

    # Normalización defensiva de candidatos
    normalized = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue

        cond_id = (
            item.get("condition_id")
            or item.get("conditionId")
            or item.get("id")
        )
        sym = (
            item.get("symbol")
            or item.get("question")
            or item.get("title")
            or "MERCADO_GENERICO"
        )

        raw_price = item.get("limit_price")
        if raw_price is None:
            raw_price = item.get("price")
        if raw_price is None:
            raw_price = item.get("best_ask")

        try:
            price = float(raw_price) if raw_price is not None else 0.50
        except (TypeError, ValueError):
            price = 0.50

        item["condition_id"] = cond_id
        item["symbol"] = sym
        item["limit_price"] = price
        normalized.append(item)

    try:
        from models.predictive_filter import filter_snapshot
        return filter_snapshot(normalized)
    except Exception as e:
        logger.warning("filter_snapshot no disponible, se omite: %s", e)
        return normalized


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def _main() -> int:
    try:
        raw_candidates = _load_raw_candidates()
        filtered = filter_candidates(raw_candidates)
        _safe_print_stdout(json.dumps(filtered))
        return 0
    except Exception as e:
        # Un único punto de captura para toda la cadena: ingesta + filtrado + emisión.
        _safe_write_stderr(f"Error filtrando snapshot: {e}\n")
        _emit_empty()
        return 0


if __name__ == "__main__":
    # Cerrar limpiamente ante SIGPIPE en plataformas POSIX: si el lector
    # cierra el pipe, el proceso recibe SIGPIPE y muere sin traceback.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass

    sys.exit(_main())