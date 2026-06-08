"""Services package — all business-logic use cases for the triton-backend.

Sub-packages and modules:
  ``access``       — shared instance-access guards.
  ``dashboard``    — dashboard alert aggregation.
  ``users``        — admin user management.
  ``auth/``        — bootstrap, local auth, OIDC settings, and preflight flows.
  ``oidc/``        — OIDC BFF, provider utilities, config source, and TLS.
  ``triton/``      — Triton client, instance/model use cases, and health poller.
  ``storage/``     — S3 use cases and boto3 client factory.
"""
