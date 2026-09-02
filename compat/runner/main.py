#!/usr/bin/env python3
"""CLI for the CanoKey virtual hardware integration harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness import Harness, Options


def parse_args(argv: list[str]) -> Options:
    repo_dir = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Build and run an isolated virtual CanoKey over USB/IP")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--core-ref", help="canokey-core tag, branch, or commit to fetch")
    source.add_argument("--core-dir", type=Path, help="existing canokey-core source tree")
    parser.add_argument("--test-command", help="caller-owned command to execute after readiness")
    parser.add_argument("--storage", type=Path, help="persistent LittleFS image (preserved after the run)")
    parser.add_argument("--timeout", type=int, default=60, help="readiness and test-command timeout in seconds (default: 60)")
    parser.add_argument("--keep-on-failure", action="store_true", help="preserve the isolated run workspace on failure")
    parser.add_argument("--touch", action="store_true", help="enable firmware user-presence checks and touch helper")
    parser.add_argument("--output-dir", type=Path, help="artifact directory (default: artifacts/<run-id>)")
    parser.add_argument("--build-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--build-dir", type=Path, help="build directory (primarily for compat/build)")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if not args.build_only and not args.test_command:
        parser.error("--test-command is required")
    caller_dir = Path.cwd().resolve()
    output_dir = args.output_dir
    if output_dir is None:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output_dir = caller_dir / "artifacts" / stamp
    return Options(
        repo_dir=repo_dir,
        caller_dir=caller_dir,
        core_ref=args.core_ref,
        core_dir=args.core_dir.resolve() if args.core_dir else None,
        test_command=args.test_command,
        storage=args.storage.resolve() if args.storage else None,
        timeout=args.timeout,
        keep_on_failure=args.keep_on_failure,
        output_dir=output_dir.resolve(),
        build_only=args.build_only,
        build_dir=args.build_dir,
        touch=args.touch,
    )


if __name__ == "__main__":
    raise SystemExit(Harness(parse_args(sys.argv[1:])).execute())
