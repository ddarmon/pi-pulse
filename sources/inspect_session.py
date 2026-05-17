#!/usr/bin/env python3
"""Summarize a pi session JSONL file.

Reads a session file produced by `pi --session-dir <dir>` and prints a
human-readable markdown summary: wall time, provider/model, token
totals, all tool calls (name + key args), and the web-search queries
plus the URLs returned. Used by pulse.sh to build logs/YYYY-MM-DD/
summary.md after each stage.

Usage:
    inspect_session.py <session.jsonl> [--label <stage-name>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

URL_PATTERN = re.compile(r"https?://[^\s)\]<>\"']+")


def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path)
    ap.add_argument("--label", default="stage", help="Stage label for the report header")
    args = ap.parse_args()

    if not args.session.is_file():
        print(f"ERROR: not a file: {args.session}", file=sys.stderr)
        return 2

    provider = model = session_id = cwd = None
    first_ts = last_ts = None
    tok_input = tok_output = tok_cache_read = tok_cache_write = 0
    cost_total = 0.0
    tool_calls: list[tuple[str, dict]] = []
    tool_results: list[tuple[str, str, bool]] = []  # (tool_call_id, text, is_error)
    compactions = 0

    with args.session.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = e.get("type")
            ts = parse_ts(e.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts

            if t == "session":
                session_id = e.get("id")
                cwd = e.get("cwd")
            elif t == "model_change":
                provider = e.get("provider")
                model = e.get("modelId")
            elif t == "compaction":
                compactions += 1
            elif t == "message":
                msg = e.get("message", {})
                usage = msg.get("usage") or {}
                tok_input += int(usage.get("input") or 0)
                tok_output += int(usage.get("output") or 0)
                tok_cache_read += int(usage.get("cacheRead") or 0)
                tok_cache_write += int(usage.get("cacheWrite") or 0)
                cost = usage.get("cost") or {}
                cost_total += float(cost.get("total") or 0)

                content = msg.get("content")
                if isinstance(content, list):
                    if msg.get("role") == "toolResult":
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                tool_results.append(
                                    (
                                        c.get("toolCallId") or c.get("tool_call_id") or "?",
                                        c.get("text") or "",
                                        bool(c.get("isError")),
                                    )
                                )
                    else:
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "toolCall":
                                tool_calls.append(
                                    (c.get("name") or "?", c.get("arguments") or {})
                                )

    duration = (last_ts - first_ts).total_seconds() if first_ts and last_ts else 0
    print(f"## {args.label}")
    print()
    print(f"- session: `{session_id}`")
    print(f"- cwd: `{cwd}`")
    print(f"- provider/model: `{provider}` / `{model}`")
    print(f"- wall time: {duration:.1f}s")
    print(
        f"- tokens: input={tok_input:,} | output={tok_output:,} | "
        f"cache_read={tok_cache_read:,} | cache_write={tok_cache_write:,}"
    )
    print(f"- cost (provider-reported): ${cost_total:.4f}")
    if compactions:
        print(f"- compactions: {compactions}")
    print()

    by_tool: dict[str, int] = {}
    for name, _ in tool_calls:
        by_tool[name] = by_tool.get(name, 0) + 1
    if by_tool:
        print(f"**Tool calls** ({len(tool_calls)} total)")
        for name in sorted(by_tool, key=lambda k: -by_tool[k]):
            print(f"- `{name}`: {by_tool[name]}")
        print()

    web_queries = [args for name, args in tool_calls if name == "web_search"]
    if web_queries:
        print(f"**Web search queries** ({len(web_queries)})")
        for i, qa in enumerate(web_queries, 1):
            q = qa.get("query") or qa.get("q") or "?"
            print(f"{i}. `{q}`")
        print()

    if tool_results:
        errs = [r for r in tool_results if r[2]]
        if errs:
            print(f"**Tool errors** ({len(errs)})")
            for _, text, _ in errs[:10]:
                print(f"- {text.strip()[:200]}")
            print()

    url_set: set[str] = set()
    for _, text, is_err in tool_results:
        if is_err:
            continue
        for m in URL_PATTERN.finditer(text):
            url_set.add(m.group(0).rstrip(".,);:!?\"'"))
    if url_set:
        print(f"**URLs returned in tool results** ({len(url_set)} unique)")
        for url in sorted(url_set)[:50]:
            print(f"- {url}")
        if len(url_set) > 50:
            print(f"- … and {len(url_set) - 50} more")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
