#!/usr/bin/env python3
"""Record Source URLs of fetch-failed expand slots in the unfetchable ledger.

Walks the expand scratch dir (manifest.tsv plus per-slot
slot.md/body.md/err.log) and, for every slot the expand model
explicitly dropped with a `DROPPED slot=NN reason=...` line, emits one
`{"url": ..., "date": ..., "run_id": ..., "slot": ..., "reason": ...}`
JSONL line to stdout. pulse.sh appends this to
memory/unfetchable_urls.jsonl, which sources/filter_signals.py reads
on the next run -- so a URL that 403'd today is not re-scouted and
re-dropped tomorrow.

Only model-emitted drops are recorded: per compose_expand.md the model
emits a DROPPED line exactly when both the content.js fetch and the
fallback search failed, i.e. the *source* is the problem. Script-level
failures are skipped because they say nothing about the URL:
`pi-exit-nonzero` (transient pi crash, injected by expand_slot.sh) and
malformed bodies with no DROPPED line (model misbehavior). URLs
already in the ledger are skipped so retries don't accumulate
duplicates.

URLs are normalized with append_seen.normalize so the ledger agrees
with the seen ledger and the filter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from append_seen import normalize

DROPPED_RE = re.compile(r"DROPPED slot=\S+ reason=(.+)")
SOURCE_URL_RE = re.compile(r"^\s*-\s*\*\*Source URL:\*\*\s*(\S+)\s*$")
RUN_ID_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
SKIP_REASONS = ("pi-exit-nonzero",)


def drop_reason(slot_dir: Path) -> str | None:
    """The model-emitted drop reason for this slot, if any.

    Mirrors pulse.sh's aggregation: the DROPPED line lands in body.md
    (the model's stdout) and sometimes also in err.log.
    """
    for name in ("body.md", "err.log"):
        f = slot_dir / name
        if not f.is_file():
            continue
        m = DROPPED_RE.search(f.read_text(errors="replace"))
        if m:
            return m.group(1).strip()
    return None


def slot_source_url(slot_dir: Path) -> str | None:
    f = slot_dir / "slot.md"
    if not f.is_file():
        return None
    for line in f.read_text(errors="replace").splitlines():
        m = SOURCE_URL_RE.match(line)
        if m:
            return m.group(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("expand_dir", type=Path, help="Expand scratch dir (.tmp/expand)")
    ap.add_argument(
        "--ledger",
        type=Path,
        default=Path("memory/unfetchable_urls.jsonl"),
        help="Existing ledger, used to skip duplicates "
        "(default: memory/unfetchable_urls.jsonl)",
    )
    args = ap.parse_args()

    manifest = args.expand_dir / "manifest.tsv"
    if not manifest.is_file():
        print(f"ERROR: no manifest at {manifest}", file=sys.stderr)
        return 1

    existing: set[str] = set()
    if args.ledger.is_file():
        for line in args.ledger.read_text(errors="replace").splitlines():
            try:
                url = json.loads(line).get("url")
            except json.JSONDecodeError:
                continue
            if url:
                existing.add(normalize(url))

    run_id = os.environ.get("RUN_ID", "")
    m = RUN_ID_DATE_RE.match(run_id)
    entry_date = m.group(1) if m else date.today().isoformat()

    recorded = 0
    for line in manifest.read_text(errors="replace").splitlines():
        slot_id = line.split("\t")[0].strip()
        if not slot_id:
            continue
        slot_dir = args.expand_dir / slot_id
        reason = drop_reason(slot_dir)
        if reason is None or any(reason.startswith(s) for s in SKIP_REASONS):
            continue
        url = slot_source_url(slot_dir)
        if url is None:
            print(
                f"# WARN: slot {slot_id} dropped but has no Source URL in slot.md",
                file=sys.stderr,
            )
            continue
        norm = normalize(url)
        if norm in existing:
            continue
        existing.add(norm)
        recorded += 1
        print(
            json.dumps(
                {
                    "url": norm,
                    "date": entry_date,
                    "run_id": run_id or None,
                    "slot": slot_id,
                    "reason": reason,
                }
            )
        )

    print(f"# append_unfetchable: {recorded} new url(s) recorded", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
