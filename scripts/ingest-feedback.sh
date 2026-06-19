#!/usr/bin/env bash
# Ingest edited feedback companion files into memory/feedback.jsonl, then
# rebuild the recent-feedback digest (.tmp/feedback_recent.md).
#
# Usage:
#   scripts/ingest-feedback.sh           # all out/*.feedback.md (default)
#   scripts/ingest-feedback.sh --all     # explicit: all feedback files
#   scripts/ingest-feedback.sh RUN_ID    # just one run
#
# Ingest is idempotent (a run's rows are replaced, not duplicated) and
# unedited files contribute zero rows, so sweeping all files is safe.
#
# NOTE: pulse.sh does NOT call this script -- it ingests inline. Under
# launchd, uv aborts "Current directory does not exist" when invoked from
# this child script (a uv-specific quirk of the grandchild-of-launchd
# process lineage; getcwd works fine for every other tool and the cwd is
# healthy), but works when called directly from pulse.sh. This script
# remains for MANUAL/interactive use, where it works fine, to pick up edits
# immediately instead of waiting for the next run.
#
# If PI_PULSE_DELIVERY is set and its copy of a feedback file is newer than
# the one in out/ (i.e. you edited the delivered copy), the delivered copy
# is synced back to out/ before ingest.
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
  # Bash only does the cheap delivery-sync (no process spawn); the ingest
  # itself runs as a single Python process over all files -- not one
  # `uv run` per file, which used to cost ~0.7s of startup each.
  for f in "${files[@]}"; do
    sync_from_delivery "$(basename "$f" .feedback.md)"
  done
  uv run sources/ingest_feedback.py --all
else
  sync_from_delivery "$MODE"
  if [[ ! -f "out/${MODE}.feedback.md" ]]; then
    echo "ERROR: out/${MODE}.feedback.md not found." >&2
    exit 1
  fi
  uv run sources/ingest_feedback.py "$MODE"
fi

uv run sources/build_feedback_digest.py
echo "[ingest] ledger: memory/feedback.jsonl"
echo "[ingest] digest: .tmp/feedback_recent.md"
