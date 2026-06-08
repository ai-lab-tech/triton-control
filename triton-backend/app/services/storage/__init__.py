"""Storage service sub-package.

Sub-modules:
  ``s3``        — business-logic use cases for S3 model-repository access
                   (list, read, write, config management).
  ``s3_client`` — low-level ``boto3`` client factory, ``S3Config`` TypedDict,
                   and error-formatting utilities.
"""
