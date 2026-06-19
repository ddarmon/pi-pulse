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

# --- TEMPORARY cwd diagnostic (remove once the orphaning is root-caused) ---
# `uv` aborts with "Current directory does not exist" when getcwd() fails,
# i.e. the directory this process is anchored to has been unlinked/replaced
# mid-run. `stat .` still reports the cwd's inode even when getcwd() fails,
# so comparing it to the live inode at the expected path reveals an
# orphaning (MISMATCH) or deletion (getcwd ERR) the instant it happens.
# Logs to $PI_PULSE_PROBE_LOG (set by pulse.sh) or stderr when run by hand.
cwd_probe() {  # $1=label  $2=expected-path (default: $PWD)
  local label="$1" exp="${2:-${PWD:-/}}" getcwd cwd_ino exp_ino match
  getcwd="$(/bin/pwd -P 2>&1)" || getcwd="ERR:${getcwd}"
  cwd_ino="$(/usr/bin/stat -f '%i' . 2>&1)" || cwd_ino="ERR"
  exp_ino="$(/usr/bin/stat -f '%i' "$exp" 2>&1)" || exp_ino="ERR"
  match=$([[ "$cwd_ino" == "$exp_ino" ]] && echo match || echo MISMATCH)
  printf '[probe %-9s] %s pid=%s ppid=%s pwd_env=%s getcwd=%s cwd_ino=%s exp_ino=%s %s\n' \
    "$label" "$(date '+%H:%M:%S')" "$$" "$PPID" "${PWD:-<unset>}" \
    "$getcwd" "$cwd_ino" "$exp_ino" "$match" \
    >>"${PI_PULSE_PROBE_LOG:-/dev/stderr}"
}
# State of the cwd inherited from pulse.sh, before we cd anywhere:
cwd_probe inherited
# --- end TEMPORARY cwd diagnostic ---

# Resolve the repo root to an absolute, canonical path. ROOT is used by the
# cwd_probe comparisons and to normalize the cwd; the earlier
# `uv --directory "$ROOT"` "fix" was REVERTED -- it did not work (the 06-17/
# 06-18 probes proved the cwd is healthy when uv fails), so uv is called
# plainly here, matching pulse.sh's collect calls that DO succeed.
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
cwd_probe after-cd "$ROOT"  # TEMPORARY: state right after we cd to ROOT

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
    cwd_probe pre-uv "$ROOT"  # TEMPORARY
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
  cwd_probe pre-uv "$ROOT"  # TEMPORARY: state right before the failing uv
  # TEMPORARY verbose capture: reproduce the failing uv from THIS child-script
  # context with -v + backtrace and a full env dump, side by side with the
  # pulse-direct probe pulse.sh wrote, to finally see uv's actual error.
  { echo "===== ingest-child $(date '+%H:%M:%S') interp=${BASH:-?} ${BASH_VERSION:-?} pid=$$ ppid=$PPID ====="
    echo "uv: $(command -v uv)"
    RUST_BACKTRACE=full uv -v run python -c 'import os;print("CHILD_CWD",os.getcwd())' 2>&1
    echo "[ingest-child uv exit=$?]"
    echo "----- env -----"; env | sort
  } >>"${PI_PULSE_DIAG_LOG:-/dev/stderr}" 2>&1 || true
  uv run sources/ingest_feedback.py --all
else
  sync_from_delivery "$MODE"
  if [[ ! -f "out/${MODE}.feedback.md" ]]; then
    echo "ERROR: out/${MODE}.feedback.md not found." >&2
    exit 1
  fi
  cwd_probe pre-uv "$ROOT"  # TEMPORARY
  uv run sources/ingest_feedback.py "$MODE"
fi

cwd_probe pre-digest "$ROOT"  # TEMPORARY
uv run sources/build_feedback_digest.py
echo "[ingest] ledger: memory/feedback.jsonl"
echo "[ingest] digest: .tmp/feedback_recent.md"
