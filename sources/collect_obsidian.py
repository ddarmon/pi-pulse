#!/usr/bin/env python3
"""Collect recent notes for the distill stage.

Globs <vault>/YYYY/MM/DD/*.md for the last --since N days, where the
vault root is supplied via $PI_PULSE_NOTES_DIR or --vault. The layout
is the one produced by exporting LLM chats into a date-bucketed
folder hierarchy (e.g. Obsidian, plain markdown). Each file is
truncated to head + tail (~4k chars by default) so a busy month stays
under the model context. Output is a single markdown bundle with ###
file headers, written to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ENV_VAR = "PI_PULSE_NOTES_DIR"


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{text[-half:]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", type=int, default=30, help="Days of history (default: 30)")
    ap.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Per-file truncation budget (default: 4000)",
    )
    ap.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=f"Notes directory (default: ${ENV_VAR})",
    )
    args = ap.parse_args()

    vault = args.vault or (Path(os.environ[ENV_VAR]).expanduser() if ENV_VAR in os.environ else None)
    if vault is None:
        print(
            f"ERROR: set {ENV_VAR} or pass --vault. "
            "Expected: directory tree of YYYY/MM/DD/*.md.",
            file=sys.stderr,
        )
        return 2
    if not vault.is_dir():
        print(f"# Notes directory not found at {vault}", file=sys.stderr)
        return 1

    today = date.today()
    cutoff = today - timedelta(days=args.since)

    files: list[Path] = []
    for year_dir in sorted(vault.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                try:
                    d = date(int(year_dir.name), int(month_dir.name), int(day_dir.name))
                except ValueError:
                    continue
                if d < cutoff or d > today:
                    continue
                files.extend(sorted(day_dir.glob("*.md")))

    print(f"# Notes (last {args.since} days, {len(files)} files)")
    print()
    print(f"Window: {cutoff.isoformat()} -- {today.isoformat()}")
    print()

    if not files:
        print("(no notes in window)")
        return 0

    total_in = total_out = 0
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError as exc:
            print(f"# WARN: could not read {f}: {exc}", file=sys.stderr)
            continue
        total_in += len(text)
        truncated = truncate(text, args.max_chars)
        total_out += len(truncated)
        rel = f.relative_to(vault)
        print(f"\n### {rel}\n")
        print(truncated)

    print(
        f"\n# Bundle stats: {total_in} chars in, {total_out} chars out",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
