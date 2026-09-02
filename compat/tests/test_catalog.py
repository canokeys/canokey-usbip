#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))

from catalog import list_firmwares  # noqa: E402


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.config = Path(__file__).resolve().parents[1] / "config"

    def test_smoke_profile(self):
        self.assertEqual([item["id"] for item in list_firmwares(self.config, "smoke")], ["1.5.2", "3.0.3", "head"])

    def test_nightly_profile_has_every_release_and_head(self):
        ids = [item["id"] for item in list_firmwares(self.config, "nightly")]
        self.assertEqual(ids, ["1.5.2", "1.6.0", "1.6.2", "2.0.0", "2.0.1", "3.0.0", "3.0.2", "3.0.3", "head"])

    def test_unknown_profile(self):
        with self.assertRaises(ValueError):
            list_firmwares(self.config, "missing")


if __name__ == "__main__":
    unittest.main()
