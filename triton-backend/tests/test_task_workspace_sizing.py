"""Actual byte totals, capacity bounds and explicit size requests."""

import unittest

from app.exceptions import BadRequestError
from app.schemas.preparation_settings import PreparationLimits
from app.services.task_workspace_sizing import GIB, storage_size


class WorkspaceSizingTests(unittest.TestCase):
    def test_actual_inputs_and_working_space_are_rounded_up(self):
        # Arrange
        limits = PreparationLimits()
        cases = [(1, "6Gi"), (4 * GIB, "9Gi"), (10 * GIB, "15Gi"),
                 (16 * GIB, "24Gi"), (20 * GIB + 1, "31Gi")]
        for total, expected in cases:
            with self.subTest(total=total):
                # Act
                result = storage_size(total, limits)
                # Assert
                self.assertEqual(result, expected)

    def test_explicit_larger_size_is_preserved(self):
        # Arrange
        limits = PreparationLimits()
        # Act
        result = storage_size(10 * GIB, limits, "30Gi")
        # Assert
        self.assertEqual(result, "30Gi")

    def test_invalid_or_out_of_policy_explicit_sizes_are_rejected(self):
        # Arrange
        limits = PreparationLimits()
        for size in ("14Gi", "51Gi", "0", "-1Gi", "NaN", "Infinity", "garbage"):
            with self.subTest(size=size):
                # Act / Assert
                with self.assertRaises(BadRequestError):
                    storage_size(10 * GIB, limits, size)

    def test_insufficient_admin_maximum_rejects_instead_of_truncating(self):
        # Arrange
        limits = PreparationLimits(max_workspace_storage_gib=20)
        # Act / Assert
        with self.assertRaisesRegex(BadRequestError, "requires 24 GiB"):
            storage_size(16 * GIB, limits)
