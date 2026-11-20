"""Exec readiness probe inside the qualified helper. Only the pod loopback is addressable."""

import json
import sys
from http.client import HTTPConnection, HTTPException
from pathlib import Path

HTTP_TIMEOUT_SECONDS = 2

EXPECTED = Path("/plan/readiness.json")
RECEIPT = Path("/var/run/triton-materialization/receipt.json")


def probe_timeout_seconds(version_count: int) -> int:
    """Allow the sequential health/version sweep plus Python startup overhead."""
    return 5 + (2 + version_count) * HTTP_TIMEOUT_SECONDS


def check(expected_path: Path = EXPECTED, receipt_path: Path = RECEIPT) -> None:
    expected = json.loads(expected_path.read_bytes())
    if json.loads(receipt_path.read_bytes()) != expected["receipt"]:
        raise ValueError("Materialization receipt differs from the release expectation")

    paths = ["/v2/health/live", "/v2/health/ready"]
    for model in expected["models"]:
        paths.extend(f"/v2/models/{model['name']}/versions/{version}/ready" for version in model["versions"])

    connection = HTTPConnection("127.0.0.1", 8000, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        for path in paths:
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"{path} returned HTTP {response.status}")
            if len(response.read(4097)) > 4096:
                raise ValueError(f"{path} returned an oversized health response")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        check()
    except (ValueError, OSError, HTTPException, KeyError) as error:
        sys.exit(str(error))
