"""Synthetic Triton HTTP fixture used only inside qualification pods."""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = sys.argv[1]


class Handler(BaseHTTPRequestHandler):
    attempts = 0

    def do_GET(self):
        ok = self.path in {"/v2/health/live", "/v2/health/ready", "/v2/models/iris/versions/1/ready"}
        if self.path == "/v2/models/iris/versions/2/ready":
            Handler.attempts += 1
            ok = MODE == "slow" and Handler.attempts > 2
        self.send_response(200 if ok else 503)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
