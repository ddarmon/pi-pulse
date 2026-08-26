#!/usr/bin/env python3
"""Verify the rendered Pi model catalog actually supports what pulse.sh asks for.

Pi's `models.json` is a metadata catalog, not an allowlist: an unknown
model id is passed through to the provider endpoint with a warning, so
the run *works* while every piece of per-model metadata is silently
missing. That is not hypothetical -- between 2026-08-15 and 2026-08-26
this pipeline ran twelve days with no catalog entry for
`glm-5.2:cloud`, which meant:

  * `PI_PULSE_SCOUT_THINKING=off` did nothing. Without a
    `thinkingLevelMap`, Pi *omits* the level rather than sending
    `reasoning_effort: none`, so the stage documented as
    "stall-proof by construction" reasoned on every run.
  * Pi assumed its 128k default context window for a model whose real
    window is 1M, while distill routinely sends 150-230k tokens.

Neither failure is visible in the output, so this check runs before the
first Pi call and fails the run loudly instead. Each requirement is a
`--require STAGE PROVIDER MODEL THINKING` quadruple (THINKING may be
empty, meaning the stage passes no --thinking flag).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")
# Pi sends this level verbatim and Ollama answers 400, which empties the
# stage's output and aborts the run. A catalog may only map it to null.
NEVER_REQUEST = "minimal"


def load_catalog(path: Path) -> tuple[dict, list[str]]:
    if not path.is_file():
        return {}, [f"catalog missing: {path}"]
    text = path.read_text(errors="replace")
    stray = PLACEHOLDER_RE.findall(text)
    if stray:
        return {}, [f"catalog has unsubstituted placeholder(s): {', '.join(sorted(set(stray)))}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"catalog is not valid JSON: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return {}, ["catalog has no providers object"]
    return data, []


def find_model(catalog: dict, provider: str, model: str) -> dict | None:
    entry = catalog.get("providers", {}).get(provider)
    if not isinstance(entry, dict):
        return None
    for candidate in entry.get("models", []):
        if isinstance(candidate, dict) and candidate.get("id") == model:
            return candidate
    return None


def check_requirement(catalog: dict, stage: str, provider: str, model: str, thinking: str) -> list[str]:
    problems: list[str] = []
    if provider not in catalog.get("providers", {}):
        return [f"{stage}: provider {provider!r} is not in the catalog"]
    entry = find_model(catalog, provider, model)
    if entry is None:
        return [
            f"{stage}: model {model!r} is not in the {provider!r} catalog "
            f"(Pi would pass it through as a custom id and drop all metadata)"
        ]

    window = entry.get("contextWindow")
    if not isinstance(window, int) or window <= 0:
        problems.append(
            f"{stage}: {model} has no contextWindow, so Pi assumes its 128k default"
        )

    level = thinking.strip()
    if not level:
        return problems

    if level == NEVER_REQUEST:
        problems.append(
            f"{stage}: --thinking {NEVER_REQUEST} is never safe -- Pi sends it "
            "verbatim and the provider answers 400"
        )
        return problems

    level_map = entry.get("thinkingLevelMap")
    if not isinstance(level_map, dict):
        problems.append(
            f"{stage}: {model} has no thinkingLevelMap, so --thinking {level} "
            "is omitted from the request and has no effect"
        )
        return problems
    if level not in level_map:
        problems.append(
            f"{stage}: {model} maps no {level!r} thinking level, so --thinking "
            f"{level} is omitted from the request and has no effect"
        )
    elif level_map[level] is None:
        problems.append(
            f"{stage}: {model} marks thinking level {level!r} unsupported (null), "
            "so Pi clamps it away"
        )
    elif not isinstance(level_map[level], str) or not level_map[level].strip():
        problems.append(f"{stage}: {model} maps thinking level {level!r} to a non-string value")

    if level_map.get(NEVER_REQUEST, None) is not None:
        problems.append(
            f"{stage}: {model} must map {NEVER_REQUEST!r} to null so the level "
            "can never reach the provider"
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog", type=Path, help="Rendered models.json to verify")
    ap.add_argument(
        "--require",
        nargs=4,
        action="append",
        metavar=("STAGE", "PROVIDER", "MODEL", "THINKING"),
        default=[],
        help="A stage's provider/model/thinking level (THINKING may be empty)",
    )
    args = ap.parse_args()

    catalog, problems = load_catalog(args.catalog)
    if not problems:
        for stage, provider, model, thinking in args.require:
            problems.extend(check_requirement(catalog, stage, provider, model, thinking))

    if problems:
        print("ERROR: the Pi model catalog does not support this run:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            f"\nEdit pi-agent/models.json.template (rendered to {args.catalog}).\n"
            "Context windows come from Ollama: "
            "curl -s $OLLAMA/api/show -d '{\"model\":\"<id>\"}'",
            file=sys.stderr,
        )
        return 1

    print(f"# check_models: {len(args.require)} stage requirement(s) satisfied", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
