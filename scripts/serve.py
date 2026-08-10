"""Serve docs/ for local preview.

Exists instead of `python3 -m http.server --directory docs` because that module
evaluates os.getcwd() while building its argument parser, which fails outright when the
launching process has an inaccessible working directory. Changing to an absolute path
derived from __file__ before touching the module sidesteps it.

    python3 scripts/serve.py [port]
"""

import os
import sys
from http.server import SimpleHTTPRequestHandler, test

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")


class NoCacheHandler(SimpleHTTPRequestHandler):
    """Stop the browser serving a stale data.json after a rebuild."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers_original()

    def end_headers_original(self):
        SimpleHTTPRequestHandler.end_headers(self)


if __name__ == "__main__":
    os.chdir(DOCS)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    test(HandlerClass=NoCacheHandler, port=port, bind="127.0.0.1")
