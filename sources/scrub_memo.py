#!/usr/bin/env python3
"""Redact private identifiers before a memo enters a web-facing stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from privacy import redact_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    if not args.input.is_file():
        print(f"ERROR: not a file: {args.input}", file=sys.stderr)
        return 2
    scrubbed, counts = redact_text(args.input.read_text(errors="replace"))
    sys.stdout.write(scrubbed)
    if scrubbed and not scrubbed.endswith("\n"):
        sys.stdout.write("\n")
    total = sum(counts.values())
    detail = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
    print(f"# scrub_memo: redacted {total} marker(s) ({detail})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
