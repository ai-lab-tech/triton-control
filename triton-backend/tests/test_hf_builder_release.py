"""Release selection and promotion guards for the HF builder updater."""

import tempfile
import unittest
from pathlib import Path

from scripts import hf_builder_release as release


class HfBuilderReleaseTests(unittest.TestCase):
    def test_SelectCandidate_UsesNewestStableNonYankedVersion(self):
        # Arrange
        releases = {
            "0.9.0": [{"yanked": False}], "0.10.0": [{"yanked": True}, {"yanked": False}],
            "1.0.0rc1": [{"yanked": False}], "1.0.0.dev1": [{"yanked": False}],
            "9.0.0": [{"yanked": True}], "10.0.0": [], "invalid": [{"yanked": False}],
        }
        # Act
        selected = release.select_candidate(releases)
        # Assert
        self.assertEqual(selected, "0.10.0")

    def test_SelectCandidate_ExplicitVersionMustBePublishedStableAndUsable(self):
        for candidate in ("1.0.0rc1", "2.0.0", "missing", "1.0; echo invalid"):
            with self.subTest(candidate=candidate):
                # Arrange
                releases = {"1.0.0": [{"yanked": False}], "2.0.0": [{"yanked": True}]}
                # Act / Assert
                with self.assertRaises(ValueError):
                    release.select_candidate(releases, candidate)

    def test_SelectCandidate_AllYanked_Fails(self):
        # Arrange
        releases = {"1.0.0": [{"yanked": True}]}
        # Act / Assert
        with self.assertRaises(ValueError):
            release.select_candidate(releases)

    def _root(self, readme=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / release.VERSION_FILE).parent.mkdir(parents=True)
        (root / release.VERSION_FILE).write_text("1.0.0\n")
        (root / release.README).parent.mkdir(parents=True)
        (root / release.README).write_text(readme or (
            f"Intro\n{release.START}\nTested builder version: `1.0.0`.\n{release.END}\nInstructions\n"
        ))
        return root

    def test_Promote_UpdatesConfigurationAndGeneratedReadmeTogether(self):
        # Arrange
        root = self._root()
        # Act
        release.promote("1.1.0", root)
        # Assert
        self.assertEqual(release.tested_version(root), "1.1.0")
        readme = (root / release.README).read_text()
        self.assertIn("Tested builder version: `1.1.0`.", readme)
        self.assertTrue(readme.startswith("Intro\n"))
        self.assertTrue(readme.endswith("Instructions\n"))

    def test_Promote_InvalidReadme_DoesNotChangeTestedDefault(self):
        # Arrange
        root = self._root("Missing markers\n")
        # Act
        with self.assertRaises(ValueError):
            release.promote("1.1.0", root)
        # Assert
        self.assertEqual(release.tested_version(root), "1.0.0")
        self.assertEqual((root / release.README).read_text(), "Missing markers\n")

    def test_Promote_EqualOrOlderVersion_RetainsCurrentFiles(self):
        for candidate in ("1.0.0", "0.9.0"):
            with self.subTest(candidate=candidate):
                # Arrange
                root = self._root()
                readme = (root / release.README).read_text()
                # Act
                with self.assertRaises(ValueError):
                    release.promote(candidate, root)
                # Assert
                self.assertEqual(release.tested_version(root), "1.0.0")
                self.assertEqual((root / release.README).read_text(), readme)
