#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))

from harness import (  # noqa: E402
    CORE_COMPAT_PATCHES,
    CoreSource,
    Harness,
    LinuxPlatform,
    Options,
    PhaseError,
)


class FakePlatform:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.device_path = None
        self.usb_port = "0"
        self.owns_attachment = False
        self.detached = False
        self.collected = False
        self.fail_detach = False
        self.attach_error: PhaseError | None = None

    def set_usb_identity(self, _usb_vid, _usb_pid):
        pass

    def prepare_host(self):
        pass

    def matching_usb_devices(self):
        return set()

    def attach(self, _before):
        self.owns_attachment = True
        if self.attach_error:
            raise self.attach_error

    def wait_ready(self):
        return {
            "usb": True,
            "ccid_interface": True,
            "hid_interface": True,
            "webusb_interface": True,
            "hidraw": True,
            "pcsc_checked": False,
            "pcsc": True,
            "pcsc_readers": [],
            "pcsc_all_readers": [],
        }

    def collect_debug(self):
        self.collected = True
        (self.output_dir / "usbip-port.txt").write_text("fake port\n")

    def detach(self):
        self.detached = True
        try:
            if self.fail_detach:
                raise RuntimeError("fake detach failure")
        finally:
            self.owns_attachment = False


class FakeHarness(Harness):
    def __init__(self, options: Options):
        super().__init__(options)
        self.platform = FakePlatform(self.output_dir)
        self.resolve_error: PhaseError | None = None
        self.build_error: PhaseError | None = None
        self.start_error: PhaseError | None = None
        self.lock_error: PhaseError | None = None

    def acquire_lock(self):
        if self.lock_error:
            raise self.lock_error

    def resolve_core(self):
        if self.resolve_error:
            raise self.resolve_error
        path = self.workspace / "core"
        path.mkdir()
        return CoreSource(path, "a" * 40, self.options.core_ref or "external", False)

    def build(self):
        if self.build_error:
            raise self.build_error
        binary = self.workspace / "build" / "canokey-usbip"
        binary.parent.mkdir()
        binary.write_text("fake")
        return binary

    def start_server(self):
        if self.start_error:
            raise self.start_error
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        self.storage.touch(exist_ok=True)
        self.server = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.write_state(self.server.pid, None)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "artifacts"

    def tearDown(self):
        self.temp.cleanup()

    def options(self, **changes):
        values = dict(
            repo_dir=Path(__file__).resolve().parents[2],
            caller_dir=self.root,
            core_ref=None,
            core_dir=self.root / "source-core",
            test_command="printf success",
            storage=None,
            timeout=5,
            keep_on_failure=False,
            output_dir=self.output,
            build_only=False,
            build_dir=None,
        )
        values.update(changes)
        return Options(**values)

    def metadata(self):
        return json.loads((self.output / "metadata.json").read_text())

    def test_invalid_core_ref(self):
        runner = FakeHarness(self.options(core_ref="missing", core_dir=None))
        runner.resolve_error = PhaseError("resolve-core", "invalid ref")
        self.assertEqual(runner.execute(), 1)
        self.assertFalse(runner.workspace.exists())
        self.assertEqual(self.metadata()["canokey_core_sha"], "unknown")

    def test_lock_failure_does_not_detach_active_device(self):
        runner = FakeHarness(self.options())
        runner.lock_error = PhaseError("lock", "already active")
        self.assertEqual(runner.execute(), 1)
        self.assertFalse(runner.platform.detached)
        self.assertEqual(self.metadata()["phase"], "lock")

    def test_build_failure_generates_metadata_and_cleans(self):
        runner = FakeHarness(self.options())
        runner.build_error = PhaseError("build", "failed")
        self.assertEqual(runner.execute(), 1)
        self.assertEqual(self.metadata()["phase"], "build")
        self.assertFalse(runner.workspace.exists())

    def test_start_failure(self):
        runner = FakeHarness(self.options())
        runner.start_error = PhaseError("start", "failed")
        self.assertEqual(runner.execute(), 1)
        self.assertEqual(self.metadata()["phase"], "start")

    def test_attach_timeout(self):
        runner = FakeHarness(self.options())
        runner.platform.attach_error = PhaseError("attach", "timeout")
        self.assertEqual(runner.execute(), 1)
        self.assertEqual(self.metadata()["phase"], "attach")
        self.assertTrue(runner.platform.detached)

    def test_test_command_success(self):
        runner = FakeHarness(self.options())
        self.assertEqual(runner.execute(), 0)
        self.assertEqual((self.output / "test.stdout").read_text(), "success")
        self.assertEqual(self.metadata()["exit_code"], 0)

    def test_test_command_failure_propagates(self):
        runner = FakeHarness(self.options(test_command="exit 23"))
        self.assertEqual(runner.execute(), 23)
        self.assertEqual(self.metadata()["exit_code"], 23)

    def test_cleanup_runs_after_success_and_failure(self):
        success = FakeHarness(self.options())
        self.assertEqual(success.execute(), 0)
        self.assertTrue(success.platform.detached)
        self.assertTrue(success.platform.collected)

        failure_output = self.root / "failure-artifacts"
        failure = FakeHarness(self.options(output_dir=failure_output, test_command="exit 2"))
        self.assertEqual(failure.execute(), 2)
        self.assertTrue(failure.platform.detached)
        self.assertFalse(failure.workspace.exists())

    def test_cleanup_error_does_not_mask_test_result(self):
        runner = FakeHarness(self.options(test_command="exit 7"))
        runner.platform.fail_detach = True
        self.assertEqual(runner.execute(), 7)
        self.assertIn("detach", (self.output / "cleanup-error.txt").read_text())

    def test_cleanup_error_fails_an_otherwise_successful_run(self):
        runner = FakeHarness(self.options())
        runner.platform.fail_detach = True
        self.assertEqual(runner.execute(), 1)
        self.assertEqual(self.metadata()["phase"], "cleanup")

    def test_explicit_storage_is_preserved(self):
        storage = self.root / "persistent.lfs"
        runner = FakeHarness(self.options(storage=storage))
        self.assertEqual(runner.execute(), 0)
        self.assertTrue(storage.exists())
        self.assertTrue(self.metadata()["storage_is_explicit"])

    def test_temporary_storage_is_removed(self):
        runner = FakeHarness(self.options())
        storage = runner.storage
        self.assertEqual(runner.execute(), 0)
        self.assertFalse(storage.exists())

    def test_keep_on_failure_preserves_workspace(self):
        runner = FakeHarness(self.options(test_command="exit 3", keep_on_failure=True))
        self.assertEqual(runner.execute(), 3)
        self.assertTrue(runner.workspace.exists())
        self.assertTrue(self.metadata()["workspace_preserved"])

    def test_timeout_returns_124(self):
        runner = FakeHarness(self.options(test_command="sleep 5", timeout=1))
        self.assertEqual(runner.execute(), 124)
        self.assertEqual(self.metadata()["exit_code"], 124)

    def test_public_environment(self):
        command = (
            "test \"$CANOKEY_USBIP\" = 1 && "
            "test -n \"$CANOKEY_CORE_SHA\" && "
            "test -n \"$CANOKEY_STORAGE\" && "
            "test -n \"$CANOKEY_TEST_OUTPUT\""
        )
        runner = FakeHarness(self.options(test_command=command))
        self.assertEqual(runner.execute(), 0)

    def test_legacy_patch_is_applied_only_to_core_snapshot(self):
        sha = next(iter(CORE_COMPAT_PATCHES))
        source = self.root / "source-core" / "virt-card"
        source.mkdir(parents=True)
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        (snapshot / "virt-card").mkdir()
        runner = Harness(self.options())

        patches = runner.apply_core_compatibility(snapshot, sha)

        self.assertEqual(patches, ("core-1.3-legacy-device-sim.patch",))
        self.assertTrue((snapshot / "virt-card" / "device-sim.c").is_file())
        self.assertFalse((source / "device-sim.c").exists())

    def test_legacy_patch_is_not_applied_to_unknown_core(self):
        snapshot = self.root / "snapshot"
        (snapshot / "virt-card").mkdir(parents=True)
        runner = Harness(self.options())
        self.assertEqual(runner.apply_core_compatibility(snapshot, "f" * 40), ())
        self.assertFalse((snapshot / "virt-card" / "device-sim.c").exists())

    def test_core_usb_identity_supports_non_default_vid_pid(self):
        core = self.root / "core" / "interfaces" / "USB" / "device"
        core.mkdir(parents=True)
        (core / "usbd_desc.h").write_text("#define USBD_VID 0x1677\n#define USBD_PID 0x0025\n")
        self.assertEqual(Harness.core_usb_identity(self.root / "core"), ("1677", "0025"))

    def test_pcsc_readiness_uses_reader_added_by_attachment(self):
        device = self.root / "1-1"
        device.mkdir()
        platform = LinuxPlatform(self.output, 1, "1677", "0025")
        platform.device_path = device
        platform.pcsc_readers_before = {"Existing reader"}
        with (
            mock.patch.object(platform, "_interface_classes", return_value={"0b", "03", "ff"}),
            mock.patch.object(platform, "_hidraw_ready", return_value=True),
            mock.patch.object(
                platform,
                "pcsc_readers",
                return_value=["Existing reader", "Attached virtual reader"],
            ),
        ):
            status = platform.wait_ready()
        self.assertTrue(status["pcsc"])
        self.assertEqual(status["pcsc_readers"], ["Attached virtual reader"])

    def test_interface_classes_are_read_below_usb_device(self):
        device = self.root / "1-1"
        interface = device / "1-1:1.0"
        interface.mkdir(parents=True)
        (interface / "bInterfaceClass").write_text("0b\n")
        platform = LinuxPlatform(self.output, 1)
        platform.device_path = device
        self.assertEqual(platform._interface_classes(), {"0b"})

    def test_prepare_host_requires_usbfs(self):
        platform = LinuxPlatform(self.output, 1)
        with (
            mock.patch.object(platform, "require_host"),
            mock.patch.object(platform, "vhci_available", return_value=True),
            mock.patch.object(platform, "usbfs_available", return_value=False),
        ):
            with self.assertRaisesRegex(PhaseError, "/dev/bus/usb") as raised:
                platform.prepare_host()
        self.assertEqual(raised.exception.phase, "host")


if __name__ == "__main__":
    unittest.main()
