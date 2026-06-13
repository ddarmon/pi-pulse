#!/usr/bin/env python3
"""Emit an editable feedback companion file for a delivered brief.

Reads a markdown brief (argv[1]), pulls its level-2 card headings in
order, and writes a numbered template (argv[2]) where the user marks
each card with a rating token. The marks are ingested later by
`ingest_feedback.py` into `memory/feedback.jsonl`.

The card numbering here is the contract the ingest step relies on:
card N is the Nth `## ` heading in the delivered brief, which is exactly
what the reader sees. Dropped slots never reach the brief, so they are
never numbered.

Usage:
    build_feedback_template.py <brief.md> <out.feedback.md>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Level-2 headings are cards. The brief opens with a single `# Pulse ...`
# title and a lede paragraph; every card below is `## `.
CARD_HEADING = re.compile(r"^##\s+(.*\S)\s*$")

HEADER = """\
# Feedback - {run_id}
#
# Edit the marks below, then run:  scripts/ingest-feedback.sh {run_id}
#
# Marks:  [++] excellent   [+] useful   [ ] skip (unrated)
#         [-] not interesting   [--] don't want this topic
#
# Optionally add a note on an indented line beneath any card:
#     note: free text here
#
# Lines starting with # are ignored. Unrated ([ ]) cards are skipped.
"""


def card_titles(brief_md: str) -> list[str]:
    titles: list[str] = []
    for line in brief_md.splitlines():
        m = CARD_HEADING.match(line)
        if m:
            titles.append(m.group(1).strip())
    return titles


def render(run_id: str, titles: list[str]) -> str:
    lines = [HEADER.format(run_id=run_id), ""]
    for i, title in enumerate(titles, start=1):
        lines.append(f"[ ] {i}  {title}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: build_feedback_template.py <brief.md> <out.feedback.md>", file=sys.stderr)
        return 2
    brief_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    text = brief_path.read_text(errors="replace")
    titles = card_titles(text)
    if not titles:
        print(f"no card headings found in {brief_path}", file=sys.stderr)
        return 1

    run_id = brief_path.stem  # YYYY-MM-DD-HHMM
    out_path.write_text(render(run_id, titles))
    print(f"wrote {len(titles)} cards to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
