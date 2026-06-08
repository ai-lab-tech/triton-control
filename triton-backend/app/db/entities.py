"""SQLModel ORM table definitions for the triton-backend.

Defines the following database tables:
  ``triton_instances``   (``TritonInstanceEntity``) — registered Triton
                          servers with connection details, health state, and
                          optional S3 model-repository credentials.
  ``users``              (``UserEntity``)            — local and OIDC users
                          with roles, assigned instances, and auth metadata.
  ``oidc_config``        (``OidcConfigEntity``)      — singleton row that
                          persists the OIDC provider configuration.
  ``dashboard_alerts``   (``DashboardAlertEntity``)  — ephemeral health-alert
                          snapshots rebuilt on every health-refresh cycle.
"""

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import JSON, String, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


class TritonInstanceEntity(SQLModel, table=True):
    """Database model for storing Triton instance configuration."""

    __tablename__ = "triton_instances"

    id: Optional[int] = Field(default=None, primary_key=True)

    url: str = Field(index=True)
    name: str = Field(sa_column=Column(String, unique=True, index=True))

    created_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)

    model_names: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    repository_models: List[dict[str, str]] = Field(default_factory=list, sa_column=Column(JSON))
    server_metadata: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    health_live: bool = Field(default=False)
    health_ready: bool = Field(default=False)
    health_last_checked_at: Optional[datetime] = Field(default=None)
    health_error: Optional[str] = None
    triton_verify_ssl: bool = Field(default=False)
    triton_ca_certificate: str = Field(default="")
    metrics_url: Optional[str] = None
    metrics_cpu: float = Field(default=0)
    metrics_ram: float = Field(default=0)
    metrics_gpu: float = Field(default=0)
    metrics_last_checked_at: Optional[datetime] = Field(default=None)
    metrics_error: Optional[str] = None

    deployment_runtime: str = Field(default="external")
    deployment_namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    deployment_service_name: Optional[str] = None
    deployment_secret_name: Optional[str] = None
    deployment_log: str = Field(default="")
    is_self_deployed: bool = Field(default=False)
    pod_statuses: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    s3_endpoint: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = Field(default="us-east-1")
    s3_prefix: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key_hash: Optional[str] = None
    s3_secret_key_enc: Optional[str] = None
    s3_use_https: Optional[bool] = None
    s3_verify_ssl: Optional[bool] = None
    s3_ca_certificate: str = Field(default="")
    s3_address_style: str = Field(default="path")
    s3_enabled: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        """Pydantic configuration."""

        arbitrary_types_allowed = True


class UserEntity(SQLModel, table=True):
    """Database model for local/OIDC users."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(sa_column=Column(String, unique=True, index=True))
    name: str
    role: str = Field(default="viewer")
    auth_provider: str = Field(default="local")  # local | oidc
    password_hash: Optional[str] = None
    oidc_subject: Optional[str] = Field(default=None, index=True)
    assigned_instances: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OidcConfigEntity(SQLModel, table=True):
    """Database model for persisted OIDC and auth settings."""

    __tablename__ = "oidc_config"

    id: Optional[int] = Field(default=1, primary_key=True)
    oidc_enabled: bool = Field(default=True)
    issuer: str = Field(default="")
    client_id: str = Field(default="")
    client_secret: str = Field(default="")
    redirect_uri: str = Field(default="")
    scopes: str = Field(default="openid profile email")
    strict_discovery_document_validation: bool = Field(default=False)
    ca_certificate: str = Field(default="")
    api_base_url: str = Field(default="")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PerfAnalyzerEntity(SQLModel, table=True):
    """Database model for the singleton Perf Analyzer installation."""

    __tablename__ = "perf_analyzer"

    id: Optional[int] = Field(default=1, primary_key=True)
    namespace: str
    deployment_name: str
    image: str
    applied_resources: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="creating")
    status_message: str = Field(default="")
    last_transition_at: datetime = Field(default_factory=datetime.utcnow)
    installed_at: datetime = Field(default_factory=datetime.utcnow)


class PerfAnalyzerRunEntity(SQLModel, table=True):
    """Persisted latest Perf Analyzer result for a specific model target."""

    __tablename__ = "perf_analyzer_runs"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "model_name",
            "model_version",
            name="uq_perf_analyzer_run_target",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    instance_id: int = Field(foreign_key="triton_instances.id", index=True)
    model_name: str = Field(index=True)
    model_version: str = Field(index=True)
    batch_size: int = Field(default=1)
    concurrency_range: str = Field(default="1")
    measurement_request_count: int = Field(default=50)
    input_data: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    command: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    output: str = Field(default="", sa_column=Column(String))
    executed_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class DashboardAlertEntity(SQLModel, table=True):
    """Persisted dashboard alert snapshot computed by backend background jobs."""

    __tablename__ = "dashboard_alerts"

    id: Optional[int] = Field(default=None, primary_key=True)
    instance_id: Optional[int] = Field(default=None, foreign_key="triton_instances.id", index=True)
    instance_name: Optional[str] = None
    icon: str
    label: str
    tone: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
