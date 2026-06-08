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

from sqlmodel import Session, select

from app.db.entities import TritonInstanceEntity


def find_by_id(session: Session, instance_id: int) -> TritonInstanceEntity | None:
    return session.get(TritonInstanceEntity, instance_id)


def find_by_url(session: Session, url: str) -> TritonInstanceEntity | None:
    return session.exec(select(TritonInstanceEntity).where(TritonInstanceEntity.url == url)).first()


def find_by_name(session: Session, name: str) -> TritonInstanceEntity | None:
    return session.exec(select(TritonInstanceEntity).where(TritonInstanceEntity.name == name)).first()


def list_ids(session: Session) -> list[int | None]:
    return list(session.exec(select(TritonInstanceEntity.id)).all())


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
    session.delete(instance)
    session.commit()
