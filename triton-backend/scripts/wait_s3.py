"""Wait for an authenticated S3 API, independently of the storage vendor."""

import argparse
import os
import time
from contextlib import closing
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def wait_for_s3(client: Any, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client.list_buckets()
            return
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (401, 403):
                raise RuntimeError("S3 readiness authentication failed") from error
        except BotoCoreError:
            pass
        time.sleep(1)
    raise RuntimeError("Authenticated S3 API did not become ready before the deadline")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    with closing(
        boto3.client(
            "s3",
            endpoint_url=args.endpoint,
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region_name="us-east-1",
            config=Config(
                s3={"addressing_style": "path"}, connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}
            ),
        )
    ) as client:
        wait_for_s3(client)


if __name__ == "__main__":
    main()
