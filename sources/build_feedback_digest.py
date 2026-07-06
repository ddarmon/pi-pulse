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

Windowing keys on the brief's DELIVERY date, parsed from the leading
`YYYY-MM-DD` of each row's `run_id` (handles both the legacy
`YYYY-MM-DD` and the `YYYY-MM-DD-HHMM` forms), falling back to the
row's `date` field (the rating date) only when `run_id` is missing or
unparseable. This keeps a bulk rating session -- one day where the user
rates months of old cards -- from making stale cards look current.

`--max-per-section N` caps each of the four rating sections to its top
N rows (by absolute rating strength, then delivery recency), appending
a "(... and K more not shown)" line to any truncated section. The
default (0) is unlimited. `## Tendencies` is always computed over every
in-window row, not the truncated subset.

Usage:
    build_feedback_digest.py [--days N] [--ledger PATH] [--out PATH]
                             [--max-per-section N]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


# Legacy briefs carried a "## Dropped from this run" section; some of
# those dropped-card lines got rated and ingested. Filter them out by
# exact (stripped) title so they never reach a section or Tendencies.
DROPPED_TITLE = "Dropped from this run"


def delivery_date(row: dict) -> date | None:
    """Brief delivery date: leading YYYY-MM-DD of `run_id`.

    `run_id` is `YYYY-MM-DD` (legacy) or `YYYY-MM-DD-HHMM`; either way
    the first ten chars are the delivery date. Fall back to the `date`
    field (the rating date) only when `run_id` is missing/unparseable.
    """
    run_id = row.get("run_id", "")
    if isinstance(run_id, str) and len(run_id) >= 10:
        try:
            return datetime.strptime(run_id[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    try:
        return datetime.strptime(row.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        return None


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
        if str(obj.get("title", "")).strip() == DROPPED_TITLE:
            continue
        d = delivery_date(obj)
        if d is None or d < cutoff:
            continue
        # Stash the windowed delivery date so fmt/sort agree with the
        # value we filtered on (more meaningful than the rating date).
        obj["_delivery"] = d
        rows.append(obj)
    return rows


def _deliv(row: dict) -> date:
    """Delivery date for display/sort, tolerating rows not from load_rows."""
    d = row.get("_delivery")
    if isinstance(d, date):
        return d
    return delivery_date(row) or date.min


def fmt(row: dict) -> str:
    mark = {2: "++", 1: "+", 0: "=", -1: "-", -2: "--"}.get(row.get("rating"), "?")
    tag = row.get("tag", "")
    note = row.get("note", "")
    d = _deliv(row)
    shown = d.isoformat() if d != date.min else row.get("date", "")
    line = f"- [{shown}] ({mark}) {row.get('title', '')}"
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


def _ranked(group: list[dict]) -> list[dict]:
    """Strongest opinions first, then newest delivery date first."""
    return sorted(group, key=lambda r: (abs(r.get("rating", 0)), _deliv(r)), reverse=True)


def render(rows: list[dict], days: int, max_per_section: int = 0) -> str:
    valued = _ranked([r for r in rows if r.get("rating", 0) > 0])
    neutral = _ranked([r for r in rows if r.get("rating") == 0])
    disliked = _ranked([r for r in rows if r.get("rating", 0) < 0])
    avoid = _ranked([r for r in rows if r.get("rating", 0) == -2])

    out = [f"# Recent feedback (last {days} days)", ""]
    if not rows:
        out.append("(no feedback in window)")
        out.append("")
        return "\n".join(out)

    # Tendencies always spans every in-window row, never the truncated set.
    out.append("## Tendencies")
    out.extend(tendencies(rows))
    out.append("")

    def section(heading: str, group: list[dict]) -> None:
        out.append(heading)
        shown = group if max_per_section <= 0 else group[:max_per_section]
        if shown:
            out.extend(fmt(r) for r in shown)
        else:
            out.append("(none)")
        hidden = len(group) - len(shown)
        if hidden > 0:
            out.append(f"- (... and {hidden} more not shown)")
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
    ap.add_argument(
        "--max-per-section",
        type=int,
        default=0,
        help="Cap each rating section to its top N rows (0 = unlimited).",
    )
    args = ap.parse_args()

    cutoff = date.today() - timedelta(days=args.days)
    rows = load_rows(args.ledger, cutoff)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, args.days, args.max_per_section))
    print(f"wrote digest of {len(rows)} ratings to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
