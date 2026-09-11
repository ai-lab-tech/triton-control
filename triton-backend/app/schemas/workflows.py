"""Schemas for the global Argo Workflows integration."""

from datetime import datetime

from pydantic import field_validator, model_validator
from sqlmodel import SQLModel

from app.schemas.certificates import validate_ca_certificate


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
    access_key_id: str = ""
    secret_access_key: str = ""
    s3_profile_id: int | None = None
    ca_certificate: str = ""

    @field_validator("ca_certificate")
    @classmethod
    def validate_ca_certificate(cls, value: str) -> str:
        return validate_ca_certificate(value)

    @field_validator("name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_source(self) -> "CreateWorkflowS3CredentialRequest":
        if self.s3_profile_id is not None:
            if self.s3_profile_id <= 0:
                raise ValueError("Invalid S3 profile ID")
            if self.access_key_id or self.secret_access_key or self.ca_certificate:
                raise ValueError("Choose an S3 profile or enter credentials manually, not both")
        elif not self.access_key_id.strip() or not self.secret_access_key.strip():
            raise ValueError("Access Key ID and Secret Access Key are required")
        self.access_key_id = self.access_key_id.strip()
        self.secret_access_key = self.secret_access_key.strip()
        return self


class WorkflowS3ProfileChoice(SQLModel):
    id: int
    name: str
    endpoint: str
    bucket: str
    region: str


class WorkflowS3CredentialDTO(SQLModel):
    """Workflow S3 credential metadata stored by Triton Control."""

    id: int
    name: str
    namespace: str
    secret_name: str
    access_key_id: str
    created_at: datetime
    updated_at: datetime
    s3_profile_id: int | None = None
    s3_profile_name: str = ""
    sync_error: str = ""
    last_synced_at: datetime | None = None


class WorkflowS3CredentialDeleteResponse(SQLModel):
    """Result of deleting a workflow S3 credential."""

    status: str
    message: str
    id: int
