#!/usr/bin/env python3
"""Firmware catalog and profile expansion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json_yaml(path: Path) -> dict:
    # JSON is a strict YAML subset and keeps this public helper dependency-free.
    return json.loads(path.read_text())


def list_firmwares(config_dir: Path, profile: str | None) -> list[dict]:
    catalog = load_json_yaml(config_dir / "firmwares.yaml")
    entries = catalog["firmwares"]
    by_id = {entry["id"]: entry for entry in entries}
    aliases = catalog["aliases"]
    if profile is None:
        return entries
    profiles = load_json_yaml(config_dir / "profiles.yaml")["profiles"]
    if profile not in profiles:
        raise ValueError(f"unknown profile: {profile}")
    selected: list[dict] = []
    seen: set[str] = set()
    for requested in profiles[profile]["firmwares"]:
        ids = [entry["id"] for entry in entries if entry["id"] != "head"] if requested == "all-supported-releases" else [aliases.get(requested, requested)]
        for firmware_id in ids:
            if firmware_id not in by_id:
                raise ValueError(f"profile {profile} references unknown firmware: {firmware_id}")
            if firmware_id not in seen:
                selected.append(by_id[firmware_id])
                seen.add(firmware_id)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit canokey-core firmware matrix JSON")
    parser.add_argument("--profile", help="profile from compat/config/profiles.yaml")
    parser.add_argument("--compact", action="store_true", help="emit single-line JSON for GitHub outputs")
    args = parser.parse_args()
    config_dir = Path(__file__).resolve().parents[1] / "config"
    try:
        entries = list_firmwares(config_dir, args.profile)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(entries, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
