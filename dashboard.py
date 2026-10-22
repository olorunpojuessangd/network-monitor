"""
dashboard.py - A tiny local web server for viewing results. Run it with:

    python3 dashboard.py

then open http://127.0.0.1:8080 in your browser.

It serves two kinds of things:
  /                      -> the HTML page (static/dashboard.html)
  /api/results?hours=24  -> JSON list of results from the last N hours
  /api/alerts            -> JSON list of the most recent alerts
  /api/status            -> JSON: latest diagnosis + the monitor's own health warnings

The page's JavaScript calls the /api/ URLs and draws the charts.
It only listens on 127.0.0.1, so it is reachable from this computer only.
Run it alongside monitor.py (in a second terminal window).
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from netmon import storage
from netmon.health import monitor_status
from netmon.config import PROJECT_ROOT, load_config, resolve_path

CONFIG = load_config()
DB_PATH = resolve_path(CONFIG["database_path"])
HTML_PATH = PROJECT_ROOT / "static" / "dashboard.html"


class DashboardHandler(BaseHTTPRequestHandler):
    """Python's http.server calls do_GET once for every browser request."""

    def do_GET(self):
        url = urlparse(self.path)

        if url.path == "/":
            self._send(200, "text/html", HTML_PATH.read_bytes())

        elif url.path == "/api/results":
            try:
                hours = float(parse_qs(url.query).get("hours", ["24"])[0])
            except ValueError:
                return self._send(400, "text/plain", b"hours must be a number")
            self._send_json(self._query(lambda conn: storage.recent_results(conn, hours)))

        elif url.path == "/api/alerts":
            self._send_json(self._query(storage.recent_alerts))

        elif url.path == "/api/status":
            self._send_json(self._query(lambda conn: monitor_status(conn, CONFIG)))

        else:
            self._send(404, "text/plain", b"Not found")

    def _query(self, fn):
        # Open a fresh connection per request: simple, and safe across threads.
        conn = storage.connect(DB_PATH)
        try:
            return fn(conn)
        finally:
            conn.close()

    def _send_json(self, data):
        self._send(200, "application/json", json.dumps(data).encode())

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep the terminal quiet; the browser polls every minute


def main():
    port = CONFIG["dashboard_port"]
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
