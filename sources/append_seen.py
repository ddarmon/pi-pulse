#!/usr/bin/env python3
"""Extract URLs from a brief and emit JSONL entries for the dedup ledger.

Reads a markdown brief from argv[1], pulls all URLs out (markdown
links + bare http(s) URLs), normalizes them so future runs don't
re-surface the same source under a different query-string, and writes
one `{"url": ..., "date": ...}` line per URL to stdout.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MD_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
BARE_URL = re.compile(r"(?<![\w(])(https?://[^\s)\]>]+)")
ARXIV_VERSION = re.compile(r"(arxiv\.org/abs/\d{4}\.\d{4,5})v\d+", re.IGNORECASE)
ARXIV_PDF = re.compile(r"(arxiv\.org)/pdf/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", re.IGNORECASE)
STRIP_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "ref_src", "ref_url"}


def normalize(url: str) -> str:
    url = url.strip().rstrip(".,;:!?\"'")
    url = ARXIV_VERSION.sub(r"\1", url)
    url = ARXIV_PDF.sub(r"\1/abs/\2", url)
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k.lower() not in STRIP_QUERY_KEYS]
    )
    return urlunparse((scheme, netloc, path, "", query, ""))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: append_seen.py <brief.md>", file=sys.stderr)
        return 2
    text = Path(sys.argv[1]).read_text(errors="replace")

    urls: list[str] = []
    seen_in_run: set[str] = set()
    for match in MD_LINK.finditer(text):
        url = normalize(match.group(1))
        if url not in seen_in_run:
            seen_in_run.add(url)
            urls.append(url)
    for match in BARE_URL.finditer(text):
        url = normalize(match.group(1))
        if url not in seen_in_run:
            seen_in_run.add(url)
            urls.append(url)

    today = date.today().isoformat()
    for url in urls:
        print(json.dumps({"url": url, "date": today}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
