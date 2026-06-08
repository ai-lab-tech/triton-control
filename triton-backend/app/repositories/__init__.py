"""Repository package — thin data-access helpers that wrap SQLModel queries.

Each sub-module corresponds to one ORM entity and exposes pure functions
that accept a ``Session`` as their first argument:
  ``dashboard_alerts`` — queries and bulk-replace for ``DashboardAlertEntity``.
  ``instances``        — lookups and listing for ``TritonInstanceEntity``.
  ``oidc_config``      — singleton read/write for ``OidcConfigEntity``.
  ``users``            — lookups, listing, and mutations for ``UserEntity``.
"""
