"""Compatibility entrypoint for legacy ``uvicorn main:app`` invocations.

This module is a thin shim kept so that existing launch commands and
deployment scripts that reference ``main:app`` continue to work without
modification. All real application logic lives in ``app/main.py``.

Public surface:
  ``app``  — the FastAPI application instance (re-exported from ``app.main``).
  ``run``  — convenience callable for ``python -m`` style execution.
"""

from app.main import app, run

__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
