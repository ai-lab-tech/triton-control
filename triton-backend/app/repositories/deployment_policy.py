"""Persistence for the singleton deployment access policy."""

from sqlmodel import Session

from app.db.entities import DeploymentPolicyEntity


def get_override(session: Session) -> bool | None:
    entity = session.get(DeploymentPolicyEntity, 1)
    return entity.allow_http if entity else None


def save_override(session: Session, value: bool | None) -> None:
    entity = session.get(DeploymentPolicyEntity, 1) or DeploymentPolicyEntity()
    entity.allow_http = value
    session.add(entity)
    session.commit()
