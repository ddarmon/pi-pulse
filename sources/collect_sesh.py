#!/usr/bin/env python3
"""Collect recent sesh sessions for the distill stage.

Uses `sesh sessions` JSON to find sessions within the last --since N
days, groups them by project path, and emits the markdown export of
each (truncated head + tail). Provides a view of recent coding work
to feed the distill prompt. Requires `sesh` on $PATH; see
https://github.com/ddarmon/sesh.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{text[-half:]}"


def find_sesh(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("sesh")
    if found:
        return found
    print(
        "ERROR: `sesh` not found on PATH. Install from "
        "https://github.com/ddarmon/sesh and ensure it is on $PATH, or "
        "pass --sesh /path/to/sesh.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", type=int, default=7, help="Days of history (default: 7)")
    ap.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Per-session truncation budget (default: 4000)",
    )
    ap.add_argument(
        "--deny-projects",
        default="",
        help="Comma-separated substring deny list applied to project_path",
    )
    ap.add_argument(
        "--providers",
        default="claude,codex,cursor,copilot,pi",
        help="Comma-separated provider allow list",
    )
    ap.add_argument("--sesh", default=None, help="Path to sesh binary")
    ap.add_argument(
        "--exclude-cwd",
        action="append",
        default=[],
        help="Drop sessions whose project_path is this directory or a descendant. Repeatable.",
    )
    args = ap.parse_args()

    sesh = find_sesh(args.sesh)
    deny = [s.strip() for s in args.deny_projects.split(",") if s.strip()]
    allow = {s.strip() for s in args.providers.split(",") if s.strip()}

    excludes: list[Path] = []
    for raw in args.exclude_cwd:
        try:
            excludes.append(Path(raw).expanduser().resolve(strict=False))
        except (OSError, RuntimeError) as exc:
            print(f"WARN: could not resolve --exclude-cwd {raw!r}: {exc}", file=sys.stderr)

    # Build/refresh the index. sesh CLI subcommands like `sessions` and
    # `export` require an index; if it is missing or stale, every call
    # fails with exit 1 and pulse.sh dies under `set -e`. Refresh is
    # idempotent and takes a few seconds.
    try:
        subprocess.check_call(
            [sesh, "refresh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        print(f"WARN: `sesh refresh` failed: {exc}", file=sys.stderr)

    try:
        raw = subprocess.check_output([sesh, "sessions"], text=True)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: `sesh sessions` failed: {exc}", file=sys.stderr)
        return 1

    sessions = json.loads(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.since)

    recent: list[dict] = []
    for s in sessions:
        ts = s.get("timestamp")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t < cutoff:
            continue
        if s.get("provider") not in allow:
            continue
        path = s.get("project_path", "") or ""
        if any(d in path for d in deny):
            continue
        if excludes and path:
            try:
                rp = Path(path).expanduser().resolve(strict=False)
                if any(rp == e or rp.is_relative_to(e) for e in excludes):
                    continue
            except (OSError, RuntimeError):
                if any(path == str(e) or path.startswith(str(e) + os.sep) for e in excludes):
                    continue
        recent.append(s)

    print(f"# sesh sessions (last {args.since} days, {len(recent)} sessions)")
    print()

    if not recent:
        print("(no sessions in window)")
        return 0

    by_project: dict[str, list[dict]] = defaultdict(list)
    for s in recent:
        by_project[s.get("project_path", "(unknown)")].append(s)

    total_in = total_out = 0
    for project in sorted(by_project):
        print(f"\n## {project}\n")
        for s in sorted(by_project[project], key=lambda x: x.get("timestamp", ""), reverse=True):
            sid = s["id"]
            ts = s.get("timestamp", "?")
            summary = (s.get("summary") or "").strip().replace("\n", " ")[:160]
            provider = s.get("provider", "?")
            model = s.get("model", "?")
            print(f"### {provider} | {model} | {ts} | {sid}")
            print(f"_Summary:_ {summary}")
            print()
            try:
                md = subprocess.check_output(
                    [sesh, "export", sid, "--provider", provider, "--format", "md"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError as exc:
                print(f"_(export failed: {exc})_\n")
                continue
            total_in += len(md)
            truncated = truncate(md, args.max_chars)
            total_out += len(truncated)
            print(truncated)
            print()

    print(
        f"\n# Bundle stats: {total_in} chars in, {total_out} chars out",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
