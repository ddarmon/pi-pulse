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
  NN<TAB>tag<TAB>source-url

where NN is the zero-padded slot id, tag is the card category
(`tracked`, `adjacent`, `bridge`, or `follow-up of STEM`), and source-url
is the URL parsed from the already-verified plan slot. Carrying the URL in
the manifest keeps the expand shell from re-parsing model-authored markdown.
The pipeline iterates this manifest to launch per-slot pi sessions.

When --signals is given, each slot's `Source URL:` is verified against
the signal sheet (normalized comparison via append_seen.normalize).
The plan prompt requires URLs to be copied verbatim from signals.md;
this turns that requirement into a structural invariant. A slot whose
URL is missing or not in the sheet is excluded from the manifest and
reported on stderr as `DROPPED slot=NN tag=<tag> reason=...` so
pulse.sh can aggregate it into dropped.md.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from append_seen import normalize
from privacy import find_private_markers, redact_text
from url_policy import UrlPolicyError, validate_public_url

CARD_RE = re.compile(r"^## Card (\d+) \((.+)\)\s*$")
PLAN_DATE_RE = re.compile(r"^# Plan (\d{4}-\d{2}-\d{2})\s*$")
THEME_RE = re.compile(r"^\*\*Today's theme:\*\*\s*(.+)$")
RUN_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2}))?$")
SOURCE_URL_RE = re.compile(r"^\s*-\s*\*\*Source URL:\*\*\s*(\S+)\s*$")
SIGNAL_URL_RE = re.compile(r"^\s*-\s*url:\s*(\S+)\s*$")


def signal_urls(signals_path: Path) -> set[str]:
    """Normalized URLs present in the scout signal sheet."""
    urls: set[str] = set()
    for line in signals_path.read_text(errors="replace").splitlines():
        m = SIGNAL_URL_RE.match(line)
        if m:
            urls.add(normalize(m.group(1)))
    return urls


def slot_source_url(body: list[str]) -> str | None:
    for line in body:
        m = SOURCE_URL_RE.match(line)
        if m:
            return m.group(1)
    return None


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
    ap.add_argument(
        "--signals",
        type=Path,
        default=None,
        help="Signal sheet to verify each slot's Source URL against "
        "(.tmp/signals.md). Slots whose URL is not in the sheet are "
        "dropped from the manifest.",
    )
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

    allowed: set[str] | None = None
    if args.signals is not None:
        allowed = signal_urls(args.signals)

    written = 0
    for slot_id, tag, body in slots:
        # Bind the URL before the optional verification branch so the
        # manifest shape is stable even when split_plan is used manually
        # without --signals.
        url = slot_source_url(body) or ""
        if not url:
            print(
                f"DROPPED slot={slot_id} tag={tag} "
                "reason=no Source URL line in plan slot",
                file=sys.stderr,
            )
            continue
        try:
            validate_public_url(url)
        except UrlPolicyError as exc:
            print(
                f"DROPPED slot={slot_id} tag={tag} "
                f"reason=invalid Source URL ({exc})",
                file=sys.stderr,
            )
            continue
        private_markers = find_private_markers(url)
        if private_markers:
            print(
                f"DROPPED slot={slot_id} tag={tag} "
                f"reason=private marker in Source URL ({','.join(private_markers)})",
                file=sys.stderr,
            )
            continue
        if allowed is not None:
            if normalize(url) not in allowed:
                print(
                    f"DROPPED slot={slot_id} tag={tag} "
                    f"reason=plan Source URL not in signal sheet ({url})",
                    file=sys.stderr,
                )
                continue
        slot_dir = args.out_dir / slot_id
        slot_dir.mkdir(parents=True, exist_ok=True)
        trimmed = "\n".join(body).rstrip() + "\n"
        # The plan is sealed, but its rationale can echo private strings from
        # the raw memo. Scrub the slot itself before it enters web-derived
        # expand context; the verified URL travels separately in the manifest.
        scrubbed, redactions = redact_text(trimmed)
        if redactions:
            detail = ",".join(f"{key}={value}" for key, value in sorted(redactions.items()))
            print(f"# split_plan: slot={slot_id} redacted {detail}", file=sys.stderr)
        (slot_dir / "slot.md").write_text(scrubbed)
        sys.stdout.write(f"{slot_id}\t{tag}\t{url}\n")
        written += 1

    print(
        f"# split_plan: {written} of {len(slots)} slots written to {args.out_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
