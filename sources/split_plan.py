#!/usr/bin/env python3
"""Split .tmp/plan.md into per-slot files for parallel expand.

Writes:
  <out_dir>/theme.md   -- `# Pulse <label>` heading + the plan's
                          `Today's theme:` lede paragraph. The label
                          is derived from the RUN_ID env var: if RUN_ID
                          is `YYYY-MM-DD-HHMM`, the label is
                          `YYYY-MM-DD HH:MM`; otherwise the label is
                          the plan's own date.
  <out_dir>/NN/slot.md -- one directory per planned card, containing
                          that card's full plan block (`## Card N
                          (tag)` heading + dash-prefixed fields).

Emits a newline-separated manifest on stdout:
  NN<TAB>tag

where NN is the zero-padded slot id and tag is the card category
(`tracked`, `adjacent`, `bridge`, or `follow-up of STEM`).
The pipeline iterates this manifest to launch per-slot pi sessions.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

CARD_RE = re.compile(r"^## Card (\d+) \((.+)\)\s*$")
PLAN_DATE_RE = re.compile(r"^# Plan (\d{4}-\d{2}-\d{2})\s*$")
THEME_RE = re.compile(r"^\*\*Today's theme:\*\*\s*(.+)$")
RUN_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2}))?$")


def pulse_label(plan_date: str) -> str:
    """Build the `# Pulse <label>` heading text from RUN_ID, if set.

    Falls back to the plan's own date when RUN_ID is missing or
    doesn't match the expected shape.
    """
    run_id = os.environ.get("RUN_ID", "")
    m = RUN_ID_RE.match(run_id)
    if not m:
        return plan_date
    date_part, hh, mm = m.group(1), m.group(2), m.group(3)
    if hh is None or mm is None:
        return date_part
    return f"{date_part} {hh}:{mm}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan", type=Path, help="Path to .tmp/plan.md")
    ap.add_argument("out_dir", type=Path, help="Output directory (e.g. .tmp/expand)")
    args = ap.parse_args()

    text = args.plan.read_text(errors="replace")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    plan_date = ""
    theme = ""
    for line in text.splitlines():
        m = PLAN_DATE_RE.match(line)
        if m:
            plan_date = m.group(1)
            continue
        m = THEME_RE.match(line)
        if m:
            theme = m.group(1).strip()
            break

    if not plan_date:
        print("ERROR: no `# Plan YYYY-MM-DD` heading in plan", file=sys.stderr)
        return 1
    if not theme:
        print("WARN: no `**Today's theme:**` line in plan", file=sys.stderr)

    theme_lines = [f"# Pulse {pulse_label(plan_date)}", ""]
    if theme:
        theme_lines.append(theme)
        theme_lines.append("")
    (args.out_dir / "theme.md").write_text("\n".join(theme_lines))

    slots: list[tuple[str, str, list[str]]] = []
    current_id: str | None = None
    current_tag: str | None = None
    current_body: list[str] = []

    for line in text.splitlines():
        m = CARD_RE.match(line)
        if m:
            if current_id is not None and current_tag is not None:
                slots.append((current_id, current_tag, current_body))
            current_id = m.group(1).zfill(2)
            current_tag = m.group(2).strip()
            current_body = [line]
        elif current_id is not None:
            current_body.append(line)

    if current_id is not None and current_tag is not None:
        slots.append((current_id, current_tag, current_body))

    if not slots:
        print("ERROR: no `## Card N (tag)` blocks found in plan", file=sys.stderr)
        return 1

    for slot_id, tag, body in slots:
        slot_dir = args.out_dir / slot_id
        slot_dir.mkdir(parents=True, exist_ok=True)
        trimmed = "\n".join(body).rstrip() + "\n"
        (slot_dir / "slot.md").write_text(trimmed)
        sys.stdout.write(f"{slot_id}\t{tag}\n")

    print(f"# split_plan: {len(slots)} slots written to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
