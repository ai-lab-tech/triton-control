#!/usr/bin/env python3
"""Deterministic Triton protocol fixture used only by the isolated validation CI test."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def respond(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in {"/v2/health/live", "/v2/health/ready", "/v2/models/iris/versions/1/ready"}:
            self.respond({})
        elif self.path == "/v2":
            self.respond({"version": "fixture-1"})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/v2/models/iris/versions/1/infer":
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.respond({"outputs": [{"data": [2 * data["inputs"][0]["data"][0]]}]})


if __name__ == "__main__":
    assert Path("/models/iris/1/weights/model.bin").read_bytes() == b"synthetic model fixture"
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
