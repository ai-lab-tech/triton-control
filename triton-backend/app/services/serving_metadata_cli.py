"""Offline serving metadata validator, usable from an installed wheel."""

from app.services.contract_cli import run_cli
from app.services.serving_metadata import load_metadata


def main() -> int:
    return run_cli(load_metadata, description="Validate metadata structure and semantics; no trust or approval check.")


if __name__ == "__main__":
    raise SystemExit(main())
