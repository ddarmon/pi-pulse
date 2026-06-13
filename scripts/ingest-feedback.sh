#!/usr/bin/env bash
# Ingest edited feedback companion files into memory/feedback.jsonl, then
# rebuild the recent-feedback digest (.tmp/feedback_recent.md).
#
# Usage:
#   scripts/ingest-feedback.sh           # all out/*.feedback.md (default)
#   scripts/ingest-feedback.sh --all     # explicit: all feedback files
#   scripts/ingest-feedback.sh RUN_ID    # just one run
#
# Sweeping all files every run is safe: ingest is idempotent (a run's
# rows are replaced, not duplicated) and unedited files contribute zero
# rows. pulse.sh calls `--all` each run so you never have to run this by
# hand -- edit marks whenever, and the next pulse picks them up.
#
# If PI_PULSE_DELIVERY is set and its copy of a feedback file is newer
# than the one in out/ (i.e. you edited the delivered copy), the
# delivered copy is synced back to out/ before ingest.
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

# Pull the user's edits back from the delivered copy if it is newer.
sync_from_delivery() {
  local run_id="$1"
  local out_fb="out/${run_id}.feedback.md"
  if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
    local deliv_fb="${PI_PULSE_DELIVERY}/${run_id}.feedback.md"
    if [[ -f "$deliv_fb" && ( ! -f "$out_fb" || "$deliv_fb" -nt "$out_fb" ) ]]; then
      echo "[ingest] syncing edited feedback from $deliv_fb"
      cp "$deliv_fb" "$out_fb"
    fi
  fi
}

ingest_one() {
  local run_id="$1"
  sync_from_delivery "$run_id"
  if [[ ! -f "out/${run_id}.feedback.md" ]]; then
    echo "ERROR: out/${run_id}.feedback.md not found." >&2
    return 1
  fi
  uv run sources/ingest_feedback.py "$run_id"
}

MODE="${1:-}"

if [[ -z "$MODE" || "$MODE" == "--all" ]]; then
  shopt -s nullglob
  files=(out/*.feedback.md)
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    # Nothing edited yet -- still refresh the digest so its window stays
    # current as old ratings roll off, then exit cleanly.
    uv run sources/build_feedback_digest.py
    echo "[ingest] no feedback files; digest refreshed."
    exit 0
  fi
  for f in "${files[@]}"; do
    ingest_one "$(basename "$f" .feedback.md)"
  done
else
  ingest_one "$MODE"
fi

uv run sources/build_feedback_digest.py
echo "[ingest] ledger: memory/feedback.jsonl"
echo "[ingest] digest: .tmp/feedback_recent.md"
