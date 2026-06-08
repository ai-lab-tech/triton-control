"""Database package — ORM engine, session management, and entity definitions.

Sub-modules:
  ``database`` — SQLModel/SQLAlchemy engine, FastAPI session dependency
                  (``get_session``), background-task session factory
                  (``session_factory``), and ``init_db`` startup helper.
  ``entities`` — all ORM table models: ``TritonInstanceEntity``,
                  ``UserEntity``, ``OidcConfigEntity``,
                  ``DashboardAlertEntity``.
"""
