"""
web/dashboard_app.py
Servidor HTTP ligero (solo lectura) para el dashboard de rendimiento.
"""

import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.dashboard_metrics import get_performance

logger = logging.getLogger("dashboard_app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

TEMPLATE_PATH = PROJECT_ROOT / "templates" / "dashboard.html"


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DashboardHTTP/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/performance":
            query = parse_qs(parsed.query)
            window = query.get("window", ["all"])[0]
            try:
                payload = get_performance(
                    window, db_path=getattr(self.server, "db_path", None)
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception:
                logger.exception("Error calculando performance")
                self._send_json(500, {"error": "internal_error"})
                return

            self._send_json(200, payload)
            return

        if path in ("/", "/dashboard", "/index.html"):
            if not TEMPLATE_PATH.exists():
                self._send_json(500, {"error": "dashboard_template_missing"})
                return
            self._send_html(200, TEMPLATE_PATH.read_bytes())
            return

        self._send_json(404, {"error": "not_found"})

    def log_message(self, format: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), format % args)


def create_server(
    host: str = "127.0.0.1", port: int = 8000, db_path: str | Path | None = None
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.db_path = db_path
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard HTTP de solo lectura")
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8000"))
    )
    parser.add_argument("--db", default=os.getenv("DASHBOARD_DB_PATH"))
    args = parser.parse_args()

    server = create_server(args.host, args.port, db_path=args.db)
    logger.info("Dashboard disponible en http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Deteniendo dashboard")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
