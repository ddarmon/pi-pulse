#!/usr/bin/env bash
# Ingest an edited feedback companion file into memory/feedback.jsonl,
# then rebuild the recent-feedback digest (.tmp/feedback_recent.md).
#
# Usage:
#   scripts/ingest-feedback.sh [RUN_ID]
#
# With no RUN_ID, the newest out/*.feedback.md is used.
#
# If PI_PULSE_DELIVERY is set and its copy of the feedback file is newer
# than the one in out/ (i.e. you edited the delivered copy), the delivered
# copy is synced back to out/ before ingest.
#
# This makes zero model calls -- it is pure local bookkeeping.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

RUN_ID="${1:-}"
if [[ -z "$RUN_ID" ]]; then
  newest=$(ls -1t out/*.feedback.md 2>/dev/null | head -1 || true)
  if [[ -z "$newest" ]]; then
    echo "ERROR: no out/*.feedback.md found and no RUN_ID given." >&2
    exit 1
  fi
  RUN_ID=$(basename "$newest" .feedback.md)
fi

OUT_FB="out/${RUN_ID}.feedback.md"

# If the delivered copy is newer, pull the user's edits back into out/.
if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  DELIV_FB="${PI_PULSE_DELIVERY}/${RUN_ID}.feedback.md"
  if [[ -f "$DELIV_FB" && ( ! -f "$OUT_FB" || "$DELIV_FB" -nt "$OUT_FB" ) ]]; then
    echo "[ingest] syncing edited feedback from $DELIV_FB"
    cp "$DELIV_FB" "$OUT_FB"
  fi
fi

if [[ ! -f "$OUT_FB" ]]; then
  echo "ERROR: $OUT_FB not found." >&2
  exit 1
fi

echo "[ingest] run_id: $RUN_ID"
uv run sources/ingest_feedback.py "$RUN_ID"
uv run sources/build_feedback_digest.py
echo "[ingest] ledger: memory/feedback.jsonl"
echo "[ingest] digest: .tmp/feedback_recent.md"
