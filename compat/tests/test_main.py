#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))

from main import parse_args  # noqa: E402


class MainTests(unittest.TestCase):
    def test_require_is_repeatable(self):
        options = parse_args([
            "--require", "usb",
            "--require", "ccid",
            "--require", "pcsc",
            "--test-command", "true",
        ])
        self.assertEqual(options.readiness_requirements, ("usb", "ccid", "pcsc"))

    def test_default_has_no_explicit_requirements(self):
        options = parse_args(["--test-command", "true"])
        self.assertEqual(options.readiness_requirements, ())


if __name__ == "__main__":
    unittest.main()
