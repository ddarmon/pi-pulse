#!/usr/bin/env python3
"""Interactively apply profile-suggest proposals to memory/interests.md.

Reads `.tmp/profile_updates.md` (proposals emitted by
prompts/suggest_profile.md), snapshots the profile to
memory/interests-history/ (mirroring scripts/interview.sh), then walks
each proposal y/n/s/q. Accepted ADDs append a bullet under the named
section; accepted EDIT/DEMOTE locate the target bullet by
whitespace-normalized match (handling wrapped bullets) and replace or
remove it, reporting any that cannot be matched unambiguously rather
than guessing. Ends with a unified diff vs the snapshot.

The proposal parsing and the three apply operations are pure functions
(line-list in, line-list out) so they can be unit-tested; only the
prompt loop needs a TTY.

Usage:
    apply_updates.py [--proposals PATH] [--profile PATH]
                     [--history-dir DIR] [--accept-all] [--dry-run]
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HEADING = re.compile(r"^#{2,3}\s+\S")
BULLET = re.compile(r"^\s*-\s+\S")


def norm(s: str) -> str:
    return " ".join(s.split())


# --- proposal parsing --------------------------------------------------


def parse_proposals(text: str) -> list[dict]:
    # The model sometimes wraps the whole output in a ``` code fence
    # (it imitates the format example in the prompt). Strip any fence
    # lines so the blocks below start cleanly with `PROPOSAL:`.
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("```"))
    if re.search(r"^NO PROPOSALS", text, re.MULTILINE):
        return []
    blocks = re.split(r"^---\s*$", text, flags=re.MULTILINE)
    proposals: list[dict] = []
    for block in blocks:
        block = block.strip()
        if not block or not block.startswith("PROPOSAL:"):
            continue
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            m = re.match(r"^(PROPOSAL|Section|Target|Text|Rationale|Evidence):\s?(.*)$", line)
            if m:
                key = m.group(1).lower()
                fields[key] = m.group(2).rstrip()
            elif key:  # continuation of a wrapped field
                fields[key] += " " + line.strip()
        if "proposal" in fields:
            fields["type"] = fields.pop("proposal").strip().upper()
            proposals.append(fields)
    return proposals


# --- bullet/section geometry -------------------------------------------


def bullet_blocks(lines: list[str]) -> list[dict]:
    """Return [{start, end, text}] for each logical bullet (incl. wraps)."""
    blocks: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        if BULLET.match(lines[i]):
            start = i
            i += 1
            # continuation: indented, non-blank, not a new bullet/heading
            while i < n and lines[i].strip() and not BULLET.match(lines[i]) and not HEADING.match(lines[i]) and lines[i][:1] in (" ", "\t"):
                i += 1
            blocks.append({"start": start, "end": i, "text": norm("\n".join(lines[start:i]))})
        else:
            i += 1
    return blocks


def section_range(lines: list[str], section: str) -> tuple[int, int] | None:
    """(heading_idx, end_idx) for the section whose heading matches."""
    want = norm(section)
    start = None
    for i, ln in enumerate(lines):
        if HEADING.match(ln) and norm(ln) == want:
            start = i
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        if HEADING.match(lines[j]):
            return start, j
    return start, len(lines)


# --- apply operations (pure) -------------------------------------------


def apply_add(lines: list[str], section: str, text: str) -> tuple[list[str], bool, str]:
    rng = section_range(lines, section)
    if rng is None:
        return lines, False, f"section not found: {section!r}"
    start, end = rng
    # Insert after the last bullet block within the section, else right
    # after the heading (and its blank line).
    blocks = [b for b in bullet_blocks(lines) if start < b["start"] < end]
    insert_at = blocks[-1]["end"] if blocks else min(start + 2, end)
    new = lines[:insert_at] + [text] + lines[insert_at:]
    return new, True, f"added under {section}"


def _find_unique(lines: list[str], target: str) -> tuple[int, int] | str:
    want = norm(target)
    matches = [b for b in bullet_blocks(lines) if b["text"] == want]
    if len(matches) == 1:
        return matches[0]["start"], matches[0]["end"]
    if not matches:
        return "no match for target"
    return f"{len(matches)} matches for target (ambiguous)"


def apply_edit(lines: list[str], target: str, text: str) -> tuple[list[str], bool, str]:
    res = _find_unique(lines, target)
    if isinstance(res, str):
        return lines, False, res
    start, end = res
    return lines[:start] + [text] + lines[end:], True, "replaced bullet"


def apply_demote(lines: list[str], target: str) -> tuple[list[str], bool, str]:
    res = _find_unique(lines, target)
    if isinstance(res, str):
        return lines, False, res
    start, end = res
    return lines[:start] + lines[end:], True, "removed bullet"


def apply_one(lines: list[str], p: dict) -> tuple[list[str], bool, str]:
    t = p.get("type")
    if t == "ADD":
        return apply_add(lines, p.get("section", ""), p.get("text", ""))
    if t == "EDIT":
        return apply_edit(lines, p.get("target", ""), p.get("text", ""))
    if t == "DEMOTE":
        return apply_demote(lines, p.get("target", ""))
    return lines, False, f"unknown proposal type: {t!r}"


# --- interactive driver ------------------------------------------------


def show(p: dict, idx: int, total: int) -> None:
    print("=" * 64)
    print(f"Proposal {idx}/{total}: {p.get('type')}   [{p.get('section', '')}]")
    if p.get("target"):
        print(f"  target: {p['target']}")
    print(f"  text  : {p.get('text', '')}")
    if p.get("rationale"):
        print(f"  why   : {p['rationale']}")
    if p.get("evidence"):
        print(f"  evid  : {p['evidence']}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", type=Path, default=Path(".tmp/profile_updates.md"))
    ap.add_argument("--profile", type=Path, default=Path("memory/interests.md"))
    ap.add_argument("--history-dir", type=Path, default=Path("memory/interests-history"))
    ap.add_argument("--accept-all", action="store_true", help="apply every proposal without prompting")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    args = ap.parse_args()

    if not args.proposals.exists():
        print(f"no proposals file: {args.proposals}", file=sys.stderr)
        return 1
    if not args.profile.exists():
        print(f"no profile: {args.profile}", file=sys.stderr)
        return 1

    proposals = parse_proposals(args.proposals.read_text(errors="replace"))
    if not proposals:
        print("no proposals to apply (profile is current).")
        return 0

    original = args.profile.read_text(errors="replace")
    lines = original.splitlines()

    interactive = not args.accept_all and not args.dry_run
    if interactive and not sys.stdin.isatty():
        print("apply_updates needs a TTY (or use --accept-all / --dry-run).", file=sys.stderr)
        return 1

    applied = 0
    deferred: list[tuple[dict, str]] = []
    for i, p in enumerate(proposals, 1):
        show(p, i, len(proposals))
        if interactive:
            try:
                ans = input("apply? [y]es / [n]o / [q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if ans in ("q", "quit"):
                break
            if ans not in ("y", "yes"):
                continue
        new_lines, ok, msg = apply_one(lines, p)
        if ok:
            lines = new_lines
            applied += 1
            print(f"  -> {msg}")
        else:
            deferred.append((p, msg))
            print(f"  -> SKIPPED: {msg} (apply by hand)")

    new_text = "\n".join(lines).rstrip("\n") + "\n"
    changed = new_text != (original.rstrip("\n") + "\n")

    if args.dry_run:
        print("\n(dry run -- no file written)")
    elif changed:
        args.history_dir.mkdir(parents=True, exist_ok=True)
        snap = args.history_dir / f"{datetime.now():%Y-%m-%d-%H%M}.md"
        snap.write_text(original)
        args.profile.write_text(new_text)
        print(f"\nsnapshot: {snap}")
        print(f"applied {applied} proposal(s) to {args.profile}. Diff:\n")
        sys.stdout.writelines(
            difflib.unified_diff(original.splitlines(True), new_text.splitlines(True), fromfile="before", tofile="after")
        )
    else:
        print("\nno changes applied.")

    if deferred:
        print(f"\n{len(deferred)} proposal(s) could not be applied automatically:")
        for p, msg in deferred:
            print(f"  - {p.get('type')} [{p.get('section', '')}]: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
