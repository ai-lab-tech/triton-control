"""Capture inference examples at deployment time without changing repository files."""

import json
import logging
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.exceptions import BadRequestError
from app.schemas import CreateDeploymentRequest
from app.services.storage.s3 import _list_config_pbtxt_keys, require_s3_client

logger = logging.getLogger(__name__)


def collect_deployment_examples(request: CreateDeploymentRequest, instance: Any) -> dict[str, dict[str, Any]]:
    if request.example_request is not None:
        return {str(request.example_model_name): request.example_request}
    if not getattr(instance, "s3_enabled", False):
        return {}
    examples: dict[str, dict[str, Any]] = {}
    try:
        client = require_s3_client(instance)
        prefix = (instance.s3_prefix or "").strip("/")
        root = prefix + "/" if prefix else ""
        for config in _list_config_pbtxt_keys(client, instance.s3_bucket, prefix):
            relative = config.removeprefix(root)
            folder = relative.removesuffix("config.pbtxt").rstrip("/")
            # Only models directly in this repository, not nested saved-model artifacts.
            if "/" in folder:
                continue
            model = folder or request.model_name or prefix.rsplit("/", 1)[-1]
            if not model or (request.model_name and request.model_name != model):
                continue
            key = config.removesuffix("config.pbtxt") + "request.json"
            try:
                response = client.get_object(Bucket=instance.s3_bucket, Key=key)
                body = response["Body"]
                try:
                    payload = json.loads(body.read())
                finally:
                    body.close()
                if isinstance(payload, dict):
                    json.dumps(payload, allow_nan=False)
                    examples[model] = payload
            except (BotoCoreError, ClientError, ValueError, TypeError):
                # Missing or invalid optional examples must not prevent deployment.
                continue
    except (BadRequestError, BotoCoreError, ClientError, ValueError):
        logger.warning("Could not read optional inference examples during deployment")
    return examples
