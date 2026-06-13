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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--feedback", type=Path)
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--ledger", type=Path, default=Path("memory/feedback.jsonl"))
    args = ap.parse_args()

    feedback_path = args.feedback or Path(f"out/{args.run_id}.feedback.md")
    brief_path = args.brief or Path(f"out/{args.run_id}.md")

    if not feedback_path.exists():
        print(f"no feedback file: {feedback_path}", file=sys.stderr)
        return 1
    if not brief_path.exists():
        print(f"no brief: {brief_path}", file=sys.stderr)
        return 1

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
                "run_id": args.run_id,
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

    # Idempotent rewrite: drop existing rows for this run_id, keep the rest.
    ledger = args.ledger
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
            if obj.get("run_id") != args.run_id:
                kept.append(line)

    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("w") as fh:
        for line in kept:
            fh.write(line + "\n")
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    print(
        f"ingested {len(rows)} ratings for {args.run_id} "
        f"(++:{counts[2]} +:{counts[1]} =:{counts[0]} -:{counts[-1]} --:{counts[-2]})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
