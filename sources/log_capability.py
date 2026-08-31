#!/usr/bin/env python3
"""Append the security-relevant flags from one exact Pi invocation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


VALUE_FLAGS = {"--extension", "--model", "--provider", "--skill", "--thinking", "--tools"}


def parse_pi_command(command: list[str]) -> dict:
    """Extract only capability/provenance flags, never prompts or secrets."""
    values: dict[str, list[str]] = {flag: [] for flag in VALUE_FLAGS}
    switches = {
        "--no-tools": False,
        "--no-builtin-tools": False,
        "--no-context-files": False,
        "--no-extensions": False,
        "--no-skills": False,
    }
    index = 0
    while index < len(command):
        argument = command[index]
        if argument in switches:
            switches[argument] = True
        elif argument in VALUE_FLAGS and index + 1 < len(command):
            values[argument].append(command[index + 1])
            index += 1
        index += 1
    tools: list[str] = []
    for value in values["--tools"]:
        tools.extend(item.strip() for item in value.split(",") if item.strip())
    return {
        "no_tools": switches["--no-tools"],
        "no_builtin_tools": switches["--no-builtin-tools"],
        "no_context_files": switches["--no-context-files"],
        "no_extensions": switches["--no-extensions"],
        "no_skills": switches["--no-skills"],
        "tools": tools,
        "extensions": values["--extension"],
        "skills": values["--skill"],
        "provider": values["--provider"][-1] if values["--provider"] else None,
        "model": values["--model"][-1] if values["--model"] else None,
        # Recorded so a stage that asks for a thinking level the catalog
        # would silently drop is visible in the run record, not only in
        # the preflight that gates it.
        "thinking": values["--thinking"][-1] if values["--thinking"] else None,
    }


def append_record(path: Path, stage: str, slot: str | None, command: list[str]) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "slot": slot,
        **parse_pi_command(command),
    }
    payload = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        # O_APPEND plus one write keeps parallel expand workers from sharing a
        # computed file offset and overwriting one another's JSON fragments.
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("distill", "scout", "plan", "expand"))
    parser.add_argument("--slot")
    # Split at the first `--` ourselves: argparse.REMAINDER matches
    # positionals greedily, so `expand --slot 01 -- pi ...` would swallow
    # `--slot 01` into the command and silently record slot=None.
    argv = sys.argv[1:]
    if "--" in argv:
        cut = argv.index("--")
        own_args, command = argv[:cut], argv[cut + 1 :]
    else:
        own_args, command = argv, []
    args = parser.parse_args(own_args)
    if not command:
        parser.error("a Pi command is required after --")
    raw_path = os.environ.get("PI_PULSE_CAPABILITY_LOG")
    if not raw_path:
        parser.error("PI_PULSE_CAPABILITY_LOG is not set")
    append_record(Path(raw_path), args.stage, args.slot, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
