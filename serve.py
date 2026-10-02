"""MIT. Local demo server: python -B serve.py (no dependencies, loopback only)."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8087)
args = parser.parse_args()
root = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    # Explicit MIME type is useful on Windows installations with different registry mappings.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


print(f"Open http://localhost:{args.port}/web/", flush=True)
with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(root))) as server:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
