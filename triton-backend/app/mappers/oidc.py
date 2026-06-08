"""Mapper from ``OidcConfigEntity`` to ``OidcSettingsDTO``.

Provides a single conversion function:
  ``oidc_entity_to_dto(entity)`` — maps all OIDC configuration fields from
    the ``OidcConfigEntity`` ORM row to the outgoing ``OidcSettingsDTO``
    Pydantic model used by the settings API.
"""

from app.db.entities import OidcConfigEntity
from app.schemas import OidcSettingsDTO


def oidc_entity_to_dto(entity: OidcConfigEntity) -> OidcSettingsDTO:
    """Convert OIDC settings entity to outgoing DTO."""
    return OidcSettingsDTO(
        oidc_enabled=entity.oidc_enabled,
        issuer=entity.issuer,
        client_id=entity.client_id,
        client_secret=entity.client_secret,
        client_secret_configured=bool(entity.client_secret),
        redirect_uri=entity.redirect_uri,
        scopes=entity.scopes,
        strict_discovery_document_validation=entity.strict_discovery_document_validation,
        ca_certificate=entity.ca_certificate,
        api_base_url=entity.api_base_url,
        config_source="db",
    )
