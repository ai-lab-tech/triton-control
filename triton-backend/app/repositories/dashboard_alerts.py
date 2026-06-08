"""Data-access helpers for ``DashboardAlertEntity`` rows.

Provides two functions that operate directly on the SQLModel session:
  ``list_visible(session, assigned_instance_names)`` — queries alerts ordered
    by creation time (newest first); when ``assigned_instance_names`` is a
    list it limits results to matching instance names (global alerts with
    ``instance_name=None`` are always included); when it is ``None`` all
    alerts are returned (admin path).
  ``replace_all(session, alerts)``                  — atomically deletes the
    entire ``dashboard_alerts`` table and inserts the provided list; used by
    ``InstanceHealthRefresher`` after each health-check cycle.
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.entities import DashboardAlertEntity


def list_visible(session: Session, assigned_instance_names: list[str] | None) -> list[DashboardAlertEntity]:
    query = select(DashboardAlertEntity).order_by(DashboardAlertEntity.created_at.desc())  # type: ignore[attr-defined]
    if assigned_instance_names is not None:
        if not assigned_instance_names:
            return []
        query = query.where(
            (DashboardAlertEntity.instance_name.is_(None))  # type: ignore[union-attr]
            | (DashboardAlertEntity.instance_name.in_(assigned_instance_names))  # type: ignore[union-attr]
        )
    return list(session.exec(query).all())


def replace_all(session: Session, alerts: list[DashboardAlertEntity]) -> None:
    session.exec(delete(DashboardAlertEntity))
    for alert in alerts:
        session.add(alert)
    session.commit()


def delete_for_instance(session: Session, instance_id: int, instance_name: str) -> None:
    session.exec(
        delete(DashboardAlertEntity).where(
            (col(DashboardAlertEntity.instance_id) == instance_id)
            | (col(DashboardAlertEntity.instance_name) == instance_name)
        )
    )
    session.commit()
