"""Pydantic/SQLModel schemas for S3 file-system operations.

Defines the response models used by the S3 browsing API:
  ``S3EntryDTO``           — a single S3 object or pseudo-folder entry
                              (name, path, type, optional size and modified).
  ``S3ListResponse``       — paginated listing response containing a ``prefix``
                              and a list of ``S3EntryDTO`` items.
  ``S3FileContentResponse``— response body for a file download (path + text
                              content).
  ``S3FileWriteResponse``  — confirmation response after a successful file
                              upload (echoes back the object path).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from sqlmodel import SQLModel


class S3EntryDTO(SQLModel):
    name: str
    path: str
    type: Literal["folder", "file"]
    size: Optional[int] = None
    modified: Optional[datetime] = None


class S3ListResponse(SQLModel):
    prefix: str
    entries: List[S3EntryDTO]


class S3FileContentResponse(SQLModel):
    path: str
    content: str


class S3FileWriteResponse(SQLModel):
    path: str
    size: int
