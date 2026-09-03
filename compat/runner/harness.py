#!/usr/bin/env python3
"""Virtual CanoKey lifecycle used by compat/run."""

from __future__ import annotations

import ctypes
import ctypes.util
import datetime as dt
import fcntl
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Sequence


DEFAULT_VID = "20a0"
DEFAULT_PID = "42d4"
BUS_ID = "1-1"
USBIP_PORT = 3240
CORE_URL = "https://github.com/canokeys/canokey-core.git"
CORE_COMPAT_PATCHES = {
    "5f1e95f8341856d994abb4566995e2379cc0612d": ("core-1.3-legacy-device-sim.patch",),
    "e1ee3710d97f2d6350d67fa0937a7ee2974a3e9c": ("core-3.1.0-fabrication.patch",),
}
CORE_COMPAT_PATCH_SKIP_IF_PRESENT = {
    "core-1.3-legacy-device-sim.patch": Path("virt-card/device-sim.c"),
}
DEFAULT_READINESS_STATUS = (
    "usb",
    "ccid_interface",
    "hid_interface",
    "webusb_interface",
    "hidraw",
    "pcsc",
)


class HarnessError(RuntimeError):
    pass


class PhaseError(HarnessError):
    def __init__(self, phase: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.phase = phase
        self.exit_code = exit_code


@dataclass
class Options:
    repo_dir: Path
    caller_dir: Path
    core_ref: str | None
    core_dir: Path | None
    test_command: str | None
    storage: Path | None
    timeout: int
    keep_on_failure: bool
    output_dir: Path
    build_only: bool = False
    build_dir: Path | None = None
    touch: bool = False
    readiness_requirements: tuple[str, ...] = ()


@dataclass
class CoreSource:
    path: Path
    sha: str
    ref: str
    dirty: bool
    patches: tuple[str, ...] = ()
    usb_vid: str = DEFAULT_VID
    usb_pid: str = DEFAULT_PID
    firmware_version: str | None = None


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    log: IO[str] | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv), cwd=cwd, timeout=timeout, text=True,
        stdout=log if log else subprocess.PIPE,
        stderr=subprocess.STDOUT if log else subprocess.PIPE,
        env=env,
    )
    if check and result.returncode != 0:
        detail = "" if log else (result.stdout or result.stderr or "").strip()
        raise HarnessError(f"command failed ({result.returncode}): {' '.join(argv)}\n{detail}")
    return result


def git_value(path: Path, *args: str, default: str = "unknown") -> str:
    try:
        return run_command(["git", "-C", str(path), *args]).stdout.strip()
    except (HarnessError, OSError):
        return default


def tool_version(argv: Sequence[str]) -> str:
    try:
        result = run_command(argv, check=False, timeout=5)
        value = (result.stdout or result.stderr or "").splitlines()
        return value[0].strip() if value else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def copy_core_tree(source: Path, destination: Path) -> None:
    ignored_names = {".git", "build", "cmake-build-debug", "cmake-build-release"}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored_names or name.startswith("cmake-build-")}

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


class LinuxPlatform:
    def __init__(
        self,
        output_dir: Path,
        timeout: int,
        usb_vid: str = DEFAULT_VID,
        usb_pid: str = DEFAULT_PID,
        readiness_requirements: Sequence[str] = (),
    ):
        self.output_dir = output_dir
        self.timeout = timeout
        self.usb_vid = usb_vid
        self.usb_pid = usb_pid
        self.readiness_requirements = tuple(readiness_requirements)
        self.device_path: Path | None = None
        self.usb_port: str | None = None
        self.owns_attachment = False
        self.pcsc_readers_before: set[str] = set()

    @staticmethod
    def privileged(argv: Sequence[str]) -> list[str]:
        if os.geteuid() == 0:
            return list(argv)
        return ["sudo", "-n", *argv]

    @staticmethod
    def vhci_available() -> bool:
        return Path("/sys/devices/platform/vhci_hcd.0").exists()

    @staticmethod
    def usbfs_available() -> bool:
        return Path("/dev/bus/usb").is_dir()

    def require_host(self) -> None:
        if sys.platform != "linux":
            raise PhaseError("host", "USB/IP attach is supported on Linux only")
        missing = [name for name in ("usbip",) if shutil.which(name) is None]
        if not self.vhci_available() and shutil.which("modprobe") is None:
            missing.append("modprobe")
        if os.geteuid() != 0 and shutil.which("sudo") is None:
            missing.append("sudo")
        if missing:
            raise PhaseError("host", f"missing required command(s): {', '.join(missing)}")

    def prepare_host(self) -> None:
        self.require_host()
        if not self.vhci_available():
            run_command(self.privileged(["modprobe", "vhci_hcd"]), timeout=20)
        if not self.vhci_available():
            raise PhaseError("host", "vhci_hcd loaded but its sysfs device is unavailable")
        if not self.usbfs_available():
            raise PhaseError(
                "host",
                "usbfs device nodes are unavailable at /dev/bus/usb; use a Linux VM or runner with host USB support",
            )
        if shutil.which("systemctl") and shutil.which("pcscd"):
            run_command(self.privileged(["systemctl", "start", "pcscd"]), check=False, timeout=20)

    def set_usb_identity(self, usb_vid: str, usb_pid: str) -> None:
        self.usb_vid = usb_vid
        self.usb_pid = usb_pid

    def matching_usb_devices(self) -> set[Path]:
        devices: set[Path] = set()
        root = Path("/sys/bus/usb/devices")
        if not root.exists():
            return devices
        for vendor_file in root.glob("*/idVendor"):
            try:
                if vendor_file.read_text().strip().lower() != self.usb_vid:
                    continue
                device = vendor_file.parent
                if (device / "idProduct").read_text().strip().lower() == self.usb_pid:
                    devices.add(device.resolve())
            except OSError:
                continue
        return devices

    def attach(self, before: set[Path]) -> None:
        readers_before = self.pcsc_readers()
        self.pcsc_readers_before = set(readers_before or [])
        command = ["usbip", "--tcp-port", str(USBIP_PORT), "attach", "--remote", "127.0.0.1", "--busid", BUS_ID]
        try:
            run_command(self.privileged(command), timeout=30)
        except (HarnessError, subprocess.TimeoutExpired) as exc:
            raise PhaseError("attach", f"USB/IP attach failed: {exc}") from exc
        self.owns_attachment = True
        self.usb_port = self.find_usbip_port()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            candidates = self.matching_usb_devices() - before
            if candidates:
                self.device_path = sorted(candidates)[0]
                if self.usb_port is None:
                    self.usb_port = self.find_usbip_port()
                return
            time.sleep(0.25)
        raise PhaseError("attach", f"Timed out waiting for USB device after {self.timeout} seconds")

    def find_usbip_port(self) -> str | None:
        result = run_command(["usbip", "port"], check=False, timeout=10)
        blocks = re.split(r"(?=^Port\s+\d+:)", result.stdout or "", flags=re.MULTILINE)
        for block in blocks:
            identity = f"{self.usb_vid}:{self.usb_pid}"
            if identity in block.lower() or "CanoKey" in block or "1-1" in block:
                match = re.search(r"^Port\s+(\d+):", block, re.MULTILINE)
                if match:
                    return str(int(match.group(1)))
        return None

    def _interface_classes(self) -> set[str]:
        if not self.device_path:
            return set()
        classes: set[str] = set()
        for value in self.device_path.glob(f"{self.device_path.name}:*/bInterfaceClass"):
            try:
                classes.add(value.read_text().strip().lower())
            except OSError:
                pass
        return classes

    def _hidraw_ready(self) -> bool:
        if not self.device_path:
            return False
        base = str(self.device_path)
        for entry in Path("/sys/class/hidraw").glob("hidraw*/device"):
            try:
                if str(entry.resolve()).startswith(base):
                    return True
            except OSError:
                pass
        return False

    @staticmethod
    def pcsc_readers() -> list[str] | None:
        library_name = ctypes.util.find_library("pcsclite")
        if not library_name:
            return None
        try:
            library = ctypes.CDLL(library_name)
            establish = library.SCardEstablishContext
            list_readers = getattr(library, "SCardListReaders", None) or getattr(library, "SCardListReadersA", None)
            release = library.SCardReleaseContext
        except (OSError, AttributeError):
            return None
        if list_readers is None:
            return None
        context = ctypes.c_ulong()
        if establish(0, None, None, ctypes.byref(context)) != 0:
            return []
        try:
            length = ctypes.c_uint32(0)
            status = list_readers(context, None, None, ctypes.byref(length))
            if status != 0 or length.value == 0:
                return []
            buffer = ctypes.create_string_buffer(length.value)
            if list_readers(context, None, buffer, ctypes.byref(length)) != 0:
                return []
            return [item.decode(errors="replace") for item in buffer.raw.split(b"\0") if item]
        finally:
            release(context)

    def wait_ready(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        status: dict[str, Any] = {}
        requirement_status: dict[str, bool] = {}
        while time.monotonic() < deadline:
            classes = self._interface_classes()
            readers = self.pcsc_readers()
            new_readers = sorted(set(readers or []) - self.pcsc_readers_before)
            status = {
                "usb": bool(self.device_path and self.device_path.exists()),
                "ccid_interface": "0b" in classes,
                "hid_interface": "03" in classes,
                "webusb_interface": "ff" in classes,
                "hidraw": self._hidraw_ready(),
                "pcsc_checked": readers is not None,
                "pcsc_readers": new_readers,
                "pcsc_all_readers": readers or [],
                "pcsc": readers is None or bool(new_readers),
            }
            if not self.readiness_requirements:
                requirement_status = {name: bool(status[name]) for name in DEFAULT_READINESS_STATUS}
            else:
                all_requirements = {
                    "usb": status["usb"],
                    "ccid": status["ccid_interface"],
                    "hid": status["hid_interface"] and status["hidraw"],
                    "webusb": status["webusb_interface"],
                    "pcsc": status["pcsc_checked"] and bool(status["pcsc_readers"]),
                }
                requirement_status = {
                    name: bool(all_requirements[name]) for name in self.readiness_requirements
                }
            if all(requirement_status.values()):
                return status
            time.sleep(0.25)
        unmet = ", ".join(name for name, ready in requirement_status.items() if not ready)
        details = "\n".join(f"  {key}: {value}" for key, value in status.items())
        raise PhaseError(
            "readiness",
            f"Timed out waiting for virtual CanoKey after {self.timeout} seconds\n"
            f"Unmet readiness requirements: {unmet}\n{details}",
        )

    def detach(self) -> None:
        if not self.owns_attachment or shutil.which("usbip") is None:
            return
        port = self.usb_port or self.find_usbip_port()
        if port is None:
            return
        try:
            run_command(self.privileged(["usbip", "detach", "--port", port]), check=False, timeout=20)
        finally:
            self.owns_attachment = False
            self.usb_port = None
            self.device_path = None

    def collect_debug(self) -> None:
        commands = {
            "usbip-port.txt": ["usbip", "port"],
            "lsusb.txt": ["lsusb", "-v", "-d", f"{self.usb_vid}:{self.usb_pid}"],
            "kernel.txt": ["uname", "-a"],
        }
        for filename, command in commands.items():
            path = self.output_dir / filename
            if shutil.which(command[0]) is None:
                path.write_text(f"{command[0]} unavailable\n")
                continue
            with path.open("w") as log:
                run_command(command, log=log, check=False, timeout=20)
        if self.device_path and shutil.which("udevadm"):
            with (self.output_dir / "udev.txt").open("w") as log:
                run_command(["udevadm", "info", "--query=all", f"--path={self.device_path}"], log=log, check=False, timeout=20)
        if shutil.which("journalctl"):
            with (self.output_dir / "pcscd.log").open("w") as log:
                run_command(self.privileged(["journalctl", "-u", "pcscd", "--no-pager", "-n", "300"]), log=log, check=False, timeout=20)


class Harness:
    def __init__(self, options: Options):
        self.options = options
        self.run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        self.workspace = Path(tempfile.gettempdir()) / "canokey-usbip" / self.run_id
        self.output_dir = options.output_dir.resolve()
        self.storage = options.storage.resolve() if options.storage else self.workspace / "device.lfs"
        self.explicit_storage = options.storage is not None
        self.core: CoreSource | None = None
        self.binary: Path | None = None
        self.server: subprocess.Popen[bytes] | None = None
        self.platform = LinuxPlatform(
            self.output_dir,
            options.timeout,
            readiness_requirements=options.readiness_requirements,
        )
        self.metadata: dict[str, Any] = {}
        self.state_path = self.workspace / "state.json"
        self.lock_file: IO[str] | None = None
        self.debug = os.environ.get("CANOKEY_USBIP_DEBUG") == "1"

    def acquire_lock(self) -> None:
        lock_path = Path(tempfile.gettempdir()) / "canokey-usbip.lock"
        self.lock_file = lock_path.open("w")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PhaseError("lock", "another canokey-usbip environment is active on this runner") from exc

    def apply_core_compatibility(self, destination: Path, sha: str) -> tuple[str, ...]:
        patch_names = CORE_COMPAT_PATCHES.get(sha, ())
        applied = []
        patch_dir = self.options.repo_dir / "compat" / "patches"
        for patch_name in patch_names:
            skip_path = CORE_COMPAT_PATCH_SKIP_IF_PRESENT.get(patch_name)
            if skip_path is not None and (destination / skip_path).exists():
                continue
            try:
                run_command(
                    ["git", "apply", "--whitespace=nowarn", str(patch_dir / patch_name)],
                    cwd=destination,
                )
            except HarnessError as exc:
                raise PhaseError(
                    "resolve-core",
                    f"failed to apply canokey-core compatibility patch {patch_name}",
                ) from exc
            applied.append(patch_name)
        return tuple(applied)

    def firmware_version_for_sha(self, sha: str) -> str | None:
        catalog = json.loads(
            (self.options.repo_dir / "compat/config/firmwares.yaml").read_text()
        )
        for mapping in catalog["release_mappings"]:
            if mapping["core_commit"] == sha:
                return mapping["id"]
        return None

    @staticmethod
    def core_usb_identity(destination: Path) -> tuple[str, str]:
        descriptor = destination / "interfaces" / "USB" / "device" / "usbd_desc.h"
        try:
            content = descriptor.read_text()
        except OSError as exc:
            raise PhaseError("resolve-core", f"cannot read USB descriptor identity: {descriptor}") from exc

        values: dict[str, str] = {}
        for name in ("USBD_VID", "USBD_PID"):
            match = re.search(rf"^\s*#\s*define\s+{name}\s+(0[xX][0-9a-fA-F]+|[0-9]+)\b", content, re.MULTILINE)
            if not match:
                raise PhaseError("resolve-core", f"cannot resolve {name} from {descriptor}")
            values[name] = f"{int(match.group(1), 0):04x}"
        return values["USBD_VID"], values["USBD_PID"]

    def resolve_core(self) -> CoreSource:
        destination = self.workspace / "core"
        if self.options.core_ref:
            destination.mkdir(parents=True)
            run_command(["git", "init", "--quiet", str(destination)])
            run_command(["git", "-C", str(destination), "remote", "add", "origin", CORE_URL])
            try:
                run_command(["git", "-C", str(destination), "fetch", "--quiet", "--depth", "1", "origin", self.options.core_ref])
            except HarnessError as exc:
                raise PhaseError("resolve-core", f"invalid or unreachable canokey-core ref: {self.options.core_ref}") from exc
            run_command(["git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"])
            run_command(["git", "-C", str(destination), "submodule", "update", "--init", "--recursive", "--depth", "1"])
            sha = git_value(destination, "rev-parse", "HEAD")
            patches = self.apply_core_compatibility(destination, sha)
            usb_vid, usb_pid = self.core_usb_identity(destination)
            return CoreSource(
                destination,
                sha,
                self.options.core_ref,
                False,
                patches,
                usb_vid,
                usb_pid,
                firmware_version=self.firmware_version_for_sha(sha),
            )

        source = self.options.core_dir or self.options.repo_dir / "canokey-core"
        if not (source / "CMakeLists.txt").exists():
            raise PhaseError("resolve-core", f"not a canokey-core source tree: {source}")
        sha = git_value(source, "rev-parse", "HEAD")
        dirty = bool(git_value(source, "status", "--porcelain", default=""))
        try:
            copy_core_tree(source, destination)
        except OSError as exc:
            raise PhaseError("resolve-core", f"failed to snapshot canokey-core: {exc}") from exc
        if not (destination / "canokey-crypto" / "CMakeLists.txt").exists():
            raise PhaseError("resolve-core", "canokey-core submodules are missing; run git submodule update --init --recursive")
        ref = "external" if self.options.core_dir else "submodule"
        patches = self.apply_core_compatibility(destination, sha)
        usb_vid, usb_pid = self.core_usb_identity(destination)
        return CoreSource(
            destination,
            sha,
            ref,
            dirty,
            patches,
            usb_vid,
            usb_pid,
            firmware_version=self.firmware_version_for_sha(sha),
        )

    def build(self) -> Path:
        assert self.core
        if not self.core.firmware_version:
            raise PhaseError(
                "resolve-core",
                f"core {self.core.sha} has no firmware version mapping",
            )
        if self.options.build_dir:
            build_dir = self.options.build_dir.resolve()
        elif self.options.build_only:
            build_dir = self.options.caller_dir / "build" / "compat"
        else:
            build_dir = self.workspace / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "build.log").open("w") as log:
            try:
                run_command([
                    "cmake", "-S", str(self.options.repo_dir), "-B", str(build_dir),
                    f"-DCANOKEY_CORE_DIR={self.core.path}",
                    f"-DCANOKEY_CORE_SHA={self.core.sha}",
                    f"-DCANOKEY_FIRMWARE_VERSION={self.core.firmware_version}",
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
                ], log=log)
                run_command(["cmake", "--build", str(build_dir), "--target", "canokey-usbip", "--parallel"], log=log)
            except HarnessError as exc:
                raise PhaseError("build", "canokey-usbip build failed; see build.log") from exc
        binary = build_dir / "canokey-usbip"
        if not binary.is_file():
            raise PhaseError("build", f"build succeeded but binary is missing: {binary}")
        return binary

    def start_server(self) -> None:
        assert self.binary
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        log = (self.output_dir / "usbip.log").open("ab", buffering=0)
        command = [str(self.binary), str(self.storage), str(USBIP_PORT)]
        if self.options.touch:
            command.append("touch")
        self.server = subprocess.Popen(
            command,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.write_state(self.server.pid, None)
        deadline = time.monotonic() + min(self.options.timeout, 15)
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise PhaseError("start", f"canokey-usbip exited during startup ({self.server.returncode}); see usbip.log")
            try:
                result = run_command(["usbip", "--tcp-port", str(USBIP_PORT), "list", "--remote", "127.0.0.1"], check=False, timeout=3)
                if result.returncode == 0 and BUS_ID in (result.stdout or ""):
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.25)
        raise PhaseError("start", "canokey-usbip did not expose busid 1-1 before startup timeout")

    def write_state(self, pid: int, usb_port: str | None) -> None:
        state = {
            "pid": pid,
            "usb_port": usb_port,
            "binary": str(self.binary) if self.binary else None,
            "storage": str(self.storage),
            "output_dir": str(self.output_dir),
            "timeout": self.options.timeout,
            "touch": self.options.touch,
            "readiness_requirements": list(self.options.readiness_requirements),
            "usb_vid": self.core.usb_vid if self.core else DEFAULT_VID,
            "usb_pid": self.core.usb_pid if self.core else DEFAULT_PID,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")

    def run_test(self, ready: dict[str, Any]) -> int:
        assert self.core and self.options.test_command
        environment = os.environ.copy()
        environment.update({
            "CANOKEY_USBIP": "1",
            "CANOKEY_CORE_REF": self.core.ref,
            "CANOKEY_CORE_SHA": self.core.sha,
            "CANOKEY_FIRMWARE_VERSION": self.core.firmware_version,
            "CANOKEY_USBIP_SHA": self.metadata["canokey_usbip_sha"],
            "CANOKEY_STORAGE": str(self.storage),
            "CANOKEY_TEST_OUTPUT": str(self.output_dir),
            "CANOKEY_USBIP_STATE": str(self.state_path),
            "CANOKEY_USB_VID": self.core.usb_vid,
            "CANOKEY_USB_PID": self.core.usb_pid,
            "CANOKEY_DEVICE_RESTART": str(self.options.repo_dir / "compat/scripts/restart-device.sh"),
            "CANOKEY_DEVICE_TOUCH": str(self.options.repo_dir / "compat/scripts/touch-device.sh"),
        })
        if self.platform.device_path:
            bus = (self.platform.device_path / "busnum").read_text().strip()
            device = (self.platform.device_path / "devnum").read_text().strip()
            environment["CANOKEY_USB_BUS"] = bus
            environment["CANOKEY_USB_DEVICE"] = device
        readers = ready.get("pcsc_readers", [])
        if readers:
            environment["CANOKEY_PCSC_READER"] = readers[0]

        stdout_path = self.output_dir / "test.stdout"
        stderr_path = self.output_dir / "test.stderr"
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            process = subprocess.Popen(
                ["bash", "-lc", self.options.test_command],
                cwd=self.options.caller_dir, env=environment,
                stdout=stdout, stderr=stderr, start_new_session=True,
            )
            try:
                return process.wait(timeout=self.options.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                return 124

    def base_metadata(self) -> dict[str, Any]:
        requested_ref = self.options.core_ref or ("external" if self.options.core_dir else "submodule")
        return {
            "run_id": self.run_id,
            "canokey_usbip_sha": git_value(self.options.repo_dir, "rev-parse", "HEAD"),
            "canokey_usbip_dirty": bool(git_value(self.options.repo_dir, "status", "--porcelain", default="")),
            "canokey_core_sha": "unknown",
            "canokey_firmware_version": None,
            "canokey_core_ref": requested_ref,
            "canokey_core_dirty": None,
            "canokey_core_compat_patches": [],
            "usb_vid": DEFAULT_VID,
            "usb_pid": DEFAULT_PID,
            "kernel": platform.platform(),
            "usbip_version": tool_version(["usbip", "version"]),
            "cmake_version": tool_version(["cmake", "--version"]),
            "compiler": tool_version([os.environ.get("CC", "cc"), "--version"]),
            "storage": str(self.storage),
            "storage_is_explicit": self.explicit_storage,
            "test_command": self.options.test_command,
            "readiness_requirements": list(self.options.readiness_requirements),
            "caller_workspace": str(self.options.caller_dir),
            "run_workspace": str(self.workspace),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "capabilities": ["ccid", "hid", "webusb", "touch-simulation", "persistent-storage"],
        }

    def initial_metadata(self) -> dict[str, Any]:
        assert self.core
        metadata = self.base_metadata()
        metadata.update({
            "canokey_core_sha": self.core.sha,
            "canokey_firmware_version": self.core.firmware_version,
            "canokey_core_ref": self.core.ref,
            "canokey_core_dirty": self.core.dirty,
            "canokey_core_compat_patches": list(self.core.patches),
            "usb_vid": self.core.usb_vid,
            "usb_pid": self.core.usb_pid,
        })
        return metadata

    def save_metadata(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "metadata.json").write_text(json.dumps(self.metadata, indent=2, sort_keys=True) + "\n")

    def stop_server(self) -> None:
        pid: int | None = None
        if self.state_path.exists():
            try:
                pid = int(json.loads(self.state_path.read_text()).get("pid"))
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if self.server is not None and self.server.pid == pid:
                try:
                    self.server.wait(timeout=5)
                    return
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    self.server.wait(timeout=5)
                    return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self.server is not None and self.server.poll() is not None:
            self.server.wait()

    def execute(self) -> int:
        result = 1
        phase = "setup"
        failure: str | None = None
        ready: dict[str, Any] = {}
        self.workspace.mkdir(parents=True, exist_ok=False)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata = self.base_metadata()
        self.save_metadata()
        try:
            self.acquire_lock()
            phase = "resolve-core"
            self.core = self.resolve_core()
            self.platform.set_usb_identity(self.core.usb_vid, self.core.usb_pid)
            self.metadata = self.initial_metadata()
            self.save_metadata()
            phase = "build"
            self.binary = self.build()
            self.metadata["binary"] = str(self.binary)
            if self.options.build_only:
                result = 0
                print(self.binary)
            else:
                phase = "host"
                self.platform.prepare_host()
                before = self.platform.matching_usb_devices()
                phase = "start"
                self.start_server()
                phase = "attach"
                self.platform.attach(before)
                self.write_state(self.server.pid if self.server else 0, self.platform.usb_port)
                phase = "readiness"
                ready = self.platform.wait_ready()
                self.metadata["readiness"] = ready
                phase = "test"
                result = self.run_test(ready)
                if result != 0:
                    failure = f"test command exited with {result}"
        except PhaseError as exc:
            phase = exc.phase
            result = exc.exit_code
            failure = str(exc)
            print(f"canokey-usbip: {failure}", file=sys.stderr)
        except (HarnessError, OSError, subprocess.TimeoutExpired) as exc:
            failure = str(exc)
            print(f"canokey-usbip: {failure}", file=sys.stderr)
            result = 1
        finally:
            try:
                self.platform.collect_debug()
            except Exception as exc:  # diagnostic collection must not mask the test result
                (self.output_dir / "collect-debug-error.txt").write_text(f"{exc}\n")
            if self.debug:
                for name in ("usbip-port.txt", "lsusb.txt", "pcscd.log"):
                    path = self.output_dir / name
                    if path.exists():
                        print(f"--- {name} ---", file=sys.stderr)
                        print(path.read_text(errors="replace"), file=sys.stderr)
            if self.platform.owns_attachment:
                try:
                    self.platform.detach()
                except Exception as exc:
                    (self.output_dir / "cleanup-error.txt").write_text(f"detach: {exc}\n")
                    if result == 0:
                        result = 1
                        phase = "cleanup"
                        failure = f"detach failed: {exc}"
            try:
                self.stop_server()
            except Exception as exc:
                with (self.output_dir / "cleanup-error.txt").open("a") as log:
                    log.write(f"stop: {exc}\n")
                if result == 0:
                    result = 1
                    phase = "cleanup"
                    failure = f"stop failed: {exc}"
            if self.metadata:
                self.metadata.update({
                    "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "phase": phase,
                    "exit_code": result,
                    "failure": failure,
                    "workspace_preserved": bool((self.options.build_only and result == 0) or (result != 0 and self.options.keep_on_failure)),
                })
                self.save_metadata()
            preserve_workspace = (self.options.build_only and result == 0) or (result != 0 and self.options.keep_on_failure)
            if not preserve_workspace:
                shutil.rmtree(self.workspace, ignore_errors=True)
        return result
