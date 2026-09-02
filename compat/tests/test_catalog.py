#!/usr/bin/env python3

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))

from catalog import list_firmwares, list_release_mappings  # noqa: E402


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.config = Path(__file__).resolve().parents[1] / "config"

    def test_smoke_profile(self):
        self.assertEqual(
            [item["id"] for item in list_firmwares(self.config, "smoke")],
            ["1.3", "3.0.1", "head"],
        )

    def test_nightly_profile_has_every_release_and_head(self):
        ids = [item["id"] for item in list_firmwares(self.config, "nightly")]
        self.assertEqual(ids, [
            "1.3", "1.5.2", "1.6.1", "1.6.2",
            "2.0.0", "2.0.1", "3.0.0", "3.0.1", "head",
        ])

    def test_release_mapping(self):
        mappings = {item["id"]: item["core_commit"] for item in list_release_mappings(self.config)}
        self.assertEqual(mappings, {
            "1.3": "5f1e95f8341856d994abb4566995e2379cc0612d",
            "1.5.2": "b16e8c517ed72fe26e5101b450a99df2b3526aa1",
            "1.6.1": "e022053a87e10d5d1e655f9cc59ecb0207160e09",
            "1.6.2": "0ac63dfb52805c77af5b19e51b9c5ae19a741c92",
            "2.0.0": "e90f851fe220082a9864136862d3253ab57c96f0",
            "2.0.1": "be6325b8c4e6d40e86b2943f65083ed6b71f8259",
            "3.0.0": "7cb33508a69ce4d281a053e1e53e6d006469076b",
            "3.0.1": "69e562bcb07eedda015aae6064870c8548571e2b",
        })

    def test_1_3_is_the_oldest_supported_firmware(self):
        catalog = json.loads((self.config / "firmwares.yaml").read_text())
        supported = {item["id"] for item in catalog["firmwares"]}
        self.assertIn("1.3", supported)
        self.assertEqual(catalog["aliases"]["oldest-supported"], "1.3")

    def test_supported_releases_match_firmware_core_map(self):
        mappings = {item["id"]: item["core_commit"] for item in list_release_mappings(self.config)}
        for item in list_firmwares(self.config, None):
            if item["id"] in mappings:
                self.assertEqual(item["verified_sha"], mappings[item["id"]])

    def test_unknown_profile(self):
        with self.assertRaises(ValueError):
            list_firmwares(self.config, "missing")


if __name__ == "__main__":
    unittest.main()
