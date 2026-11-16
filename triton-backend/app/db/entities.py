"""SQLModel ORM table definitions for the triton-backend.

Defines the following database tables:
  ``triton_instances``   (``TritonInstanceEntity``) — registered Triton
                          servers with connection details, health state, and
                          optional S3 model-repository credentials.
  ``users``              (``UserEntity``)            — local and OIDC users
                          with roles, assigned instances, and auth metadata.
  ``oidc_config``        (``OidcConfigEntity``)      — singleton row that
                          persists the OIDC provider configuration.
  ``code_servers``       (``CodeServerEntity``)       — per-user Kubernetes
                          Development workspaces.
  ``workflow_s3_credentials`` (``WorkflowS3CredentialEntity``) — Argo workflow
                          S3 credentials mirrored to Kubernetes secrets.
  ``mlflow``             (``MlflowEntity``)          — singleton MLflow
                          installation managed by Triton Control.
  ``dashboard_alerts``   (``DashboardAlertEntity``)  — ephemeral health-alert
                          snapshots rebuilt on every health-refresh cycle.
"""

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    perf_deleting: bool = Field(default=False)
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
    example_model_name: Optional[str] = None
    inference_examples: dict[str, dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON))
    example_request: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    mlflow_model_uri: Optional[str] = None
    mlflow_model_name: Optional[str] = None
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
    credential_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TeamEntity(SQLModel, table=True):
    """Engineering responsibility, independent of application authorization."""

    __tablename__ = "teams"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserTeamEntity(SQLModel, table=True):
    """One current membership per pair; revocations are retained, not deleted."""

    __tablename__ = "user_teams"

    team_id: UUID = Field(foreign_key="teams.id", primary_key=True)
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True))
    is_active: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TeamEventEntity(SQLModel, table=True):
    """Append-only administrative history, retained after account removal."""

    __tablename__ = "team_events"

    id: int | None = Field(default=None, primary_key=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    # Historical identities intentionally do not reference deletable user rows.
    actor_user_id: int
    subject_user_id: int | None = None
    action: str
    changes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PreparationTaskEntity(SQLModel, table=True):
    """Engineering work item; execution states belong to separate attempts."""

    __tablename__ = "preparation_tasks"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_preparation_revision"),
        CheckConstraint(
            "state IN ('queued', 'claimed', 'awaiting_information', 'cancelled')",
            name="ck_preparation_state",
        ),
        CheckConstraint("state != 'claimed' OR assignee_user_id IS NOT NULL", name="ck_preparation_claim"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(sa_column=Column(String(200), nullable=False))
    assigned_team_id: UUID = Field(foreign_key="teams.id", index=True)
    # Historical identities survive user deletion; eligibility is checked on every use.
    submitter_user_id: int = Field(index=True)
    assignee_user_id: int | None = Field(default=None, index=True)
    source_snapshot_id: UUID | None = None
    state: str = Field(default="queued", index=True)
    revision: int = 1
    missing_inputs: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PreparationTaskEventEntity(SQLModel, table=True):
    """Append-only task history, written atomically with each revision."""

    __tablename__ = "preparation_task_events"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="uq_preparation_event_revision"),)

    id: int | None = Field(default=None, primary_key=True)
    task_id: UUID = Field(foreign_key="preparation_tasks.id", index=True)
    revision: int
    actor_user_id: int
    action: str
    changes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PreparationAttemptEntity(SQLModel, table=True):
    """Immutable references to actual engineering attempts, never task status."""

    __tablename__ = "preparation_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "installation_id", "record_id", name="uq_preparation_attempt_origin"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_id: UUID = Field(foreign_key="preparation_tasks.id", index=True)
    installation_id: str = Field(sa_column=Column(String(256), nullable=False))
    record_id: str = Field(sa_column=Column(String(256), nullable=False))
    actor_user_id: int
    attempt: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AccountLifecycleTokenEntity(SQLModel, table=True):
    """Hashed, single-use local-account invitation and reset tokens."""

    __tablename__ = "account_lifecycle_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    purpose: str = Field(index=True)  # invite | password_reset
    token_hash: str = Field(sa_column=Column(String, unique=True, index=True))
    expires_at: datetime = Field(index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    consumed_at: Optional[datetime] = Field(default=None, index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)


class EmailConfigEntity(SQLModel, table=True):
    """Database-managed singleton email delivery configuration."""

    __tablename__ = "email_config"

    id: Optional[int] = Field(default=1, primary_key=True)
    delivery_mode: str = Field(default="disabled")
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_tls_mode: str = Field(default="starttls")
    smtp_allow_insecure: bool = Field(default=False)
    smtp_username: str = Field(default="")
    smtp_password_enc: str = Field(default="")
    sender_email: str = Field(default="")
    sender_name: str = Field(default="Triton Control")
    public_app_url: str = Field(default="")
    ca_certificate: str = Field(default="", sa_column=Column(Text))
    invite_expiry_minutes: int = Field(default=1440)
    reset_expiry_minutes: int = Field(default=30)
    connect_timeout_seconds: int = Field(default=10)
    operation_timeout_seconds: int = Field(default=15)
    invite_subject: str = Field(default="You are invited to Triton Control")
    invite_text_template: str = Field(default="", sa_column=Column(Text))
    invite_html_template: str = Field(default="", sa_column=Column(Text))
    reset_subject: str = Field(default="Reset your Triton Control password")
    reset_text_template: str = Field(default="", sa_column=Column(Text))
    reset_html_template: str = Field(default="", sa_column=Column(Text))
    last_status: str = Field(default="not_tested")
    last_status_message: str = Field(default="")
    last_status_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SecurityEventEntity(SQLModel, table=True):
    """Sanitized local-account lifecycle security event."""

    __tablename__ = "security_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    outcome: str = Field(index=True)
    target_email: str = Field(default="", index=True)
    actor_email: str = Field(default="", index=True)
    purpose: str = Field(default="", index=True)
    detail: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class RecoveryRateLimitEntity(SQLModel, table=True):
    """Database-backed forgot-password rate-limit bucket."""

    __tablename__ = "recovery_rate_limits"
    __table_args__ = (UniqueConstraint("bucket_key", "window_started_at", name="uq_recovery_rate_bucket"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    bucket_key: str = Field(index=True)
    window_started_at: datetime = Field(index=True)
    request_count: int = Field(default=1)


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


class ModelPerfJobEntity(SQLModel, table=True):
    """Durable execution intent; active model uniqueness spans versions and workers."""

    __tablename__ = "model_perf_jobs"
    __table_args__ = (
        Index(
            "uq_model_perf_job_active", "instance_id", "model_name", unique=True,
            postgresql_where=text("state IN ('creating', 'pending', 'running', 'stopping')"),
            sqlite_where=text("state IN ('creating', 'pending', 'running', 'stopping')"),
        ),
    )

    id: str = Field(primary_key=True)
    instance_id: int = Field(foreign_key="triton_instances.id", index=True)
    model_name: str
    model_version: str
    image: str
    namespace: str
    job_name: str
    job_uid: str | None = None
    state: str = Field(default="creating", index=True)
    message: str = "Preparing benchmark."
    prepared: bool = False
    stop_output_saved: bool = False
    cleaned: bool = False
    pull_secret: bool = False
    batch_size: int = 1
    concurrency_range: str = "1"
    measurement_request_count: int = 50
    input_data: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    command: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    output: str = Field(default="", sa_column=Column(Text))
    exit_code: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MlflowEntity(SQLModel, table=True):
    """Database model for the singleton MLflow installation."""

    __tablename__ = "mlflow"

    s3_profile_id: int | None = Field(default=None, foreign_key="s3_profiles.id")
    artifact_root: str = ""

    id: Optional[int] = Field(default=1, primary_key=True)
    namespace: str
    deployment_name: str
    service_name: str
    image: str
    applied_resources: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="creating")
    status_message: str = Field(default="")
    last_transition_at: datetime = Field(default_factory=datetime.utcnow)
    installed_at: datetime = Field(default_factory=datetime.utcnow)


class CodeServerEntity(SQLModel, table=True):
    """Database model for a user's Kubernetes Development workspace."""

    __tablename__ = "code_servers"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "name",
            name="uq_code_server_owner_name",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(index=True)
    namespace: str
    statefulset_name: str
    service_name: str
    secret_name: str
    image: str
    url: str = Field(default="")
    password_enc: str = Field(default="")
    status: str = Field(default="creating")
    status_message: str = Field(default="")
    applied_resources: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class S3ProfileEntity(SQLModel, table=True):
    """Global S3 credentials; owner_user_id records the creator."""

    __tablename__ = "s3_profiles"
    __table_args__ = (
        UniqueConstraint(
            "name",
            name="uq_s3_profile_name",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(index=True)
    endpoint: str
    bucket: str
    region: str = Field(default="us-east-1")
    access_key: str
    secret_key_hash: str = Field(default="")
    secret_key_enc: str = Field(default="")
    prefix: str = Field(default="")
    force_path_style: bool = Field(default=True)
    ca_certificate: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowS3CredentialEntity(SQLModel, table=True):
    """Metadata index for S3 credentials stored in Kubernetes Secrets."""

    __tablename__ = "workflow_s3_credentials"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id",
            "name",
            name="uq_workflow_s3_credential_owner_name",
        ),
        UniqueConstraint(
            "namespace",
            "secret_name",
            name="uq_workflow_s3_credential_secret",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by_user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(index=True)
    namespace: str
    secret_name: str
    access_key_id: str
    s3_profile_id: Optional[int] = Field(default=None, foreign_key="s3_profiles.id", index=True)
    s3_profile_name: str = ""
    sync_error: str = ""
    last_synced_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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


class ErrorEventEntity(SQLModel, table=True):
    """Persisted sanitized error event visible to administrators."""

    __tablename__ = "error_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    level: str = Field(default="ERROR", index=True)
    message: str = Field(sa_column=Column(Text))
    detail: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    path: Optional[str] = Field(default=None, index=True)
    method: Optional[str] = None
    status_code: Optional[int] = Field(default=None, index=True)
    user_email: Optional[str] = Field(default=None, index=True)
    user_id: Optional[int] = Field(default=None, index=True)
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class DeploymentPolicyEntity(SQLModel, table=True):
    """Optional administrator overrides for model endpoints."""

    __tablename__ = "deployment_policy"
    id: int = Field(default=1, primary_key=True)
    allow_http: bool | None = Field(default=None)
    base_domain: str | None = Field(default=None)


class RegistryConfigEntity(SQLModel, table=True):
    """Singleton registry credentials managed through admin settings."""
    __tablename__ = "registry_config"
    id: int = Field(default=1, primary_key=True)
    dockerconfigjson_enc: str = Field(default="", repr=False)


class OperationEntity(SQLModel, table=True):
    """Durable execution intent, separate from domain records."""

    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("actor_id", "kind", "target", "idempotency_key",
                                       name="uq_operation_request"),)
    id: str = Field(primary_key=True)
    actor_id: int = Field(index=True)
    kind: str = Field(max_length=80)
    target: str = Field(max_length=256)
    idempotency_key: str = Field(max_length=128)
    payload_hash: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON))
    state: str = Field(default="pending", index=True)
    cancel_requested: bool = False
    generation: int = 0
    attempts: int = 0
    worker: Optional[str] = None
    lease_until: Optional[datetime] = None
    available_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    message: str = ""


class DeploymentIntentEntity(SQLModel, table=True):
    """Frozen protected creation inputs and resource ownership; retained after deletion."""

    __tablename__ = "deployment_intents"
    operation_id: str = Field(primary_key=True, foreign_key="operations.id")
    instance_id: int = Field(index=True, unique=True)
    namespace: str
    name: str
    reservation: Optional[str] = Field(default=None, unique=True)
    bundle_enc: str = Field(sa_column=Column(Text))
    response: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    resource_uids: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    deadline_at: datetime
    delete_requested: bool = False
    cleaned: bool = False
    examples_captured: bool = False


class ServingCandidateEntity(SQLModel, table=True):
    """Immutable candidate envelope and inventories; publication has no update path."""

    __tablename__ = "serving_candidates"
    installation_id: str = Field(primary_key=True)
    record_id: str = Field(primary_key=True)
    digest: str
    payload: str = Field(sa_column=Column(Text, nullable=False))
    inventories: str = Field(sa_column=Column(Text, nullable=False))


class CandidateImportEntity(SQLModel, table=True):
    """Insert-only capture receipt alongside the canonical #25 candidate."""

    __tablename__ = "candidate_imports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "record_id"],
            ["serving_candidates.installation_id", "serving_candidates.record_id"],
            name="fk_candidate_import_origin",
        ),
    )
    installation_id: str = Field(primary_key=True)
    record_id: str = Field(primary_key=True)
    candidate_digest: str = Field(index=True)
    import_digest: str
    owner_user_id: int
    task_id: UUID | None = Field(default=None, foreign_key="preparation_tasks.id", index=True)
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BuildQualificationEntity(SQLModel, table=True):
    """Local qualification attempts, including failures; secrets stay in Kubernetes."""

    __tablename__ = "build_qualifications"
    id: Optional[int] = Field(default=None, primary_key=True)
    policy_hash: str = Field(index=True)
    fixture_revision: str
    executor_image: str
    platform: str
    passed: bool = False
    checks: dict[str, bool] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evidence_path: str
    evidence_sha256: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HandoffEntity(SQLModel, table=True):
    """Immutable, deduplicated submission receipt; JSON contains no credentials."""
    __tablename__ = "preparation_handoffs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    identity_digest: str = Field(unique=True, index=True)
    request_digest: str
    task_id: UUID = Field(foreign_key="preparation_tasks.id", unique=True)
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HandoffNotificationEntity(SQLModel, table=True):
    __tablename__ = "handoff_notifications"
    __table_args__ = (UniqueConstraint("handoff_id", "user_id"),)
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    handoff_id: UUID = Field(foreign_key="preparation_handoffs.id", index=True)
    user_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: datetime | None = None


class PreparationSnapshotEntity(SQLModel, table=True):
    """Completed immutable experiment receipt; source deletion does not cascade."""

    __tablename__ = "preparation_snapshots"
    id: UUID = Field(primary_key=True)
    operation_id: str = Field(unique=True)
    instance_id: int = Field(index=True)
    actor_id: int
    task_id: UUID = Field(index=True)
    manifest_digest: str
    manifest: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    artifacts: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
