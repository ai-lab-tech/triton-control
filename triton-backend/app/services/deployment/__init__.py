"""Deployment service package for self-managed Triton deployments.

Public surface is provided by the submodules:
``deployment`` for use cases, ``kubernetes`` for Kubernetes operations, and
``records`` for deployment-related database updates.

Service-layer package only; no HTTP handlers live here.
"""
