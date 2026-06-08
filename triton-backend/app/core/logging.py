"""Central logging configuration for the triton-backend.

Exposes two helpers:
  ``configure_logging()`` — should be called once at application startup;
                             reads ``LOG_LEVEL`` and ``BACKEND_VERBOSE`` and
                             ``LOG_FORMAT`` from the environment and
                             configures the root logger via
                             ``logging.basicConfig``.
  ``get_logger(name)``    — thin wrapper around ``logging.getLogger`` that
                             returns a named logger; use ``__name__`` as
                             the argument in each module.
"""

from __future__ import annotations

import logging
import os

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_verbose_logging() -> bool:
    """Return True when verbose backend logging is enabled."""
    return _parse_bool(os.getenv("BACKEND_VERBOSE"), default=False)


def get_log_level_name() -> str:
    """Return the configured effective log level name."""
    explicit = (os.getenv("LOG_LEVEL") or "").strip().upper()
    if explicit:
        return explicit
    return "INFO" if is_verbose_logging() else "WARNING"


def configure_logging() -> None:
    """Configure application logging once, using environment overrides."""
    level_name = get_log_level_name()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=os.getenv("LOG_FORMAT", _DEFAULT_FORMAT))
    for logger_name in ("sqlalchemy.engine", "botocore", "boto3", "urllib3"):
        logging.getLogger(logger_name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)
