#!/usr/bin/env bash
# Interactive single-keypress reviewer for card feedback. Walks unrated
# cards across all briefs (or one RUN_ID), shows each card's prose, and
# rates it with one keypress -- writing straight back to the
# out/*.feedback.md files. On a clean exit it folds the ratings into
# memory/feedback.jsonl via ingest-feedback.sh.
#
# Usage:
#   scripts/review-feedback.sh                  # all unrated cards
#   scripts/review-feedback.sh RUN_ID           # one brief
#   scripts/review-feedback.sh --include-rated  # revisit rated cards too
#
# Keys (best to worst): 1=++  2=+  3==neutral  4=-  5=--
#   u=unrated  n=note  >=next  p=prev  q=quit
#
# Makes zero model calls.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if uv run sources/review_feedback.py "$@"; then
  echo
  scripts/ingest-feedback.sh --all
fi
