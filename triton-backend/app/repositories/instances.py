"""Data-access helpers for ``TritonInstanceEntity`` rows.

Provides thin query functions that abstract SQLModel selects from service code:
  ``find_by_id(session, id)``          — look up a single instance by primary key.
  ``find_by_url(session, url)``        — look up by normalised URL.
  ``find_by_name(session, name)``      — look up by unique instance name.
  ``list_ids(session)``                — return all registered instance ids.
  ``list_visible(session, ...)``       — paginated/filtered listing; supports
                                          optional name filter and result limit.
  ``save(session, entity)``            — persist (add + commit + refresh) an entity.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlmodel import Session, col, select

from app.db.entities import DeploymentIntentEntity, TritonInstanceEntity
from app.exceptions import ConflictError


def lock_model_state(session: Session, instance_id: int) -> None:
    """Serialize model mutation across backend workers without blocking an async request."""
    if session.get_bind().dialect.name == "postgresql":
        acquired = session.execute(
            text("SELECT pg_try_advisory_xact_lock(741293817, CAST(:instance AS integer))"),
            {"instance": instance_id},
        ).scalar_one()
        if not acquired:
            raise ConflictError("Another model state change is in progress")
    elif session.get_bind().dialect.name == "sqlite":
        # Take a database write lock before inspecting the provider. SQLite is
        # supported for local use and serializes writers at database scope.
        session.execute(
            text("UPDATE triton_instances SET id = id WHERE id = :instance"), {"instance": instance_id},
        )


def find_by_id(session: Session, instance_id: int) -> TritonInstanceEntity | None:
    return session.get(TritonInstanceEntity, instance_id)


def find_by_url(session: Session, url: str) -> TritonInstanceEntity | None:
    return session.exec(select(TritonInstanceEntity).where(TritonInstanceEntity.url == url)).first()


def find_by_name(session: Session, name: str) -> TritonInstanceEntity | None:
    return session.exec(select(TritonInstanceEntity).where(TritonInstanceEntity.name == name)).first()


def list_ids(session: Session) -> list[int | None]:
    return list(session.exec(select(TritonInstanceEntity.id)).all())


def list_mlflow_for_owner(session: Session, owner_user_id: int) -> list[TritonInstanceEntity]:
    query = select(TritonInstanceEntity).where(
        TritonInstanceEntity.created_by_user_id == owner_user_id,
        col(TritonInstanceEntity.mlflow_model_uri).is_not(None),
    )
    return list(session.exec(query).all())


def list_visible(
    session: Session,
    *,
    limit: int,
    assigned_names: list[str] | None = None,
) -> list[TritonInstanceEntity]:
    query = select(TritonInstanceEntity)
    if assigned_names is not None:
        if not assigned_names:
            return []
        query = query.where(TritonInstanceEntity.name.in_(assigned_names))  # type: ignore[attr-defined]

    query = query.limit(limit).order_by(TritonInstanceEntity.created_at.desc())  # type: ignore[attr-defined]
    return list(session.exec(query).all())


def save(session: Session, instance: TritonInstanceEntity, *, refresh: bool = True) -> TritonInstanceEntity:
    session.add(instance)
    session.commit()
    if refresh:
        session.refresh(instance)
    return instance


def create(session: Session, **values: Any) -> TritonInstanceEntity:
    return save(session, TritonInstanceEntity(**values))


def delete(session: Session, instance: TritonInstanceEntity) -> None:
    intent = session.exec(select(DeploymentIntentEntity).where(
        DeploymentIntentEntity.instance_id == instance.id,
    )).first()
    if intent:
        if not intent.cleaned:
            from app.exceptions import ConflictError
            raise ConflictError("Owned deployment cleanup must finish before instance deletion")
        intent.reservation = None
        session.add(intent)
    session.delete(instance)
    session.commit()
