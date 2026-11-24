"""Dependency-free HTTP server for the pointwise delivery decision app."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

if __package__ in (None, ""):  # Support `python app/server.py` as well.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.engine import analyze_dataset, apply_measurement, enumeration_check, recommend_measurements
    from app.engine.analysis import DatasetError
else:
    from .engine import analyze_dataset, apply_measurement, enumeration_check, recommend_measurements
    from .engine.analysis import DatasetError

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SEED_PATH = BASE_DIR / "data" / "seed.json"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def load_seed() -> Dict[str, Any]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PointwiseDelivery/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep test output readable; errors are still returned to the client.
        if os.environ.get("APP_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, relative: str) -> None:
        if relative in ("", "/"):
            path = WEB_DIR / "index.html"
        else:
            safe = relative.lstrip("/")
            path = (WEB_DIR / safe).resolve()
            if WEB_DIR.resolve() not in path.parents and path != WEB_DIR.resolve():
                self.send_error(403)
                return
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 10_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                self._send_json({"ok": True, "service": "pointwise-delivery"})
            elif path == "/api/seed":
                self._send_json(load_seed())
            elif path == "/api/template":
                self._send_json(load_seed())
            elif path.startswith("/api/"):
                self._send_json({"error": "not_found", "path": path}, 404)
            else:
                self._send_static(path)
        except Exception as exc:  # pragma: no cover - network boundary
            self._send_json({"error": "server_error", "detail": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
            dataset = payload.get("dataset") if "dataset" in payload else load_seed()
            options = payload.get("options", {})
            if path == "/api/analyze":
                self._send_json(analyze_dataset(dataset, options))
            elif path == "/api/recommend":
                self._send_json(recommend_measurements(dataset, options))
            elif path == "/api/enumeration-check":
                grid_n = int(payload.get("grid_n", 180))
                grid_n = max(20, min(grid_n, 360))
                self._send_json(enumeration_check(dataset, options, grid_n=grid_n))
            elif path == "/api/apply-measurement":
                result = apply_measurement(dataset, payload, options)
                # API boundary should not return giant before/after pair rows?
                # They are useful for explanation and detail; sample is small.
                self._send_json(result)
            elif path == "/api/validate-import":
                analysis = analyze_dataset(dataset, options)
                self._send_json({"valid": True, "summary": analysis["counts"], "ambiguity": analysis["ambiguity"]})
            else:
                self._send_json({"error": "not_found", "path": path}, 404)
        except DatasetError as exc:
            self._send_json({"error": "dataset_error", "detail": str(exc)}, 400)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"error": "bad_request", "detail": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - network boundary
            self._send_json({"error": "server_error", "detail": str(exc)}, 500)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Pointwise Delivery listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
