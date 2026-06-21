#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "agent_cli_audit.py"
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$")


def load_audit() -> list[dict]:
    completed = subprocess.run(
        [str(AUDIT), "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    return payload["installed"]


def parse_version(version: str) -> tuple[int, int, int, int]:
    match = SEMVER_RE.match(version)
    if not match:
        return (-1, -1, -1, -1)
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    suffix = match.group(4)
    stable_rank = 1 if not suffix else 0
    return (major, minor, patch, stable_rank)


def is_upgrade_candidate(item: dict) -> bool:
    current = item.get("current_version")
    latest = item.get("latest_version")
    if not current or not latest:
        return False
    if parse_version(latest) <= parse_version(current):
        return False
    if item.get("channel_status") not in {"recommended", "supported"}:
        return False
    if item.get("update_command") == "See official install docs":
        return False
    return True


def channel_matches(item: dict, channel: str) -> bool:
    status = item.get("channel_status")
    if channel == "all":
        return status in {"recommended", "supported"}
    if channel == "supported":
        return status in {"recommended", "supported"}
    return status == "recommended"


def build_plan(items: list[dict], selected: set[str] | None, channel: str) -> list[dict]:
    plan = []
    for item in items:
        if selected and item["id"] not in selected:
            continue
        if not channel_matches(item, channel):
            continue
        if is_upgrade_candidate(item):
            plan.append(item)
    return plan


def run_shell(command: str) -> int:
    completed = subprocess.run(command, shell=True, text=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe wrapper for upgrading audited agent CLIs.")
    parser.add_argument("--tool", action="append", dest="tools", help="Tool id to upgrade. Repeatable.")
    parser.add_argument("--apply", action="store_true", help="Execute upgrades. Without this flag, only print the plan.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation when used with --apply.")
    parser.add_argument("--channel", choices=["recommended", "supported", "all"], default="supported", help="Filter upgrade candidates by install-channel policy. 'recommended' is strict; 'supported' and 'all' currently both include recommended + supported entries.")
    parser.add_argument("--recommended-only", action="store_true", help="Deprecated compatibility alias for --channel recommended.")
    args = parser.parse_args()

    selected = set(args.tools or [])
    channel = "recommended" if args.recommended_only else args.channel
    items = load_audit()
    plan = build_plan(items, selected if selected else None, channel)

    if not plan:
        print("No upgrade candidates matched the current filters.")
        return 0

    print("Upgrade plan:")
    for item in plan:
        risk = item.get("release_summary", {}).get("risk_level", "unknown")
        print(
            f"- {item['id']}: {item['current_version']} -> {item['latest_version']} "
            f"[class={item.get('tooling_class', 'agent-cli')}, {item['channel_status']}, risk={risk}] via {item['update_command']}"
        )

    if not args.apply:
        print()
        print("Dry run only. Re-run with --apply to execute.")
        return 0

    if not args.yes:
        reply = input("Proceed with these upgrades? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            print("Aborted.")
            return 1

    failures = 0
    for item in plan:
        print()
        print(f"==> upgrading {item['id']}")
        code = run_shell(item["update_command"])
        if code != 0:
            failures += 1
            print(f"FAILED: {item['id']} exited with code {code}")

    if failures:
        print()
        print(f"Completed with {failures} failure(s).")
        return 1

    print()
    print("All selected upgrades completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
