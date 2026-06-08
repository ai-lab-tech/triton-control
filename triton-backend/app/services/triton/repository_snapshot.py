"""Helpers for persisted Triton repository model snapshots."""

from __future__ import annotations

from typing import Any


def normalize_repository_models(rows: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    for row in rows or []:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized: dict[str, str] = {"name": name.strip()}
        for key in ("version", "state", "reason"):
            value = row.get(key)
            if value is not None:
                normalized[key] = str(value)
        models.append(normalized)
    return sorted(models, key=lambda item: (item["name"].lower(), item.get("version", "")))


def repository_model_names(rows: list[dict[str, Any]] | None) -> list[str]:
    return sorted({row["name"] for row in normalize_repository_models(rows)})


def unavailable_repository_model_count(rows: list[dict[str, Any]] | None) -> int:
    return sum(
        1
        for row in normalize_repository_models(rows)
        if str(row.get("state") or "").strip().upper() == "UNAVAILABLE"
    )
