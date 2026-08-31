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

Recorded drops are the ones that indict the *source*: the model's own
DROPPED line (per compose_expand.md, emitted when the attached page is
empty or substanceless) and expand_slot.sh's `fetch-failed` (both the
guard fetch and the fallback search failed). Drop reasons that say
nothing about the URL are skipped: `pi-exit-nonzero` (transient pi
crash), `no-committed-url` (manifest bookkeeping failure),
`capability-log-failed` (local logging failure), and malformed bodies
with no DROPPED line (model misbehavior). URLs already in the ledger
are skipped so retries don't accumulate duplicates.

A slot whose primary fetch failed but whose fallback search saved the
card is recorded too. It produces no drop -- the card ships,
snippet-grounded -- so before this the failure left no trace any future
run could act on, and the same blocked publisher kept being planned.

Each row also carries the `host` that actually failed, read from the
run's egress log rather than parsed from the committed URL: a committed
doi.org link that redirects into a publisher fails at the publisher, so
the alias is the only thing the URL alone would record.
filter_signals.py uses those hosts to block a source that has failed on
several distinct URLs.

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
from urllib.parse import urlsplit

from append_seen import normalize

DROPPED_RE = re.compile(r"DROPPED slot=\S+ reason=(.+)")
SOURCE_URL_RE = re.compile(r"^\s*-\s*\*\*Source URL:\*\*\s*(\S+)\s*$")
RUN_ID_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
SKIP_REASONS = ("pi-exit-nonzero", "no-committed-url", "capability-log-failed")
FALLBACK_GROUNDING = "search-fallback"


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


def slot_grounding(slot_dir: Path) -> str:
    """How the delivered card was grounded, per expand_slot.sh."""
    f = slot_dir / "grounding"
    if not f.is_file():
        return ""
    return f.read_text(errors="replace").strip()


def fetch_failures(egress_log: Path) -> dict[str, tuple[str, str]]:
    """Map slot -> (failing host, error) for expand fetches that failed.

    Reads the run's append-only egress log, which is the only record of
    where a fetch actually died: the committed URL names the alias that
    was requested, not the host that refused after a redirect.
    """
    failures: dict[str, tuple[str, str]] = {}
    if not egress_log.is_file():
        return failures
    for line in egress_log.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("stage") != "expand" or entry.get("kind") != "fetch":
            continue
        if entry.get("event") == "result" and entry.get("outcome") != "error":
            continue
        if entry.get("event") not in {"result", "rejected"}:
            continue
        slot = str(entry.get("slot") or "").strip()
        if not slot:
            continue
        host = str(entry.get("host") or "").rstrip(".").lower()
        if not host:
            host = (urlsplit(str(entry.get("url") or "")).hostname or "").rstrip(".").lower()
        error = str(entry.get("error") or "").strip()
        failures[slot] = (host, error)
    return failures


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
    ap.add_argument(
        "--egress-log",
        type=Path,
        default=None,
        help="Run egress log naming the host each failed fetch died at "
        "(default: logs/$RUN_ID/egress.log)",
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

    egress_log = args.egress_log
    if egress_log is None and run_id:
        egress_log = Path("logs") / run_id / "egress.log"
    failures = fetch_failures(egress_log) if egress_log is not None else {}

    recorded = 0
    for line in manifest.read_text(errors="replace").splitlines():
        slot_id = line.split("\t")[0].strip()
        if not slot_id:
            continue
        slot_dir = args.expand_dir / slot_id
        reason = drop_reason(slot_dir)
        if reason is None or any(reason.startswith(s) for s in SKIP_REASONS):
            # The card shipped. If it shipped on search snippets, its
            # committed source still refused us and belongs in the ledger:
            # that failure is invisible in the drop record by construction.
            if reason is not None or slot_grounding(slot_dir) != FALLBACK_GROUNDING:
                continue
            error = failures.get(slot_id, ("", ""))[1]
            reason = f"primary-fetch-failed{': ' + error if error else ''}"
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
        host = failures.get(slot_id, ("", ""))[0]
        if not host:
            host = (urlsplit(norm).hostname or "").rstrip(".").lower()
        print(
            json.dumps(
                {
                    "url": norm,
                    "host": host or None,
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
