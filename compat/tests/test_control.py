#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))

import control  # noqa: E402


class FakeProcess:
    pid = 4242
    returncode = None

    def poll(self):
        return None


class ReadyResult:
    returncode = 0
    stdout = "busid 1-1\n"


class FailingPlatform:
    instances = []

    def __init__(self, _output_dir, _timeout):
        self.usb_port = None
        self.owns_attachment = False
        self.detached = False
        self.__class__.instances.append(self)

    def prepare_host(self):
        pass

    def set_usb_identity(self, _usb_vid, _usb_pid):
        pass

    def matching_usb_devices(self):
        return set()

    def attach(self, _before):
        self.owns_attachment = True
        self.usb_port = "0"

    def wait_ready(self):
        raise RuntimeError("readiness failed")

    def detach(self):
        self.detached = True
        self.owns_attachment = False


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "artifacts"
        self.output.mkdir()
        self.state_path = self.root / "state.json"
        self.state = {
            "pid": None,
            "usb_port": None,
            "binary": "fake-server",
            "storage": str(self.root / "device.lfs"),
            "output_dir": str(self.output),
            "timeout": 1,
            "touch": False,
        }
        FailingPlatform.instances.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_readiness_failure_cleans_process_attachment_and_state(self):
        with (
            mock.patch.object(control, "LinuxPlatform", FailingPlatform),
            mock.patch.object(control.subprocess, "Popen", return_value=FakeProcess()),
            mock.patch.object(control, "run_command", return_value=ReadyResult()),
            mock.patch.object(control, "stop_pid") as stop_pid,
        ):
            with self.assertRaisesRegex(RuntimeError, "readiness failed"):
                control.start(self.state_path, self.state)

        stop_pid.assert_called_once_with(FakeProcess.pid)
        self.assertTrue(FailingPlatform.instances[0].detached)
        saved = json.loads(self.state_path.read_text())
        self.assertIsNone(saved["pid"])
        self.assertIsNone(saved["usb_port"])


if __name__ == "__main__":
    unittest.main()
