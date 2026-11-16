"""Synthetic, read-only S3 protocol fixture. Never used by the application.

GET/list only; deliberately does not implement authentication. Real S3 IAM and
version behavior are covered separately against MinIO.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape

FILES = {key: value.encode() for key, value in json.loads(Path("/fixture/files.json").read_text()).items()}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        if "list-type" in query:
            prefix = query["prefix"][0]
            case = prefix.split("/")[0]
            paths = list(FILES)
            if case == "missing":
                paths.pop()
            if case == "extra":
                paths.append("extra.bin")
            if case == "unsafe":
                paths.append("../escape")
            contents = "".join(
                f"<Contents><Key>{escape(prefix + path)}</Key><Size>1</Size></Contents>" for path in paths
            )
            body = (
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                "<IsTruncated>false</IsTruncated>" + contents + "</ListBucketResult>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
        else:
            _, bucket, case, path = unquote(url.path).split("/", 3)
            if bucket != "fixture" or query.get("versionId") != ["v1"] or path not in FILES:
                self.send_response(404)
                self.end_headers()
                return
            body = FILES[path]
            self.send_response(200)
            self.send_header("x-amz-version-id", "v2" if case == "version" else "v1")
            if case == "corrupt":
                body = b"X" * len(body)
            if case == "interrupted":
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[:1])
                self.close_connection = True
                return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
