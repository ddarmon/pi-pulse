#!/usr/bin/env python3
"""Ingest an edited feedback companion file into the feedback ledger.

Reads `out/<RUN_ID>.feedback.md` (the user-edited marks), re-joins each
rated card against `out/<RUN_ID>.md` to recover its title, primary URL,
and tag, and writes one JSONL row per rated card to
`memory/feedback.jsonl`.

Idempotent: re-ingesting a run replaces that run's rows rather than
duplicating them, so you can re-edit and re-ingest freely.

Card N is the Nth `## ` heading in the brief -- the same numbering
`build_feedback_template.py` emits.

Usage:
    ingest_feedback.py <RUN_ID> [--feedback PATH] [--brief PATH]
                                [--ledger PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from append_seen import MD_LINK, normalize

CARD_HEADING = re.compile(r"^##\s+(.*\S)\s*$")
# A rated line: "[mark] N  title". The mark is the bracket content
# (may be empty or spaces for unrated).
RATING_LINE = re.compile(r"^\[(?P<mark>[^\]]*)\]\s*(?P<num>\d+)\s+(?P<title>.*\S)\s*$")
NOTE_LINE = re.compile(r"^\s+note:\s*(?P<note>.*\S)\s*$", re.IGNORECASE)
TAG_SUFFIX = re.compile(r"\((tracked|adjacent|bridge|follow-up)\)\s*$", re.IGNORECASE)

# Rated states. "=" is an explicit neutral (reviewed, no strong opinion)
# and is distinct from unrated -- an empty/space bracket -- which is
# skipped entirely (not yet reviewed). Neutral produces a rating-0 row.
MARKS = {"++": 2, "+": 1, "=": 0, "-": -1, "--": -2}


def parse_cards(brief_md: str) -> list[dict]:
    """Return [{title, url, tag}] in card order from a delivered brief."""
    lines = brief_md.splitlines()
    # Index of each card heading.
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = CARD_HEADING.match(line)
        if m:
            heads.append((i, m.group(1).strip()))

    cards: list[dict] = []
    for j, (idx, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        body = "\n".join(lines[idx + 1 : end])
        link = MD_LINK.search(body)
        url = normalize(link.group(1)) if link else ""
        tag_m = TAG_SUFFIX.search(title)
        tag = tag_m.group(1).lower() if tag_m else "tracked"
        cards.append({"title": title, "url": url, "tag": tag})
    return cards


def parse_feedback(feedback_md: str) -> list[dict]:
    """Return [{card, rating, note}] for rated (non-skip) cards."""
    rated: list[dict] = []
    last: dict | None = None
    for line in feedback_md.splitlines():
        if line.startswith("#"):
            continue
        rm = RATING_LINE.match(line)
        if rm:
            mark = rm.group("mark").strip()
            num = int(rm.group("num"))
            if mark in MARKS:
                last = {"card": num, "rating": MARKS[mark], "note": ""}
                rated.append(last)
            else:
                last = None  # unrated -> skip, and detach any note
            continue
        nm = NOTE_LINE.match(line)
        if nm and last is not None:
            last["note"] = nm.group("note")
    return rated


def rows_for_run(run_id: str, feedback_path: Path, brief_path: Path) -> list[dict] | None:
    """Build ledger rows for one run, or None if its files are missing."""
    if not feedback_path.exists():
        print(f"no feedback file: {feedback_path}", file=sys.stderr)
        return None
    if not brief_path.exists():
        print(f"no brief: {brief_path}", file=sys.stderr)
        return None

    cards = parse_cards(brief_path.read_text(errors="replace"))
    rated = parse_feedback(feedback_path.read_text(errors="replace"))
    today = date.today().isoformat()

    rows: list[dict] = []
    counts = {2: 0, 1: 0, 0: 0, -1: 0, -2: 0}
    for r in rated:
        n = r["card"]
        if not (1 <= n <= len(cards)):
            print(f"skip: card {n} out of range (brief has {len(cards)})", file=sys.stderr)
            continue
        card = cards[n - 1]
        rows.append(
            {
                "run_id": run_id,
                "card": n,
                "title": card["title"],
                "url": card["url"],
                "tag": card["tag"],
                "rating": r["rating"],
                "note": r["note"],
                "date": today,
            }
        )
        counts[r["rating"]] += 1

    print(
        f"ingested {len(rows)} ratings for {run_id} "
        f"(++:{counts[2]} +:{counts[1]} =:{counts[0]} -:{counts[-1]} --:{counts[-2]})",
        file=sys.stderr,
    )
    return rows


def rewrite_ledger(ledger: Path, run_ids: set[str], new_rows: list[dict]) -> None:
    """Drop existing rows for the given run_ids, keep the rest, append new."""
    kept: list[str] = []
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)  # preserve anything unparseable
                continue
            if obj.get("run_id") not in run_ids:
                kept.append(line)

    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("w") as fh:
        for line in kept:
            fh.write(line + "\n")
        for row in new_rows:
            fh.write(json.dumps(row) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--all", action="store_true", help="sweep every out/*.feedback.md in one process")
    ap.add_argument("--feedback", type=Path, help="override feedback path (single-run only)")
    ap.add_argument("--brief", type=Path, help="override brief path (single-run only)")
    ap.add_argument("--out-dir", type=Path, default=Path("out"))
    ap.add_argument("--ledger", type=Path, default=Path("memory/feedback.jsonl"))
    args = ap.parse_args()

    if not args.all and not args.run_id:
        ap.error("give a RUN_ID or --all")

    # Resolve the set of runs to ingest, then read the ledger once and
    # rewrite it once -- so --all is a single process, not one per file.
    jobs: list[tuple[str, Path, Path]] = []
    if args.all:
        files = sorted(p for p in args.out_dir.glob("*.feedback.md") if "_backup" not in p.name)
        for fb in files:
            rid = fb.name[: -len(".feedback.md")]
            jobs.append((rid, fb, args.out_dir / f"{rid}.md"))
    else:
        fb = args.feedback or (args.out_dir / f"{args.run_id}.feedback.md")
        brief = args.brief or (args.out_dir / f"{args.run_id}.md")
        jobs.append((args.run_id, fb, brief))

    all_rows: list[dict] = []
    run_ids: set[str] = set()
    for rid, fb, brief in jobs:
        rows = rows_for_run(rid, fb, brief)
        if rows is None:
            continue
        all_rows.extend(rows)
        run_ids.add(rid)

    if not run_ids:
        print("no feedback files ingested.", file=sys.stderr)
        return 1

    rewrite_ledger(args.ledger, run_ids, all_rows)
    print(f"ledger: {len(all_rows)} rows ingested across {len(run_ids)} run(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
