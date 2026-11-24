"""NSP Learning Centre - Teacher Workload, KPI and Retention module (high-fidelity prototype).

Run:  python run.py            (Python 3.8+, no packages to install)
The browser opens at http://localhost:8000. Stop with Ctrl+C.
"""
import argparse
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from nsp import App, ROOT
from nsp.controllers import make_handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app, ROOT / "static"))
    url = "http://localhost:%d" % a.port
    print("NSP module running at %s  (Ctrl+C to stop)" % url)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
