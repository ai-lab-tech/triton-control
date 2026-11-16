"""Run qualification fixtures and emit measured evidence, never approval.

Required environment: MATERIALIZER_TEST_IMAGE (registry digest),
MATERIALIZER_TEST_PLATFORM, MATERIALIZER_TEST_KUBECONFIG.
"""

import argparse
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import TypeAdapter  # noqa: E402

from app.core.canonical import canonical_bytes, digest  # noqa: E402
from app.schemas.materialization import FIXTURE_REVISION, Qualification  # noqa: E402
from app.schemas.serving_metadata import Image  # noqa: E402
from app.services.materialization.qualification import REQUIRED_CHECKS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    image: str = TypeAdapter(Image).validate_python(os.environ["MATERIALIZER_TEST_IMAGE"])
    platform = os.environ["MATERIALIZER_TEST_PLATFORM"]
    if platform not in {"linux/amd64", "linux/arm64"} or not os.environ.get("MATERIALIZER_TEST_KUBECONFIG"):
        raise ValueError("Explicit qualified platform and Kubernetes configuration required")
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        [
            "tests.test_materialization",
            "tests.test_materialization_kubernetes",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.testsRun < 16:
        return 1
    evidence = {
        "image": image,
        "platform": platform,
        "fixture_revision": FIXTURE_REVISION,
        "tests_run": result.testsRun,
        "checks": dict.fromkeys(sorted(REQUIRED_CHECKS), True),
        "scope": "Synthetic repositories, real init container and read-only mount; not serving correctness or approval",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "report.json"
    report.write_bytes(canonical_bytes(evidence) + b"\n")
    qualified = Qualification.model_validate(
        {
            "image": image,
            "platform": platform,
            "fixture_revision": FIXTURE_REVISION,
            "checks": evidence["checks"],
            "evidence": {"uri": "report.json", "digest": digest(evidence)},
        }
    )
    (args.output / "qualification.json").write_text(json.dumps(qualified.model_dump(mode="json"), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
