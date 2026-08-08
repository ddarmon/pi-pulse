#!/usr/bin/env python3
"""Deterministically filter scout's signal sheet against the URL ledgers.

Reads the raw signal sheet (scout's stdout, `.tmp/signals_raw.md`),
drops every signal whose normalized URL is already in
`memory/seen_urls.jsonl` (surfaced in a past brief) or
`memory/unfetchable_urls.jsonl` (committed to a past card but the
expand fetch failed), dedupes repeated URLs within the sheet, and
writes the surviving sheet to stdout in the same shape.

This moves the "not already seen" gate out of the scout prompt and
into code: the scout model still receives the ledger as context so it
doesn't waste query budget on seen URLs, but set membership over
normalized URLs is enforced here, where it is exact. URL
normalization is shared with append_seen.py so both sides of the
ledger agree.

Each dropped signal is reported on stderr as
`FILTERED signal=<id> reason=<reason> url=<url>`. If no signal
survives, stdout is empty -- pulse.sh treats an empty signal sheet as
a fatal stage failure, which is the correct loud outcome.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from append_seen import normalize
from privacy import find_private_markers
from url_policy import UrlPolicyError, validate_public_url

SIGNAL_RE = re.compile(r"^## Signal\s+(\S+)\s*$")
URL_RE = re.compile(r"^\s*-\s*url:\s*(\S+)\s*$")
SHEET_H1_RE = re.compile(r"^# Signals\b")


def load_ledger(path: Path) -> set[str]:
    """Return the set of normalized URLs in a JSONL ledger.

    A missing ledger is an empty set, not an error: the unfetchable
    ledger does not exist until the first recorded drop.
    """
    urls: set[str] = set()
    if not path.is_file():
        return urls
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = entry.get("url")
        if url:
            try:
                validate_public_url(url)
            except UrlPolicyError:
                continue
            urls.add(normalize(url))
    return urls


def split_sheet(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split the sheet into (header_lines, [(signal_id, block_lines), ...])."""
    header: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current_id: str | None = None
    current: list[str] = []

    for line in text.splitlines():
        m = SIGNAL_RE.match(line)
        if m:
            if current_id is not None:
                blocks.append((current_id, current))
            current_id = m.group(1)
            current = [line]
        elif current_id is None:
            header.append(line)
        else:
            current.append(line)

    if current_id is not None:
        blocks.append((current_id, current))

    return header, blocks


def block_url(lines: list[str]) -> str | None:
    for line in lines:
        m = URL_RE.match(line)
        if m:
            return m.group(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("signals", type=Path, help="Raw signal sheet (.tmp/signals_raw.md)")
    ap.add_argument(
        "--seen",
        type=Path,
        default=Path("memory/seen_urls.jsonl"),
        help="Seen-URL ledger (default: memory/seen_urls.jsonl)",
    )
    ap.add_argument(
        "--unfetchable",
        type=Path,
        default=Path("memory/unfetchable_urls.jsonl"),
        help="Unfetchable-URL ledger (default: memory/unfetchable_urls.jsonl)",
    )
    args = ap.parse_args()

    seen = load_ledger(args.seen)
    unfetchable = load_ledger(args.unfetchable)

    text = args.signals.read_text(errors="replace")
    header, blocks = split_sheet(text)

    kept: list[list[str]] = []
    in_sheet: set[str] = set()
    drops = 0
    for signal_id, lines in blocks:
        raw_url = block_url(lines)
        reason = None
        if raw_url is None:
            reason = "missing-url"
            url = ""
        else:
            private_markers = find_private_markers(raw_url)
            if private_markers:
                url = ""
                reason = f"private-url:{','.join(private_markers)}"
            else:
                try:
                    validate_public_url(raw_url)
                except UrlPolicyError as exc:
                    url = ""
                    reason = f"invalid-url:{exc}"
                else:
                    url = normalize(raw_url)
                    if url in seen:
                        reason = "seen-url"
                    elif url in unfetchable:
                        reason = "unfetchable-url"
                    elif url in in_sheet:
                        reason = "duplicate-in-sheet"
        if reason:
            drops += 1
            logged_url = raw_url or "(none)"
            if reason.startswith(("private-url:", "invalid-url:")):
                logged_url = "(redacted-invalid-url)"
            print(
                f"FILTERED signal={signal_id} reason={reason} url={logged_url}",
                file=sys.stderr,
            )
            continue
        in_sheet.add(url)
        kept.append(lines)

    print(
        f"# filter_signals: kept {len(kept)} of {len(blocks)} signals "
        f"(seen ledger: {len(seen)} urls, unfetchable ledger: {len(unfetchable)} urls)",
        file=sys.stderr,
    )

    if not kept:
        # Empty stdout on purpose: pulse.sh's empty-output guard turns
        # this into a loud stage failure rather than a silent empty plan.
        return 0

    # The sheet header is only the canonical `# Signals YYYY-MM-DD` H1.
    # Anything else before the first signal block is leaked model
    # narration (draft entries, deliberation) -- observed in a live
    # run where ~180 lines of deliberation preceded the sheet. Strip
    # it so plan receives only the sheet.
    h1 = next((line for line in header if SHEET_H1_RE.match(line)), None)
    preamble = sum(1 for line in header if line.strip() and line is not h1)
    if preamble:
        print(
            f"# filter_signals: stripped {preamble} non-sheet preamble line(s)",
            file=sys.stderr,
        )

    parts: list[str] = []
    if h1 is not None:
        parts.append(h1.strip())
    for lines in kept:
        parts.append("\n".join(lines).rstrip())
    sys.stdout.write("\n\n".join(parts) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
