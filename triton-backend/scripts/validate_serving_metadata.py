"""Repository entry point; the installed equivalent is triton-validate-metadata."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.serving_metadata_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
