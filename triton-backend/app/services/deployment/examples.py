"""Capture inference examples at deployment time without changing repository files."""

import json
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any, Iterator

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.exceptions import BadRequestError
from app.schemas import CreateDeploymentRequest
from app.schemas.deployment import MAX_EXAMPLE_BYTES, validate_example_payload
from app.services.storage.s3 import require_s3_client

logger = logging.getLogger(__name__)
MAX_LIST_PAGES = 4
MAX_MODELS = 32
MAX_TOTAL_BYTES = 1024 * 1024
CAPTURE_BUDGET_SECONDS = 5.0
S3_TIMEOUT_SECONDS = 1.0


@dataclass
class _CaptureBudget:
    deadline: float
    bytes_left: int


def _config_keys(client: Any, bucket: str, root: str, deadline: float) -> Iterator[str]:
    """Bound listing even when model artifacts contain millions of nested objects."""
    token = None
    for _ in range(MAX_LIST_PAGES):
        if monotonic() >= deadline:
            return
        args: dict[str, Any] = {"Bucket": bucket, "Prefix": root, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        response = client.list_objects_v2(**args)
        for item in response.get("Contents", []) or []:
            key = str(item.get("Key") or "")
            if key.startswith(root) and key.endswith("config.pbtxt"):
                relative = key.removeprefix(root)
                if relative == "config.pbtxt" or relative.count("/") == 1 and relative.endswith("/config.pbtxt"):
                    yield key
        token = response.get("NextContinuationToken")
        if not response.get("IsTruncated") or not token:
            return


def _read_example(body: Any, budget: _CaptureBudget) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        if monotonic() >= budget.deadline:
            raise TimeoutError("Optional example capture budget exceeded")
        if budget.bytes_left <= 0:
            raise ValueError("Optional example capture byte budget exceeded")
        chunk = body.read(min(16 * 1024, MAX_EXAMPLE_BYTES + 1 - size, budget.bytes_left))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        budget.bytes_left -= len(chunk)
        if size > MAX_EXAMPLE_BYTES:
            raise ValueError("Optional example exceeds capture size limit")


def collect_deployment_examples(request: CreateDeploymentRequest, instance: Any) -> dict[str, dict[str, Any]]:
    if request.example_request is not None:
        return {str(request.example_model_name): request.example_request}
    if not getattr(instance, "s3_enabled", False):
        return {}
    examples: dict[str, dict[str, Any]] = {}
    deadline = monotonic() + CAPTURE_BUDGET_SECONDS
    budget = _CaptureBudget(deadline, MAX_TOTAL_BYTES)
    attempted = 0
    try:
        client = require_s3_client(instance, client_config=Config(
            connect_timeout=S3_TIMEOUT_SECONDS, read_timeout=S3_TIMEOUT_SECONDS,
            retries={"total_max_attempts": 1},
        ))
        prefix = (instance.s3_prefix or "").strip("/")
        root = prefix + "/" if prefix else ""
        for config in _config_keys(client, instance.s3_bucket, root, deadline):
            if attempted >= MAX_MODELS or budget.bytes_left <= 0 or monotonic() >= deadline:
                break
            relative = config.removeprefix(root)
            folder = relative.removesuffix("config.pbtxt").rstrip("/")
            model = folder or request.model_name or prefix.rsplit("/", 1)[-1]
            if not model or (request.model_name and request.model_name != model):
                continue
            key = config.removesuffix("config.pbtxt") + "request.json"
            attempted += 1
            try:
                response = client.get_object(Bucket=instance.s3_bucket, Key=key)
                body = response["Body"]
                try:
                    limit = min(MAX_EXAMPLE_BYTES, budget.bytes_left)
                    if response.get("ContentLength", 0) > limit:
                        continue
                    raw = _read_example(body, budget)
                    payload = json.loads(raw)
                finally:
                    body.close()
                if isinstance(payload, dict):
                    validate_example_payload(payload)
                    snapshot = {**examples, model: payload}
                    if len(json.dumps(snapshot, allow_nan=False).encode("utf-8")) > MAX_TOTAL_BYTES:
                        break
                    examples = snapshot
            except (BotoCoreError, ClientError, ValueError, TypeError, RecursionError, OSError):
                # Missing or invalid optional examples must not prevent deployment.
                continue
    except (BadRequestError, BotoCoreError, ClientError, ValueError, OSError):
        logger.warning("Could not read optional inference examples during deployment")
    return examples
