"""Read-only production cutover inventory; never delete serving or development records."""

from sqlmodel import Session, col, select

from app.db.entities import (
    BuildAttemptEntity,
    CodeServerEntity,
    LocalBindingPolicyEntity,
    LocalCredentialEntity,
    MlflowEntity,
    ModelPerfJobEntity,
    OperationEntity,
    PerfAnalyzerEntity,
    WorkflowS3CredentialEntity,
)
from app.repositories.deployment_intents import KIND as DEPLOYMENT_KIND


def cutover_blockers(session: Session) -> list[str]:
    blockers = []
    for entity, label in ((CodeServerEntity, "workspaces"), (MlflowEntity, "MLflow"),
                          (WorkflowS3CredentialEntity, "workflow credentials"),
                          (PerfAnalyzerEntity, "legacy benchmarks")):
        if session.exec(select(entity).limit(1)).first() is not None:
            blockers.append(label)
    if session.exec(select(OperationEntity).where(
        col(OperationEntity.kind).in_([DEPLOYMENT_KIND, "build.context.stage", "build.image"]),
        col(OperationEntity.state).notin_(["succeeded", "failed", "cancelled"]),
    ).limit(1)).first():
        blockers.append("queued development operations")
    if session.exec(select(BuildAttemptEntity).where(col(BuildAttemptEntity.cleaned).is_(False)).limit(1)).first():
        blockers.append("uncleaned build attempts")
    if session.exec(select(ModelPerfJobEntity).where(
        col(ModelPerfJobEntity.cleaned).is_(False),
    ).limit(1)).first():
        blockers.append("active benchmarks")
    policy = session.get(LocalBindingPolicyEntity, 1)
    if policy and policy.payload.get("build_push"):
        blockers.append("build push bindings")
    for credential in session.exec(select(LocalCredentialEntity)).all():
        if credential.scope.get("permission") == "push":
            blockers.append("build push credentials")
            break
    return blockers
