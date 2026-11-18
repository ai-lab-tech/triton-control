"""Qualification evidence must come from successful tests of the actual image."""

import unittest

from scripts.qualify_materializer import CHECK_TESTS, KUBERNETES_TEST, measured_checks


class QualificationEvidenceTests(unittest.TestCase):
    def test_host_unit_tests_cannot_qualify_image_checks(self):
        # Arrange
        passed = {
            "tests.test_materialization.MaterializationTests.test_restart_reuses_verified_bytes_without_source_access"
        }
        # Act
        checks = measured_checks(passed)
        # Assert
        self.assertFalse(any(checks.values()))

    def test_missing_retained_restart_cannot_be_claimed_from_fresh_pods(self):
        # Arrange
        passed = {KUBERNETES_TEST + name for names in CHECK_TESTS.values() for name in names}
        passed.remove(KUBERNETES_TEST + "test_retained_volume_restart_without_storage")
        # Act
        checks = measured_checks(passed)
        # Assert
        self.assertFalse(checks["restart"])
        self.assertTrue(checks["readonly_mount"])

    def test_all_required_image_tests_produce_complete_checks(self):
        # Arrange
        passed = {KUBERNETES_TEST + name for names in CHECK_TESTS.values() for name in names}
        # Act
        checks = measured_checks(passed)
        # Assert
        self.assertTrue(all(checks.values()))
