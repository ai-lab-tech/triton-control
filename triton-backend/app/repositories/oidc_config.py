"""Data-access helpers for the singleton ``OidcConfigEntity`` row.

The ``oidc_config`` table is designed to hold at most one row that stores
the active OIDC provider settings.  This module provides:
  ``get(session)``          — returns the single ``OidcConfigEntity`` row,
                               or ``None`` if the table is empty.
  ``save(session, entity)`` — persists (add + commit + refresh) the entity;
                               used both for initial creation and for updates.
"""

from sqlmodel import Session

from app.db.entities import OidcConfigEntity

OIDC_CONFIG_ID = 1


def get(session: Session) -> OidcConfigEntity | None:
    """Return the singleton OIDC config row, if it exists."""
    return session.get(OidcConfigEntity, OIDC_CONFIG_ID)


def save(session: Session, entity: OidcConfigEntity, *, refresh: bool = True) -> OidcConfigEntity:
    """Persist an OIDC config row."""
    session.add(entity)
    session.commit()
    if refresh:
        session.refresh(entity)
    return entity
