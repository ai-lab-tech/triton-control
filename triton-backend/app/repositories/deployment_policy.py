"""Persistence for the singleton deployment access policy."""

from sqlmodel import Session

from app.db.entities import DeploymentPolicyEntity


def get_override(session: Session) -> bool | None:
    entity = session.get(DeploymentPolicyEntity, 1)
    return entity.allow_http if entity else None


def get_base_domain_override(session: Session) -> str | None:
    entity = session.get(DeploymentPolicyEntity, 1)
    return entity.base_domain if entity else None


def save_override(
    session: Session, value: bool | None, *, base_domain: str | None = None, update_base_domain: bool = False,
) -> None:
    entity = session.get(DeploymentPolicyEntity, 1) or DeploymentPolicyEntity()
    entity.allow_http = value
    if update_base_domain:
        entity.base_domain = base_domain
    session.add(entity)
    session.commit()
