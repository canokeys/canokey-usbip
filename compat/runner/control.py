#!/usr/bin/env python3
"""Operate the device created by compat/run from a caller test command."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from harness import BUS_ID, DEFAULT_PID, DEFAULT_VID, USBIP_PORT, LinuxPlatform, run_command


def load_state() -> tuple[Path, dict]:
    value = os.environ.get("CANOKEY_USBIP_STATE")
    if not value:
        raise RuntimeError("CANOKEY_USBIP_STATE is not set; run this helper inside compat/run")
    path = Path(value)
    return path, json.loads(path.read_text())


def stop_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            if stat_path.exists() and stat_path.read_text().split()[2] == "Z":
                return
        except (OSError, IndexError):
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def start(state_path: Path, state: dict) -> tuple[LinuxPlatform, dict]:
    output_dir = Path(state["output_dir"])
    platform = LinuxPlatform(output_dir, int(state["timeout"]))
    platform.set_usb_identity(
        state.get("usb_vid", DEFAULT_VID),
        state.get("usb_pid", DEFAULT_PID),
    )
    platform.prepare_host()
    before = platform.matching_usb_devices()
    log = (output_dir / "usbip.log").open("ab", buffering=0)
    command = [state["binary"], state["storage"], str(USBIP_PORT)]
    if state.get("touch"):
        command.append("touch")
    process = subprocess.Popen(
        command,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    log.close()
    state.update({"pid": process.pid, "usb_port": None})
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    try:
        deadline = time.monotonic() + min(int(state["timeout"]), 15)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"canokey-usbip exited during restart ({process.returncode})")
            result = run_command(
                ["usbip", "--tcp-port", str(USBIP_PORT), "list", "--remote", "127.0.0.1"],
                check=False, timeout=3,
            )
            if result.returncode == 0 and BUS_ID in (result.stdout or ""):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("canokey-usbip did not become ready during restart")
        platform.attach(before)
        platform.wait_ready()
        state["usb_port"] = platform.usb_port
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        return platform, state
    except BaseException:
        if platform.owns_attachment:
            try:
                platform.detach()
            except Exception:
                pass
        stop_pid(process.pid)
        state.update({"pid": None, "usb_port": None})
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop", "attach", "detach", "restart", "touch", "collect-debug"))
    args = parser.parse_args()
    state_path, state = load_state()
    platform = LinuxPlatform(Path(state["output_dir"]), int(state["timeout"]))
    platform.set_usb_identity(
        state.get("usb_vid", DEFAULT_VID),
        state.get("usb_pid", DEFAULT_PID),
    )
    platform.usb_port = state.get("usb_port")
    platform.owns_attachment = platform.usb_port is not None

    if args.command in ("stop", "restart"):
        platform.detach()
        stop_pid(state.get("pid"))
        state.update({"pid": None, "usb_port": None})
        state_path.write_text(json.dumps(state, indent=2) + "\n")
    if args.command in ("start", "restart"):
        start(state_path, state)
    elif args.command == "attach":
        before = platform.matching_usb_devices()
        platform.attach(before)
        platform.wait_ready()
        state["usb_port"] = platform.usb_port
        state_path.write_text(json.dumps(state, indent=2) + "\n")
    elif args.command == "detach":
        platform.detach()
        state["usb_port"] = None
        state_path.write_text(json.dumps(state, indent=2) + "\n")
    elif args.command == "touch":
        os.kill(int(state["pid"]), signal.SIGINT)
    elif args.command == "collect-debug":
        platform.collect_debug()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"canokey-usbip control: {exc}", file=sys.stderr)
        raise SystemExit(1)
