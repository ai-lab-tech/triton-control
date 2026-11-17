"""Offline validation validator, usable from an installed wheel."""

from app.services.contract_cli import run_cli
from app.services.validation import load_validation


def main() -> int:
    return run_cli(
        load_validation, description="Validate evaluation contracts; no evaluator execution, trust or approval check.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
