"""Mappers for converting between DTOs and database entities.

Sub-modules by domain:
  instance  — TritonInstanceEntity  →  TritonInstanceDTO / InstanceS3ConfigDTO
  user      — UserEntity            →  UserDTO
  dashboard — DashboardAlertEntity  →  DashboardAlertDTO
  oidc      — OidcConfigEntity      →  OidcSettingsDTO
"""

from app.mappers.dashboard import dashboard_alert_entity_to_dto
from app.mappers.instance import entity_to_dto, s3_entity_to_dto
from app.mappers.oidc import oidc_entity_to_dto
from app.mappers.perf_analyzer import perf_analyzer_entity_to_dto
from app.mappers.user import user_entity_to_dto

__all__ = [
    "dashboard_alert_entity_to_dto",
    "entity_to_dto",
    "oidc_entity_to_dto",
    "perf_analyzer_entity_to_dto",
    "s3_entity_to_dto",
    "user_entity_to_dto",
]
