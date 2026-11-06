"""Persist an optional inference example beside the selected model's config."""

import json

from botocore.exceptions import BotoCoreError, ClientError

from app.core.crypto import encrypt_secret
from app.exceptions import BadGatewayError, BadRequestError
from app.schemas import CreateDeploymentRequest
from app.services.deployment.records import _parse_s3_url
from app.services.storage.s3 import _list_config_pbtxt_keys
from app.services.storage.s3_client import build_s3_client, format_s3_error


def save_deployment_example(request: CreateDeploymentRequest) -> None:
    if request.example_request is None:
        return
    try:
        body = json.dumps(request.example_request, indent=2, allow_nan=False) + "\n"
    except (ValueError, TypeError) as exc:
        raise BadRequestError("Example request must be a valid JSON object with finite values") from exc
    s3 = _parse_s3_url(request.s3_url)
    client = build_s3_client({
        "s3_endpoint": str(s3["endpoint"]),
        "s3_access_key": request.s3_access_key,
        "s3_secret_key_enc": encrypt_secret(request.s3_secret_key),
        "s3_region": request.s3_region,
        "s3_use_https": bool(s3["use_https"]),
        "s3_ca_certificate": request.s3_ca_certificate or "",
        "s3_address_style": "path",
    })
    bucket = str(s3["bucket"])
    prefix = str(s3["prefix"] or "").strip("/")
    model = request.example_model_name
    candidates = [f"{prefix}/{model}/config.pbtxt".lstrip("/")]
    if prefix.split("/")[-1] == model:
        candidates.append(f"{prefix}/config.pbtxt")
    try:
        configs = _list_config_pbtxt_keys(client, bucket, prefix)
        config = next((key for key in candidates if key in configs), None)
        if config is None:
            raise BadRequestError("Example model was not found in the selected repository")
        client.put_object(
            Bucket=bucket,
            Key=config.removesuffix("config.pbtxt") + "request.json",
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError) as exc:
        raise BadGatewayError(f"Failed to save example request ({format_s3_error(exc)})") from exc
