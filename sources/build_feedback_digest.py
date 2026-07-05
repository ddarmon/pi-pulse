#!/usr/bin/env python3
"""Roll the feedback ledger into a recent-feedback digest.

Reads `memory/feedback.jsonl`, keeps rows within the last N days
(default 14), and writes a grouped markdown digest to
`.tmp/feedback_recent.md`. The digest feeds both the daily plan stage
(attached to the compose_plan pi call) and the weekly profile-suggest
stage: it summarizes what landed, what didn't, and which topics the
user explicitly does not want. A `## Tendencies` section up top gives
per-tag counts and mean ratings so a ranker can read the drift at a
glance.

Always writes a file (a "(no feedback in window)" stub when empty) so
downstream consumers can attach it unconditionally.

Usage:
    build_feedback_digest.py [--days N] [--ledger PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def load_rows(ledger: Path, cutoff: date) -> list[dict]:
    if not ledger.exists():
        return []
    rows: list[dict] = []
    for line in ledger.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            d = datetime.strptime(obj.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= cutoff:
            rows.append(obj)
    return rows


def fmt(row: dict) -> str:
    mark = {2: "++", 1: "+", 0: "=", -1: "-", -2: "--"}.get(row.get("rating"), "?")
    tag = row.get("tag", "")
    note = row.get("note", "")
    line = f"- [{row.get('date', '')}] ({mark}) {row.get('title', '')}"
    if tag:
        line += f" [{tag}]"
    if note:
        line += f" -- note: {note}"
    return line


TAG_ORDER = ["tracked", "adjacent", "bridge", "follow-up"]


def tendencies(rows: list[dict]) -> list[str]:
    """One `- tag: N rated, mean +X.X` line per tag with rated rows."""
    by_tag: dict[str, list[int]] = {}
    for r in rows:
        tag = r.get("tag", "")
        if not tag:
            continue
        by_tag.setdefault(tag, []).append(r.get("rating", 0))
    ordered = [t for t in TAG_ORDER if t in by_tag]
    ordered += sorted(t for t in by_tag if t not in TAG_ORDER)
    lines = []
    for tag in ordered:
        ratings = by_tag[tag]
        mean = sum(ratings) / len(ratings)
        lines.append(f"- {tag}: {len(ratings)} rated, mean {mean:+.1f}")
    return lines


def render(rows: list[dict], days: int) -> str:
    valued = sorted([r for r in rows if r.get("rating", 0) > 0], key=lambda r: r.get("rating", 0), reverse=True)
    neutral = [r for r in rows if r.get("rating") == 0]
    disliked = [r for r in rows if r.get("rating", 0) < 0]
    avoid = [r for r in rows if r.get("rating", 0) == -2]

    out = [f"# Recent feedback (last {days} days)", ""]
    if not rows:
        out.append("(no feedback in window)")
        out.append("")
        return "\n".join(out)

    out.append("## Tendencies")
    out.extend(tendencies(rows))
    out.append("")

    def section(heading: str, group: list[dict]) -> None:
        out.append(heading)
        if group:
            out.extend(fmt(r) for r in group)
        else:
            out.append("(none)")
        out.append("")

    section("## Valued (more like this)", valued)
    section("## Neutral (reviewed, no strong opinion)", neutral)
    section("## Not valued (less like this)", disliked)
    section("## Avoid candidates (rated [--])", avoid)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--ledger", type=Path, default=Path("memory/feedback.jsonl"))
    ap.add_argument("--out", type=Path, default=Path(".tmp/feedback_recent.md"))
    args = ap.parse_args()

    cutoff = date.today() - timedelta(days=args.days)
    rows = load_rows(args.ledger, cutoff)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, args.days))
    print(f"wrote digest of {len(rows)} ratings to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
