#!/usr/bin/env python3
"""Assemble the input bundle for the weekly profile-suggest stage.

Gathers the last N days of archived distill memos (`logs/*/memo.md`),
falling back to recent delivered briefs (`out/*.md`) until enough memos
have accrued, plus the recent-feedback digest
(`.tmp/feedback_recent.md`). Writes a single bundle to
`.tmp/suggest_input.md` that `prompts/suggest_profile.md` reads
alongside the current profile.

The memo is the richer signal (it already separates Active threads /
Open questions / Persistent interests), so memos are preferred whenever
any fall in the window; briefs are only used when no memos do.

Usage:
    build_suggest_input.py [--days N] [--logs-dir DIR] [--out-dir DIR]
                           [--digest PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def within(stem: str, cutoff: date) -> bool:
    """Run-id stems start YYYY-MM-DD; keep those on/after cutoff."""
    try:
        d = datetime.strptime(stem[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return d >= cutoff


def collect_memos(logs_dir: Path, cutoff: date) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for memo in sorted(logs_dir.glob("*/memo.md")):
        run_id = memo.parent.name
        if within(run_id, cutoff):
            out.append((run_id, memo.read_text(errors="replace")))
    return out


def collect_briefs(out_dir: Path, cutoff: date) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for brief in sorted(out_dir.glob("*.md")):
        if brief.name.endswith(".feedback.md") or "_backup" in brief.name:
            continue
        if within(brief.stem, cutoff):
            out.append((brief.stem, brief.read_text(errors="replace")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--logs-dir", type=Path, default=Path("logs"))
    ap.add_argument("--out-dir", type=Path, default=Path("out"))
    ap.add_argument("--digest", type=Path, default=Path(".tmp/feedback_recent.md"))
    ap.add_argument("--out", type=Path, default=Path(".tmp/suggest_input.md"))
    args = ap.parse_args()

    cutoff = date.today() - timedelta(days=args.days)
    memos = collect_memos(args.logs_dir, cutoff)
    source = "memos"
    if memos:
        entries = memos
    else:
        entries = collect_briefs(args.out_dir, cutoff)
        source = "briefs (no archived memos in window yet)"

    parts: list[str] = [f"# Profile-suggest inputs (last {args.days} days)", ""]
    if entries:
        label = "Daily distill memos" if source == "memos" else "Recent delivered briefs (memo fallback)"
        parts.append(f"## {label}")
        parts.append("")
        for run_id, text in entries:
            parts.append(f"### {run_id}")
            parts.append("")
            parts.append(text.strip())
            parts.append("")
    else:
        parts.append("## Daily memos")
        parts.append("(no memos or briefs in window)")
        parts.append("")

    parts.append("## Recent card feedback")
    parts.append("")
    if args.digest.exists():
        parts.append(args.digest.read_text(errors="replace").strip())
    else:
        parts.append("(no feedback digest available)")
    parts.append("")

    if not entries and not args.digest.exists():
        print("no memos, briefs, or feedback to build suggest input from.", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(parts))
    print(f"wrote suggest input ({len(entries)} {source}) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
