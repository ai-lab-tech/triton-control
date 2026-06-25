"""Schemas for the global Argo Workflows integration."""

from datetime import datetime

from pydantic import field_validator
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
    service_url: str = ""


class CreateWorkflowS3CredentialRequest(SQLModel):
    """Request body for creating workflow-scoped S3 credentials."""

    name: str
    access_key_id: str
    secret_access_key: str

    @field_validator("name", "access_key_id", "secret_access_key")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned


class WorkflowS3CredentialDTO(SQLModel):
    """Workflow S3 credential metadata stored by Triton Control."""

    id: int
    name: str
    namespace: str
    secret_name: str
    access_key_id: str
    created_at: datetime
    updated_at: datetime


class WorkflowS3CredentialDeleteResponse(SQLModel):
    """Result of deleting a workflow S3 credential."""

    status: str
    message: str
    id: int
