"""NSP Learning Centre - Teacher Workload, KPI and Retention module (prototype).

Run:  python run.py          (Python 3.8+, no packages to install)
Then the browser opens at http://localhost:8000
"""
import json, sqlite3, threading, webbrowser, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "nsp_data.db"

def db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT NOT NULL)")
    return con

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            with db() as con:
                row = con.execute("SELECT json FROM app_state WHERE id = 1").fetchone()
            return self._send(200, row[0].encode()) if row else self._send(404, b"{}")
        if self.path in ("/", "/index.html"):
            return self._send(200, (ROOT / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/state":
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            json.loads(data)                      # reject invalid JSON
            with db() as con:
                con.execute("INSERT INTO app_state (id, json) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET json = excluded.json", (data.decode(),))
            return self._send(200, b"{}")
        if self.path == "/api/reset":
            with db() as con:
                con.execute("DELETE FROM app_state")
            return self._send(200, b"{}")
        self._send(404, b"Not found", "text/plain")

    def log_message(self, *a): pass

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--port", type=int, default=8000); a = p.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://localhost:{a.port}"
    print(f"NSP module running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")
