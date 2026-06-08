"""Triton service sub-package.

Sub-modules:
  ``client``    — async HTTP client (``TritonService``) for the Triton REST API.
  ``config``    — version-aware protobuf/config utilities and error helpers.
  ``health``    — background health-monitoring task (``InstanceHealthRefresher``).
  ``instances`` — Triton instance lifecycle use cases (CRUD + live connectivity).
  ``models``    — model repository and inference use cases.
"""
