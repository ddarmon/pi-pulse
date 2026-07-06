#!/usr/bin/env bash
# pi-pulse entrypoint. Runs the source-first pipeline:
#   1. Collect inputs (notes / sesh / Anki) into .tmp/
#   2. Distill via Pi headless (no tools): five-section memo
#   3. Scout via Pi headless (web search/fetch enabled): probe brave-
#      search per interest cluster, emit a structured signal sheet so
#      the plan stage picks from real evidence, not imagined sources.
#      sources/filter_signals.py then drops signals whose normalized
#      URL is in memory/seen_urls.jsonl or memory/unfetchable_urls.jsonl
#      (deterministic; the prompt-level ledger check is advisory only)
#   4. Plan via Pi headless (no tools): rank scout signals into
#      TRACKED / ADJACENT / BRIDGE / FOLLOWUP card slots, each with a
#      committed Source URL drawn verbatim from signals.md. The reader-
#      feedback digest (.tmp/feedback_recent.md) is attached as a
#      ranking prior; a per-thread diversity cap in the prompt keeps
#      it from concentrating the brief onto one thread.
#   5. Expand via Pi headless (web search/fetch enabled), one pi call
#      PER CARD in parallel (capped by PI_PULSE_EXPAND_PARALLEL): fetch
#      the committed Source URL, write 250-400 words of prose
#   6. Stitch theme + per-slot bodies into out/YYYY-MM-DD.md, aggregate
#      drops to logs/YYYY-MM-DD/dropped.md, append URLs to seen ledger,
#      render HTML, copy to delivery dir.
#
# Card quotas are CAPS, not targets: on slow signal days the brief
# shrinks rather than padding. ChatGPT Pulse parity.
#
# Each invocation owns a RUN_ID of the form YYYY-MM-DD-HHMM. Output,
# logs, and session archives are keyed on RUN_ID so multiple pulses can
# run on the same day without clobbering each other. The shared scratch
# dir .tmp/ is guarded by a lockfile (.tmp/.pulse.lock) so concurrent
# runs fail fast.
#
# Sessions for every pi call are routed to .pulse-sessions/${RUN_ID}/
# (gitignored) so they do NOT pollute ~/.pi/agent/sessions/ and never
# feed tomorrow's distill via sesh discovery.
#
# Per-run logs land in logs/${RUN_ID}/:
#   distill.log.md  scout.log.md  plan.log.md  expand.log.md
#   dropped.md  summary.md  *.err
#
# Configuration (env vars; see .env.example):
#   PI_PULSE_NOTES_DIR        Directory tree of YYYY/MM/DD/*.md notes
#   PI_PULSE_DELIVERY         Directory to copy the daily brief into
#   PI_PULSE_ANKI_SEARCH      Path to anki_search.py (optional)
#   PI_PULSE_BRAVE_DIR        brave-search skill dir, substituted into
#                             {baseDir} in scout/expand prompts (default:
#                             $HOME/.pi/agent/skills/brave-search)
#   PI_PULSE_RUN_ID           Override the run identifier (default:
#                             current date-time as YYYY-MM-DD-HHMM)
#   PI_PROVIDER               Pi provider (default: ollama)
#   PI_MODEL                  Pi model    (default: glm-5.2:cloud)
#   PI_PULSE_<STAGE>_MODEL    Per-stage model override (fallback: PI_MODEL)
#   PI_PULSE_<STAGE>_PROVIDER Per-stage provider override (fallback: PI_PROVIDER)
#   PI_PULSE_<STAGE>_THINKING Per-stage --thinking level (default: unset)
#                             <STAGE> in DISTILL, SCOUT, PLAN, EXPAND
#   PI_PULSE_NOTES_SINCE      Days of notes history (default: 30)
#   PI_PULSE_SESH_SINCE       Days of sesh history  (default: 7)
#   PI_PULSE_HISTORY_DAYS     Days of prior briefs for dedup (default: 7)
#   PI_PULSE_CARDS_TRACKED    Tracked card cap     (default: 5)
#   PI_PULSE_CARDS_ADJACENT   Adjacent card cap    (default: 2)
#   PI_PULSE_CARDS_BRIDGE     Bridge card cap      (default: 1)
#   PI_PULSE_CARDS_FOLLOWUP   Follow-up card cap   (default: 1; 0 disables;
#                             consumes one tracked slot)
#   PI_PULSE_SCOUT_MAX_INTERESTS        Scout breadth cap         (default: 12)
#   PI_PULSE_SCOUT_QUERIES_PER_INTEREST Scout queries per interest (default: 2)
#   PI_PULSE_EXPAND_PARALLEL  Per-slot expand concurrency (default: 4)

set -euo pipefail
cd "$(dirname "$0")"

# Auto-load .env if present (local-only config).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

DATE=$(date +%F)
TIME=$(date +%H%M)
RUN_ID="${PI_PULSE_RUN_ID:-${DATE}-${TIME}}"
OUT="out/${RUN_ID}.md"
SESSION_DIR=".pulse-sessions/${RUN_ID}"
LOG_DIR="logs/${RUN_ID}"
export RUN_ID
PI_PROVIDER="${PI_PROVIDER:-ollama}"
PI_MODEL="${PI_MODEL:-glm-5.2:cloud}"

# Per-stage model/provider/thinking overrides. Each falls back to the
# global PI_PROVIDER/PI_MODEL, so with nothing set the four stages all run
# on the global default (today's behavior). The recommended setup is a
# single model across all stages (e.g. glm-5.2:cloud); the override knobs
# exist so any stage can be pointed at a different model without touching
# the others. *_THINKING is empty by default (no --thinking flag passed);
# it is effectively inert for reasoning models served over Ollama's
# OpenAI-compat endpoint (pi cannot send a working "off" through it).
DISTILL_PROVIDER="${PI_PULSE_DISTILL_PROVIDER:-$PI_PROVIDER}"
DISTILL_MODEL="${PI_PULSE_DISTILL_MODEL:-$PI_MODEL}"
DISTILL_THINKING="${PI_PULSE_DISTILL_THINKING:-}"
SCOUT_PROVIDER="${PI_PULSE_SCOUT_PROVIDER:-$PI_PROVIDER}"
SCOUT_MODEL="${PI_PULSE_SCOUT_MODEL:-$PI_MODEL}"
SCOUT_THINKING="${PI_PULSE_SCOUT_THINKING:-}"
PLAN_PROVIDER="${PI_PULSE_PLAN_PROVIDER:-$PI_PROVIDER}"
PLAN_MODEL="${PI_PULSE_PLAN_MODEL:-$PI_MODEL}"
PLAN_THINKING="${PI_PULSE_PLAN_THINKING:-}"
EXPAND_PROVIDER="${PI_PULSE_EXPAND_PROVIDER:-$PI_PROVIDER}"
EXPAND_MODEL="${PI_PULSE_EXPAND_MODEL:-$PI_MODEL}"
EXPAND_THINKING="${PI_PULSE_EXPAND_THINKING:-}"

# Optional --thinking flags, present only when the stage's level is set.
distill_think=(); [[ -n "$DISTILL_THINKING" ]] && distill_think=(--thinking "$DISTILL_THINKING")
scout_think=();   [[ -n "$SCOUT_THINKING"   ]] && scout_think=(--thinking "$SCOUT_THINKING")
plan_think=();    [[ -n "$PLAN_THINKING"    ]] && plan_think=(--thinking "$PLAN_THINKING")

# Total attempts for each single-shot synthesis stage (distill/scout/plan).
# glm-5.2 occasionally ends a synthesis turn inside its reasoning channel and
# emits no answer text, leaving a 0-byte file; a fresh sample almost always
# succeeds. See run_pi_retry below.
SYNTH_RETRIES="${PI_PULSE_SYNTH_RETRIES:-3}"

NOTES_SINCE="${PI_PULSE_NOTES_SINCE:-30}"
SESH_SINCE="${PI_PULSE_SESH_SINCE:-7}"
HISTORY_DAYS="${PI_PULSE_HISTORY_DAYS:-7}"
TRACKED="${PI_PULSE_CARDS_TRACKED:-5}"
ADJACENT="${PI_PULSE_CARDS_ADJACENT:-2}"
BRIDGE="${PI_PULSE_CARDS_BRIDGE:-1}"
FOLLOWUP="${PI_PULSE_CARDS_FOLLOWUP:-1}"
SCOUT_MAX_INTERESTS="${PI_PULSE_SCOUT_MAX_INTERESTS:-12}"
SCOUT_QUERIES_PER_INTEREST="${PI_PULSE_SCOUT_QUERIES_PER_INTEREST:-2}"
EXPAND_PARALLEL="${PI_PULSE_EXPAND_PARALLEL:-4}"
# Directory of the brave-search skill. The scout and expand prompts call
# `{baseDir}/search.js` and `{baseDir}/content.js`; we substitute {baseDir}
# with this path so the model never has to discover it -- left unsubstituted,
# the model resolves it ad hoc and has been observed running `find /` across
# the whole filesystem (hours-long hang + macOS permission prompts).
BRAVE_DIR="${PI_PULSE_BRAVE_DIR:-$HOME/.pi/agent/skills/brave-search}"
export TRACKED ADJACENT BRIDGE FOLLOWUP
export SCOUT_MAX_INTERESTS SCOUT_QUERIES_PER_INTEREST EXPAND_PARALLEL

mkdir -p .tmp .tmp/expand out "$LOG_DIR" \
  "$SESSION_DIR/distill" "$SESSION_DIR/scout" \
  "$SESSION_DIR/plan" "$SESSION_DIR/expand"
if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  mkdir -p "$PI_PULSE_DELIVERY"
fi

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Run a single-shot pi synthesis stage, retrying if it yields EMPTY output.
# Guards the glm-5.2 "thinking runaway": the model can end its turn inside the
# reasoning channel and emit no answer text, leaving a 0-byte file. A fresh
# sample almost always succeeds; cost is one extra pi call (fine under the
# unlimited Ollama subscription). Errexit is suspended for the body because
# callers invoke this as `if ! run_pi_retry ...`, so a nonzero pi exit just
# triggers another attempt. Output is judged by file size, not pi's exit code.
#   run_pi_retry <outfile> <errfile> <label> -- pi <args...>
run_pi_retry() {
  local out=$1 err=$2 label=$3; shift 3
  [[ "$1" == "--" ]] && shift
  local n=1
  while (( n <= SYNTH_RETRIES )); do
    "$@" > "$out" 2>"$err"
    if [[ -s "$out" ]]; then
      (( n > 1 )) && log "  ${label}: recovered on attempt ${n}/${SYNTH_RETRIES}"
      return 0
    fi
    log "  ${label}: EMPTY output on attempt ${n}/${SYNTH_RETRIES} (glm-5.2 thinking runaway); resampling"
    (( n++ ))
  done
  return 1
}

# Lockfile: .tmp/ is shared scratch and would corrupt under concurrent
# runs. mkdir is atomic; if it fails, surface the holder's PID/RUN_ID
# and bail. The trap clears the lock on any exit (success, error, signal).
LOCK_DIR=".tmp/.pulse.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  held=""
  if [[ -f "$LOCK_DIR/info" ]]; then
    held=" (held by $(cat "$LOCK_DIR/info"))"
  fi
  echo "ERROR: another pulse.sh run is in progress${held}." >&2
  echo "       Remove $LOCK_DIR if you are sure no run is active." >&2
  exit 1
fi
trap 'rm -rf "$LOCK_DIR"' EXIT
echo "pid=$$ run_id=${RUN_ID} started=$(date -Iseconds)" > "$LOCK_DIR/info"

# Find the newest .jsonl under a directory (recursive).
newest_session() {
  find "$1" -type f -name '*.jsonl' -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null \
    | head -n 1
}

# 0. Sanity: make sure ollama is up. Cloud-routed models still need the
# local daemon to proxy. Start it if any stage's resolved provider is ollama.
needs_ollama=0
for _prov in "$DISTILL_PROVIDER" "$SCOUT_PROVIDER" "$PLAN_PROVIDER" "$EXPAND_PROVIDER"; do
  [[ "$_prov" == "ollama" ]] && needs_ollama=1
done
if [[ "$needs_ollama" == 1 ]]; then
  if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
    log "ollama not reachable on 11434; starting in background"
    nohup ollama serve >/dev/null 2>&1 &
    sleep 5
  fi
fi

# 0b. Ensure Anki is running so AnkiConnect can serve collect_anki.py.
if ! curl -sf --max-time 2 http://127.0.0.1:8765 -d '{"action":"version","version":6}' >/dev/null 2>&1; then
  log "AnkiConnect not reachable; launching Anki"
  open -g -a Anki
  for _i in $(seq 1 15); do
    sleep 2
    curl -sf --max-time 2 http://127.0.0.1:8765 -d '{"action":"version","version":6}' >/dev/null 2>&1 && break
  done
  if ! curl -sf --max-time 2 http://127.0.0.1:8765 -d '{"action":"version","version":6}' >/dev/null 2>&1; then
    log "WARN: Anki did not respond after 30s; Anki signals will be empty"
  fi
fi

# 0c. Sanity: the brave-search skill must exist where {baseDir} points, or
# scout/expand silently return no signals. Non-fatal -- just surface it.
if [[ ! -x "$BRAVE_DIR/search.js" ]]; then
  log "WARN: brave-search skill not found at $BRAVE_DIR (set PI_PULSE_BRAVE_DIR); scout/expand may return nothing"
fi

# 1. Collect
log "collecting notes (${NOTES_SINCE}d)"
uv run sources/collect_obsidian.py --since "$NOTES_SINCE" > .tmp/chats_recent.md 2>"$LOG_DIR/collect-obsidian.err"
log "collecting sesh sessions (${SESH_SINCE}d)"
uv run sources/collect_sesh.py     --since "$SESH_SINCE" --exclude-cwd "$PWD" > .tmp/sesh_recent.md 2>"$LOG_DIR/collect-sesh.err"
log "collecting anki signals"
uv run sources/collect_anki.py                            > .tmp/anki_signals.md 2>"$LOG_DIR/collect-anki.err" || true

# 1b. Build recent-pulses bundle (scout and plan both consume this).
# --exclude-stem skips this run's own brief if one already exists from a
# retry with the same PI_PULSE_RUN_ID, so we never self-cite.
log "building recent-pulses bundle (${HISTORY_DAYS}d, today included)"
uv run sources/build_recent_pulses.py --days "$HISTORY_DAYS" \
   --exclude-stem "$RUN_ID" \
   > .tmp/recent_pulses.md \
   2>"$LOG_DIR/build-recent.err"

# 1c. Sweep any feedback the reader has edited since prior runs into
# memory/feedback.jsonl and refresh .tmp/feedback_recent.md. Idempotent
# and zero model calls, so it is safe to run every pulse; unedited files
# contribute nothing. This run's own feedback file does not exist yet
# (it is written at deliver), so today's edits are picked up tomorrow.
# Non-fatal: feedback bookkeeping must never sink a brief.
#
# Run INLINE here rather than via scripts/ingest-feedback.sh. Under launchd,
# uv aborts "Current directory does not exist" only when invoked from that
# child script -- a uv-specific quirk of the grandchild-of-launchd process
# lineage (getcwd works fine for /bin/pwd, python3 and perl in the same
# process; the cwd is healthy). The identical uv calls work when run
# directly from pulse.sh, as the collect calls above already prove. The
# child script stays for manual/interactive use, where it works.
log "sweeping card feedback"
{
  # Pull back any edits made to the delivered copy when it is newer.
  shopt -s nullglob; fbfiles=(out/*.feedback.md); shopt -u nullglob
  for f in "${fbfiles[@]}"; do
    rid="$(basename "$f" .feedback.md)"
    if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
      dfb="$PI_PULSE_DELIVERY/${rid}.feedback.md"
      [[ -f "$dfb" && ( ! -f "$f" || "$dfb" -nt "$f" ) ]] && cp "$dfb" "$f"
    fi
  done
  # Cap each digest section so the plan-stage prior stays small
  # (an uncapped digest ballooned the plan pi call; see CLAUDE.md).
  if (( ${#fbfiles[@]} )); then
    uv run sources/ingest_feedback.py --all \
      && uv run sources/build_feedback_digest.py \
           --max-per-section "${PI_PULSE_FEEDBACK_DIGEST_MAX:-20}"
  else
    uv run sources/build_feedback_digest.py \
      --max-per-section "${PI_PULSE_FEEDBACK_DIGEST_MAX:-20}"
  fi
} >"$LOG_DIR/ingest-feedback.log" 2>&1 \
  || log "WARN: feedback ingest failed; see $LOG_DIR/ingest-feedback.log"

# The plan stage attaches .tmp/feedback_recent.md unconditionally; a
# missing file would sink the pi call as a bad @-attachment. The ingest
# block above is non-fatal (and on a fresh clone has nothing to sweep),
# so guarantee the file exists with the same stub the digest builder
# writes for an empty window.
if [[ ! -f .tmp/feedback_recent.md ]]; then
  printf '# Recent feedback (last 14 days)\n\n(no feedback in window)\n' \
    > .tmp/feedback_recent.md
fi

# 2. Distill (no tools)
log "distill stage: ${DISTILL_PROVIDER}/${DISTILL_MODEL}${DISTILL_THINKING:+ thinking=$DISTILL_THINKING}"
distill_start=$SECONDS
if ! run_pi_retry .tmp/interests_today.md "$LOG_DIR/distill.err" distill -- \
   pi -p "$(cat prompts/distill_context.md)" \
      --provider "$DISTILL_PROVIDER" --model "$DISTILL_MODEL" \
      ${distill_think[@]+"${distill_think[@]}"} \
      --no-skills \
      --session-dir "$SESSION_DIR/distill" \
      @.tmp/chats_recent.md @.tmp/sesh_recent.md \
      @.tmp/anki_signals.md @memory/interests.md ; then
  log "ERROR: distill stage produced empty output after $SYNTH_RETRIES attempts. See $LOG_DIR/distill.err"
  exit 1
fi
log "distill finished in $((SECONDS - distill_start))s"
distill_session=$(newest_session "$SESSION_DIR/distill")
if [[ -n "$distill_session" ]]; then
  uv run sources/inspect_session.py "$distill_session" --label "distill" \
    > "$LOG_DIR/distill.log.md"
fi

# 2b. Archive this run's memo so the weekly profile-suggest stage has a
# per-run history to read (logs/ is gitignored and keyed on RUN_ID).
cp .tmp/interests_today.md "$LOG_DIR/memo.md"

# 3. Scout (web search/fetch enabled): discover fresh primary sources
# per interest cluster, emit structured signals.md.
log "scout stage: ${SCOUT_PROVIDER}/${SCOUT_MODEL}${SCOUT_THINKING:+ thinking=$SCOUT_THINKING} (interests<=${SCOUT_MAX_INTERESTS} queries<=${SCOUT_QUERIES_PER_INTEREST})"
scout_start=$SECONDS
SCOUT_PROMPT=$(sed -e "s|{{SCOUT_MAX_INTERESTS}}|${SCOUT_MAX_INTERESTS}|g" \
                   -e "s|{{SCOUT_QUERIES_PER_INTEREST}}|${SCOUT_QUERIES_PER_INTEREST}|g" \
                   -e "s|{baseDir}|${BRAVE_DIR}|g" \
                   prompts/scout_signals.md)
if ! run_pi_retry .tmp/signals_raw.md "$LOG_DIR/scout.err" scout -- \
   pi -p "$SCOUT_PROMPT" \
      --provider "$SCOUT_PROVIDER" --model "$SCOUT_MODEL" \
      ${scout_think[@]+"${scout_think[@]}"} \
      --session-dir "$SESSION_DIR/scout" \
      @.tmp/interests_today.md @memory/interests.md \
      @memory/seen_urls.jsonl @.tmp/recent_pulses.md ; then
  log "ERROR: scout stage produced empty signals after $SYNTH_RETRIES attempts. See $LOG_DIR/scout.err"
  exit 1
fi
log "scout finished in $((SECONDS - scout_start))s"
scout_session=$(newest_session "$SESSION_DIR/scout")
if [[ -n "$scout_session" ]]; then
  uv run sources/inspect_session.py "$scout_session" --label "scout" \
    > "$LOG_DIR/scout.log.md"
fi

# 3b. Deterministic ledger filter: drop signals whose normalized URL is
# already in seen_urls.jsonl (surfaced before) or unfetchable_urls.jsonl
# (committed before but the expand fetch failed). The scout prompt asks
# the model to respect the seen ledger, but set membership over
# normalized URLs belongs in code, not in the model's head.
log "filtering signals against URL ledgers"
if ! uv run sources/filter_signals.py .tmp/signals_raw.md \
       > .tmp/signals.md 2>"$LOG_DIR/filter-signals.err"; then
  log "ERROR: filter_signals failed. See $LOG_DIR/filter-signals.err"
  exit 1
fi
if [[ ! -s .tmp/signals.md ]]; then
  log "ERROR: every scout signal was filtered out (seen or unfetchable)."
  log "       See $LOG_DIR/filter-signals.err"
  exit 1
fi
signals_raw=$(grep -c '^## Signal ' .tmp/signals_raw.md || true)
signals_kept=$(grep -c '^## Signal ' .tmp/signals.md || true)
log "signals: ${signals_kept}/${signals_raw} kept after ledger filter"

# 4. Plan (no tools): rank scout signals into card slots with committed URLs.
# Plan also receives the reader-feedback digest (.tmp/feedback_recent.md,
# refreshed in step 1c) as a ranking prior. Log a one-line census of it so
# a run's steering input is visible in the run log and summary.
if grep -q '^(no feedback in window)$' .tmp/feedback_recent.md; then
  fb_census="empty"
else
  fb_census=$(awk '
    /^## Valued /           {sec="v"; next}
    /^## Neutral /          {sec="n"; next}
    /^## Not valued /       {sec="x"; next}
    /^## Avoid candidates / {sec="a"; next}
    /^## /                  {sec="";  next}
    /^- / { if (sec=="v") v++; else if (sec=="n") n++;
            else if (sec=="x") x++; else if (sec=="a") a++ }
    END { printf "%d valued / %d neutral / %d not-valued / %d avoid", v, n, x, a }
  ' .tmp/feedback_recent.md)
fi
log "feedback digest: ${fb_census}"
log "plan stage: ${PLAN_PROVIDER}/${PLAN_MODEL}${PLAN_THINKING:+ thinking=$PLAN_THINKING} (caps T=${TRACKED} A=${ADJACENT} B=${BRIDGE} F=${FOLLOWUP})"
plan_start=$SECONDS
PLAN_PROMPT=$(sed -e "s|{{TRACKED}}|${TRACKED}|g" \
                  -e "s|{{ADJACENT}}|${ADJACENT}|g" \
                  -e "s|{{BRIDGE}}|${BRIDGE}|g" \
                  -e "s|{{FOLLOWUP}}|${FOLLOWUP}|g" \
                  prompts/compose_plan.md)
if ! run_pi_retry .tmp/plan.md "$LOG_DIR/plan.err" plan -- \
   pi -p "$PLAN_PROMPT" \
      --provider "$PLAN_PROVIDER" --model "$PLAN_MODEL" \
      ${plan_think[@]+"${plan_think[@]}"} \
      --no-skills \
      --session-dir "$SESSION_DIR/plan" \
      @.tmp/signals.md @.tmp/interests_today.md \
      @.tmp/recent_pulses.md @.tmp/feedback_recent.md \
      @memory/seen_urls.jsonl ; then
  log "ERROR: plan stage produced empty output after $SYNTH_RETRIES attempts. See $LOG_DIR/plan.err"
  exit 1
fi
log "plan finished in $((SECONDS - plan_start))s"
plan_session=$(newest_session "$SESSION_DIR/plan")
if [[ -n "$plan_session" ]]; then
  uv run sources/inspect_session.py "$plan_session" --label "plan" \
    > "$LOG_DIR/plan.log.md"
fi

# 5. Expand (per-card parallel; web search/fetch enabled).
log "splitting plan into per-slot files"
rm -rf .tmp/expand
mkdir -p .tmp/expand
MANIFEST_FILE=".tmp/expand/manifest.tsv"
if ! uv run sources/split_plan.py .tmp/plan.md .tmp/expand \
       --signals .tmp/signals.md \
       > "$MANIFEST_FILE" 2>"$LOG_DIR/split-plan.err"; then
  log "ERROR: split_plan failed. See $LOG_DIR/split-plan.err"
  exit 1
fi
SLOT_COUNT=$(wc -l < "$MANIFEST_FILE" | tr -d ' ')
if [[ "$SLOT_COUNT" -eq 0 ]]; then
  log "ERROR: split_plan produced no slots. See $LOG_DIR/split-plan.err"
  exit 1
fi

log "expand stage: ${EXPAND_PROVIDER}/${EXPAND_MODEL}${EXPAND_THINKING:+ thinking=$EXPAND_THINKING} (slots=${SLOT_COUNT} parallel=${EXPAND_PARALLEL})"
expand_start=$SECONDS
export REPO_ROOT="$PWD"
export EXPAND_DIR="$PWD/.tmp/expand"
export SESSION_DIR EXPAND_PROVIDER EXPAND_MODEL EXPAND_THINKING BRAVE_DIR
awk '{print $1}' "$MANIFEST_FILE" \
  | xargs -n1 -P "$EXPAND_PARALLEL" "$PWD/sources/expand_slot.sh"
log "expand finished in $((SECONDS - expand_start))s"

# Per-slot session logs (best effort).
: > "$LOG_DIR/expand.log.md"
while IFS=$'\t' read -r slot_id _slot_tag; do
  sess=$(newest_session "$SESSION_DIR/expand/$slot_id")
  if [[ -n "$sess" ]]; then
    uv run sources/inspect_session.py "$sess" --label "expand[$slot_id]" \
      >> "$LOG_DIR/expand.log.md" 2>/dev/null || true
  fi
done < "$MANIFEST_FILE"

# 5b. Stitch theme + per-slot bodies into the delivered brief.
# theme.md ends with the lede paragraph + a single newline; without an
# extra blank line, markdown renderers merge the first `## ...` heading
# into the lede paragraph as plain text.
{
  cat .tmp/expand/theme.md
  echo
  while IFS=$'\t' read -r slot_id _slot_tag; do
    body=".tmp/expand/$slot_id/body.md"
    # A valid card body always starts with a `## ` heading. Anything
    # else -- empty, a DROPPED marker, or stray model narration like
    # "(Empty response -- slot dropped ...)" -- is a malformed/drop
    # output and must never reach the delivered brief.
    if [[ -s "$body" ]] && [[ "$(head -c 3 "$body")" == "## " ]] \
       && ! grep -q 'DROPPED slot=' "$body"; then
      cat "$body"
      echo
    fi
  done < "$MANIFEST_FILE"
} > "$OUT"

# 5c. Aggregate dropped slots into logs (never into the delivered brief).
# Split-stage drops (plan slot whose Source URL failed verification
# against the signal sheet) come first; they never reached the manifest.
dropped_count=0
split_dropped=$(grep -c '^DROPPED ' "$LOG_DIR/split-plan.err" || true)
{
  echo "# Dropped slots ${RUN_ID}"
  echo
  if [[ "${split_dropped:-0}" -gt 0 ]]; then
    grep '^DROPPED ' "$LOG_DIR/split-plan.err" | sed 's/^DROPPED /- /'
  fi
  while IFS=$'\t' read -r slot_id slot_tag; do
    body=".tmp/expand/$slot_id/body.md"
    err=".tmp/expand/$slot_id/err.log"
    if [[ ! -s "$body" ]] || [[ "$(head -c 3 "$body" 2>/dev/null)" != "## " ]] \
       || grep -q 'DROPPED slot=' "$body"; then
      reason=""
      if grep -qE 'DROPPED slot=' "$body" 2>/dev/null; then
        reason=$(grep -oE 'DROPPED slot=[^ ]+ reason=.*' "$body" | head -1 | sed 's/^DROPPED //')
      elif grep -qE '^DROPPED ' "$err" 2>/dev/null; then
        reason=$(grep -E '^DROPPED ' "$err" | head -1 | sed 's/^DROPPED //')
      elif [[ -s "$body" ]]; then
        reason="reason=malformed expand output (no '## ' heading); see $body"
      fi
      if [[ -z "$reason" ]]; then
        reason="unknown (no DROPPED line on stderr; see $err)"
      fi
      echo "- slot=$slot_id tag=${slot_tag} ${reason}"
      dropped_count=$((dropped_count + 1))
    fi
  done < "$MANIFEST_FILE"
  if [[ "$dropped_count" -eq 0 && "${split_dropped:-0}" -eq 0 ]]; then
    echo "(none)"
  fi
} > "$LOG_DIR/dropped.md"
log "expand drops: ${dropped_count}/${SLOT_COUNT} (split-stage drops: ${split_dropped:-0})"

# 6. Aggregate summary
{
  echo "# pi-pulse run ${RUN_ID}"
  echo
  echo "- brief: \`${OUT}\`"
  if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
    echo "- delivery: \`${PI_PULSE_DELIVERY}/${RUN_ID}.md\`"
  fi
  echo "- session archive: \`${SESSION_DIR}/\`"
  echo "- card caps: tracked=${TRACKED} adjacent=${ADJACENT} bridge=${BRIDGE} followup=${FOLLOWUP}"
  echo "- scout caps: interests=${SCOUT_MAX_INTERESTS} queries=${SCOUT_QUERIES_PER_INTEREST}"
  echo "- signals: raw=${signals_raw} kept=${signals_kept} (ledger filter)"
  echo "- feedback digest: ${fb_census}"
  echo "- expand: slots=${SLOT_COUNT} parallel=${EXPAND_PARALLEL} drops=${dropped_count}"
  echo
  [[ -f "$LOG_DIR/distill.log.md" ]] && cat "$LOG_DIR/distill.log.md"
  [[ -f "$LOG_DIR/scout.log.md" ]]   && cat "$LOG_DIR/scout.log.md"
  [[ -f "$LOG_DIR/plan.log.md" ]]    && cat "$LOG_DIR/plan.log.md"
  [[ -f "$LOG_DIR/expand.log.md" ]]  && cat "$LOG_DIR/expand.log.md"
} > "$LOG_DIR/summary.md"

# 7. Bail if no card body landed (all slots dropped).
if [[ ! -s "$OUT" ]] || [[ "$dropped_count" -ge "$SLOT_COUNT" ]]; then
  log "ERROR: every expand slot dropped; brief is empty."
  log "       See $LOG_DIR/dropped.md and $LOG_DIR/summary.md."
  exit 1
fi

# 8. Dedup + deliver
log "appending seen URLs"
uv run sources/append_seen.py "$OUT" >> memory/seen_urls.jsonl

# Record Source URLs of fetch-failed slots so filter_signals excludes
# them from future runs. This runs only after the all-dropped bail
# above: if every slot dropped, the cause is usually systemic (e.g. a
# missing BRAVE_API_KEY), and recording those URLs would wrongly ban
# good sources.
log "recording unfetchable URLs"
uv run sources/append_unfetchable.py .tmp/expand \
  2>"$LOG_DIR/append-unfetchable.err" \
  >> memory/unfetchable_urls.jsonl

OUT_HTML="${OUT%.md}.html"
log "rendering HTML"
if ! uv run sources/render_html.py "$OUT" "$OUT_HTML" 2>"$LOG_DIR/render_html.err"; then
  log "WARN: html render failed; see $LOG_DIR/render_html.err"
  OUT_HTML=""
fi

# Feedback companion file: numbered card list the reader edits with
# rating marks, then ingests via scripts/ingest-feedback.sh. Generation
# is non-fatal; a failure here must not sink a delivered brief.
FEEDBACK="${OUT%.md}.feedback.md"
log "writing feedback template"
if ! uv run sources/build_feedback_template.py "$OUT" "$FEEDBACK" \
     2>"$LOG_DIR/feedback-template.err"; then
  log "WARN: feedback template failed; see $LOG_DIR/feedback-template.err"
  FEEDBACK=""
fi

if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  log "copying brief to $PI_PULSE_DELIVERY"
  cp "$OUT" "$PI_PULSE_DELIVERY/${RUN_ID}.md"
  if [[ -n "$OUT_HTML" && -f "$OUT_HTML" ]]; then
    cp "$OUT_HTML" "$PI_PULSE_DELIVERY/${RUN_ID}.html"
  fi
  if [[ -n "$FEEDBACK" && -f "$FEEDBACK" ]]; then
    cp "$FEEDBACK" "$PI_PULSE_DELIVERY/${RUN_ID}.feedback.md"
  fi
fi

log "done: $OUT"
log "summary: $LOG_DIR/summary.md"
