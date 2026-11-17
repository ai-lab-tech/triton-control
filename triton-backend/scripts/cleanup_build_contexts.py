"""Delete expired temporary build contexts; keep immutable receipts and operations."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import session_factory  # noqa: E402
from app.services.builds.contexts_cleanup import cleanup  # noqa: E402

if __name__ == "__main__":
    result = cleanup(session_factory)
    print(json.dumps(result))
    raise SystemExit(1 if result["failed"] else 0)
