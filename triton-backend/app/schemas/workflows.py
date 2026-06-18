"""Schemas for the global Argo Workflows integration."""

from sqlmodel import SQLModel


class ArgoWorkflowsStatusResponse(SQLModel):
    """Runtime state of the Helm-managed global Argo Workflows server."""

    enabled: bool
    ready: bool
    status: str
    status_message: str
    namespace: str
    service_name: str
    base_path: str
