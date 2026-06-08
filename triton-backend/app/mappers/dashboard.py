"""Mapper from ``DashboardAlertEntity`` to ``DashboardAlertDTO``.

Provides a single conversion function:
  ``dashboard_alert_entity_to_dto(entity)`` — maps all fields of a
    ``DashboardAlertEntity`` ORM row to the outgoing ``DashboardAlertDTO``
    Pydantic model, including a safe ``id`` fallback of ``0`` when the
    entity has not yet been persisted.
"""

from app.db.entities import DashboardAlertEntity
from app.schemas import DashboardAlertDTO


def dashboard_alert_entity_to_dto(entity: DashboardAlertEntity) -> DashboardAlertDTO:
    """Convert a dashboard alert entity to outgoing DTO."""
    return DashboardAlertDTO(
        id=entity.id or 0,
        instance_id=entity.instance_id,
        instance_name=entity.instance_name,
        icon=entity.icon,
        label=entity.label,
        tone=entity.tone,
        created_at=entity.created_at,
    )
