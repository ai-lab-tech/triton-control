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
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import TypeAdapter  # noqa: E402

from app.core.canonical import canonical_bytes, digest  # noqa: E402
from app.schemas.materialization import FIXTURE_REVISION, Qualification  # noqa: E402
from app.schemas.serving_metadata import Image  # noqa: E402
from app.services.materialization.qualification import REQUIRED_CHECKS  # noqa: E402

KUBERNETES_TEST = "tests.test_materialization_kubernetes.MaterializerKubernetesTests."
CHECK_TESTS = {
    "pod_local_readiness": {"test_replica_local_version_receipt_and_slow_load"},
    "gpu_capacity": {"test_insufficient_gpu_keeps_pod_pending"},
    "exact_versions": {"test_pinned_init_readonly_runtime_and_recreated_pod", "test_rejects_version"},
    "source_hash": {"test_rejects_corrupt"},
    "prepared_hash": {"test_rejects_prepared_hash"},
    "missing_file": {"test_rejects_missing"},
    "extra_file": {"test_rejects_extra"},
    "unsafe_path": {"test_rejects_unsafe"},
    "interrupted_download": {"test_rejects_interrupted", "test_interrupted_download_recovers_on_same_volume"},
    "restart": {"test_retained_volume_restart_without_storage", "test_corrupt_retained_volume_blocks_restart"},
    "readonly_mount": {"test_pinned_init_readonly_runtime_and_recreated_pod"},
    "normalization": {"test_pinned_init_readonly_runtime_and_recreated_pod", "test_rejects_normalization"},
    "changed_helper": {"test_changed_helper_cannot_adopt_retained_volume"},
    "no_runtime_install": {"test_pinned_init_readonly_runtime_and_recreated_pod"},
}


class QualificationResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.passed: set[str] = set()

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.add(test.id())


def measured_checks(passed: set[str]) -> dict[str, bool]:
    if set(CHECK_TESTS) != REQUIRED_CHECKS:
        raise ValueError("Qualification checks need explicit fixture mappings")
    return {check: all(KUBERNETES_TEST + name in passed for name in tests) for check, tests in CHECK_TESTS.items()}


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
    result = cast(QualificationResult, unittest.TextTestRunner(verbosity=2, resultclass=QualificationResult).run(suite))
    checks = measured_checks(result.passed)
    if not result.wasSuccessful() or result.skipped or not all(checks.values()):
        return 1
    evidence = {
        "image": image,
        "platform": platform,
        "fixture_revision": FIXTURE_REVISION,
        "tests_run": result.testsRun,
        "checks": checks,
        "scope": "Real Kubernetes, synthetic S3 and Triton HTTP fixtures; no inference or GPU performance qualification",
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
