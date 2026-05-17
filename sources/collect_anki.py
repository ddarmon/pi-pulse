#!/usr/bin/env python3
"""Collect three Anki signals for the distill stage.

- Recently reviewed (rated:7): what David is actively studying.
- Recently added (added:7): topics he just decided to memorize.
- Struggling / leeches (tag:leech): cards to reinforce.

Requires Anki desktop running with AnkiConnect. On connection failure
the script writes a warning to stderr, emits an empty bundle, and
exits 0 so the pipeline degrades gracefully.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENV_VAR = "PI_PULSE_ANKI_SEARCH"
DEFAULT_RELATIVE = "~/.claude/skills/anki-search/anki_search.py"

SIGNALS = [
    ("Recently reviewed", "rated:7", 50),
    ("Recently added", "added:7", 50),
    ("Struggling (leeches)", "tag:leech", 30),
]


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def query(script: Path, q: str, limit: int) -> dict | None:
    try:
        raw = subprocess.check_output(
            [
                "python3",
                str(script),
                "search",
                q,
                "-n",
                str(limit),
                "--json",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"# WARN: anki_search.py failed for '{q}': {exc.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"# WARN: anki_search.py returned non-JSON: {exc}", file=sys.stderr)
        return None


def render_section(title: str, q: str, data: dict | None, max_chars: int) -> str:
    if not data:
        return f"## {title}\n\n(Anki unavailable)\n"
    notes = data.get("notes", []) or []
    total = data.get("total_matches", len(notes))
    lines = [f"## {title}", "", f"_Query: `{q}` -- {total} match(es), showing {len(notes)}_", ""]
    if not notes:
        lines.append("(no cards)")
    for n in notes:
        fields = n.get("fields", {}) or {}
        front = strip_html(fields.get("Front") or fields.get("Text") or "")
        subject = strip_html(fields.get("Subject") or "")
        tags = ",".join(n.get("tags", []) or [])
        bucket = subject or n.get("modelName") or "?"
        front_t = truncate(front, max_chars)
        suffix = f" [tags: {tags}]" if tags else ""
        lines.append(f"- **{bucket}** -- {front_t}{suffix}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--script",
        type=Path,
        default=None,
        help=f"Path to anki_search.py (default: ${ENV_VAR} or {DEFAULT_RELATIVE})",
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=160,
        help="Per-card front truncation (default: 160)",
    )
    args = ap.parse_args()

    script = (
        args.script
        or (Path(os.environ[ENV_VAR]).expanduser() if ENV_VAR in os.environ else None)
        or Path(DEFAULT_RELATIVE).expanduser()
    )
    on_path = shutil.which("anki_search.py")
    if not script.exists() and on_path:
        script = Path(on_path)
    if not script.exists():
        print(f"# WARN: anki_search.py not found at {script}", file=sys.stderr)
        print("# Anki signals (skipped: anki_search.py not installed)\n")
        return 0

    print("# Anki signals\n")

    any_data = False
    for title, q, limit in SIGNALS:
        data = query(script, q, limit)
        if data is not None:
            any_data = True
        print(render_section(title, q, data, args.max_chars))

    if not any_data:
        print(
            "# WARN: no Anki signal succeeded. Ensure Anki desktop is running "
            "with AnkiConnect.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
