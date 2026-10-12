"""Independent S3 assertions against the smoke environment's real MinIO."""

import os
import sys

import boto3

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("PLUGIN_SMOKE_S3_URL", "http://127.0.0.1:19000"),
    aws_access_key_id=os.environ["PLUGIN_SMOKE_S3_KEY"],
    aws_secret_access_key=os.environ["PLUGIN_SMOKE_S3_SECRET"],
    region_name="us-east-1",
)
bucket = "plugin-smoke"
operation = sys.argv[1]
if operation == "init":
    if bucket not in [item["Name"] for item in s3.list_buckets()["Buckets"]]:
        s3.create_bucket(Bucket=bucket)
elif operation == "put":
    s3.put_object(Bucket=bucket, Key=sys.argv[2], Body=sys.argv[3].encode())
elif operation == "get":
    sys.stdout.write(s3.get_object(Bucket=bucket, Key=sys.argv[2])["Body"].read().decode())
else:
    raise ValueError(f"Unknown operation: {operation}")
