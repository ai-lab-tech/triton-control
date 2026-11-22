"""Admission, storage accounting and collision regression tests (AAA)."""

import json
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from app.core.canonical import inventory_digest
from app.schemas.materialization import MaterializationPlan, StorageBinding
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.budgets import configured_limits
from app.services.materialization.kubernetes import manifests
from app.services.materialization.normalization import MaterializationError
from app.services.materialization.runtime import materialize
from tests.materialization_fixtures import IMAGE, LIMITS, plan, policy, serving_release


def render(desired, name="iris", limits=LIMITS):
    release = serving_release()
    repository = release.spec.repository.model_copy(
        update={
            "inventory_digest": inventory_digest(desired.source_inventory),
            "prepared_inventory_digest": inventory_digest(desired.prepared_inventory),
        }
    )
    release = release.model_copy(update={"spec": release.spec.model_copy(update={"repository": repository})})
    return manifests(
        release,
        desired,
        policy(),
        StorageBinding(bucket="models", prefix="repository", secret_name="read-only"),
        name=name,
        namespace="test",
        limits=limits,
    )


class MaterializationBudgetTests(unittest.TestCase):
    def test_twenty_gib_plan_carries_budget_and_reserves_both_copies(self):
        # Arrange: change only declared sizes; do not allocate 40 GiB in a unit test.
        payload = plan().model_dump(mode="json")
        for key in ("source_inventory", "prepared_inventory"):
            payload[key]["files"][0]["size"] = 20 * 1024**3
        desired = MaterializationPlan.model_validate(payload)
        expected = (
            sum(x.size for inv in (desired.source_inventory, desired.prepared_inventory) for x in inv.files) + 1024**3
        )
        # Act
        config, pod = render(desired)
        # Assert
        self.assertEqual(json.loads(config["data"]["limits.json"]), asdict(LIMITS))
        spec = pod["spec"]
        self.assertEqual(int(spec["volumes"][0]["emptyDir"]["sizeLimit"]), expected)
        init = spec["initContainers"][0]
        self.assertEqual(int(init["resources"]["requests"]["ephemeral-storage"]), expected)
        self.assertIn("/plan/limits.json", init["args"])

    def test_over_budget_plan_is_rejected_before_download_or_pod_creation(self):
        # Arrange
        limits = CaptureLimits(max_bytes=1)
        source = MagicMock()
        with TemporaryDirectory() as folder:
            root = Path(folder) / "out"
            # Act / Assert
            with self.assertRaises(MaterializationError):
                materialize(plan(), source, root, expected_image=IMAGE, limits=limits)
            with self.assertRaises(MaterializationError):
                render(plan(), limits=limits)
            source.download.assert_not_called()
            self.assertFalse(root.exists())

    def test_long_names_have_distinct_stable_configmaps(self):
        # Arrange
        names = ["a" * 46 + "-one", "a" * 46 + "-two"]
        # Act
        results = [render(plan(), name) for name in names]
        repeated = render(plan(), names[0])
        # Assert
        self.assertNotEqual(results[0][0]["metadata"]["name"], results[1][0]["metadata"]["name"])
        self.assertEqual(results[0][0]["metadata"]["name"], repeated[0]["metadata"]["name"])
        for config, pod in results:
            self.assertLessEqual(len(config["metadata"]["name"]), 63)
            self.assertEqual(pod["spec"]["volumes"][1]["configMap"]["name"], config["metadata"]["name"])

    def test_changed_budget_gets_a_new_immutable_configmap(self):
        # Arrange
        changed = CaptureLimits(max_bytes=LIMITS.max_bytes, max_file_bytes=LIMITS.max_file_bytes, deadline_seconds=7200)
        # Act
        before, _ = render(plan())
        after, _ = render(plan(), limits=changed)
        # Assert
        self.assertNotEqual(before["metadata"]["name"], after["metadata"]["name"])

    def test_changed_plan_gets_a_new_immutable_configmap(self):
        # Arrange
        payload = plan().model_dump(mode="json")
        payload["objects"][0]["version_id"] = "v2"
        changed = MaterializationPlan.model_validate(payload)
        # Act
        before, _ = render(plan())
        after, _ = render(changed)
        # Assert
        self.assertNotEqual(before["metadata"]["name"], after["metadata"]["name"])

    def test_limits_document_requires_explicit_finite_budgets(self):
        # Arrange
        valid = {"max_bytes": 40 * 1024**3, "max_file_bytes": 40 * 1024**3, "deadline_seconds": 3600}
        invalid = [
            {},
            {**valid, "max_bytes": True},
            {**valid, "deadline_seconds": float("inf")},
            {**valid, "max_file_bytes": -1},
        ]
        # Act
        result = configured_limits(valid)
        # Assert
        self.assertEqual(result.max_bytes, 40 * 1024**3)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(MaterializationError):
                configured_limits(value)
