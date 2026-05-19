#!/usr/bin/env python3
"""Build a compact bundle of recently shipped Pulse cards for the plan stage.

Walks out/YYYY-MM-DD.md for the last --days N days (today excluded),
extracts each card's H2 title and first sentence (with markdown link
syntax stripped to anchor text), and emits one section per date, newest
first. Skips the H1 (`# Pulse ...`) and the `## Dropped from this run`
section. The plan prompt consumes this bundle to drop candidate topics
that semantically overlap with recent briefs; URL-level dedup is
handled separately by memory/seen_urls.jsonl.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

FILENAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
H2_RE = re.compile(r"^## (.+?)\s*$")
TAG_RE = re.compile(r"\s*\((adjacent|bridge|follow-up)\)\s*$")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def parse_cards(text: str) -> list[tuple[str, list[str]]]:
    """Return [(raw_title, body_lines), ...] in document order.

    Anything before the first H2 (the H1 and the "Today's theme:" lede)
    is ignored. The "## Dropped from this run" section and everything
    after it is dropped.
    """
    cards: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_body: list[str] = []

    for line in text.splitlines():
        m = H2_RE.match(line)
        if m:
            if current_title is not None:
                cards.append((current_title, current_body))
            heading = m.group(1).strip()
            if heading == "Dropped from this run":
                current_title = None
                break
            current_title = heading
            current_body = []
        elif current_title is not None:
            current_body.append(line)

    if current_title is not None:
        cards.append((current_title, current_body))

    return cards


def extract_first_sentence(body_lines: list[str], max_chars: int = 200) -> str:
    paragraph: list[str] = []
    for line in body_lines:
        if line.strip() == "":
            if paragraph:
                break
            continue
        paragraph.append(line.strip())
    if not paragraph:
        return ""

    joined = " ".join(paragraph)
    joined = LINK_RE.sub(r"\1", joined)

    m = SENTENCE_END_RE.search(joined)
    sentence = joined[: m.end()].rstrip() if m else joined

    if len(sentence) > max_chars:
        sentence = sentence[:max_chars].rstrip() + "…"
    return sentence


def clean_title(raw: str) -> str:
    return TAG_RE.sub("", raw).strip()


def render_section(d: date, entries: list[tuple[str, str]]) -> str:
    iso = d.isoformat()
    lines = [f"## {iso}", ""]
    for title, sentence in entries:
        if sentence:
            lines.append(f"- [{iso}] {title} -- {sentence}")
        else:
            lines.append(f"- [{iso}] {title}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--days",
        type=int,
        default=7,
        help="Days of history, today excluded (default: 7)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="Directory of YYYY-MM-DD.md briefs (default: out)",
    )
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=5120,
        help="Total bundle byte budget (default: 5120)",
    )
    args = ap.parse_args()
    args.out_dir = args.out_dir.resolve()

    today = date.today()
    earliest = today - timedelta(days=args.days)
    latest = today - timedelta(days=1)

    header = (
        f"# Recently covered ({earliest.isoformat()} through "
        f"{latest.isoformat()}, today {today.isoformat()} excluded)\n"
    )

    if not args.out_dir.is_dir():
        print(f"# WARN: out-dir not a directory: {args.out_dir}", file=sys.stderr)

    files: list[tuple[date, Path]] = []
    if args.out_dir.is_dir():
        for f in args.out_dir.iterdir():
            if not FILENAME_RE.fullmatch(f.name):
                continue
            try:
                d = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if earliest <= d <= latest:
                files.append((d, f))

    files.sort(key=lambda pair: pair[0], reverse=True)

    if not files:
        out = header + "\n(no prior pulses in window)\n"
        sys.stdout.write(out)
        print(
            f"\n# Bundle stats: 0 chars in, {len(out)} chars out",
            file=sys.stderr,
        )
        return 0

    total_in = 0
    sections: list[str] = []
    cumulative = len(header) + 1

    for d, f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError as exc:
            print(f"# WARN: could not read {f}: {exc}", file=sys.stderr)
            continue
        total_in += len(text)

        entries = [
            (clean_title(raw), extract_first_sentence(body))
            for raw, body in parse_cards(text)
        ]
        if not entries:
            continue

        section = render_section(d, entries)
        # +1 for the "\n" separator that "\n".join inserts before this
        # section (no separator before the first). Always include the
        # first section even if it alone busts the budget; otherwise an
        # oversized single brief produces an empty bundle.
        extra = len(section) + (1 if sections else 0)
        if sections and cumulative + extra > args.max_bytes:
            break
        sections.append(section)
        cumulative += extra

    if sections:
        out = header + "\n" + "\n".join(sections)
    else:
        out = header + "\n(no prior pulses in window)\n"
    sys.stdout.write(out)
    print(
        f"\n# Bundle stats: {total_in} chars in, {len(out)} chars out",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
