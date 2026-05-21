#!/usr/bin/env bash
# pi-pulse entrypoint. Runs the source-first pipeline:
#   1. Collect inputs (notes / sesh / Anki) into .tmp/
#   2. Distill via Pi headless (no tools): five-section memo
#   3. Scout via Pi headless (web search/fetch enabled): probe brave-
#      search per interest cluster, emit structured signals.md so the
#      plan stage picks from real evidence, not imagined sources
#   4. Plan via Pi headless (no tools): rank scout signals into
#      TRACKED / ADJACENT / BRIDGE / FOLLOWUP card slots, each with a
#      committed Source URL drawn verbatim from signals.md
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
# Sessions for every pi call are routed to .pulse-sessions/YYYY-MM-DD/
# (gitignored) so they do NOT pollute ~/.pi/agent/sessions/ and never
# feed tomorrow's distill via sesh discovery.
#
# Per-run logs land in logs/YYYY-MM-DD/:
#   distill.log.md  scout.log.md  plan.log.md  expand.log.md
#   dropped.md  summary.md  *.err
#
# Configuration (env vars; see .env.example):
#   PI_PULSE_NOTES_DIR        Directory tree of YYYY/MM/DD/*.md notes
#   PI_PULSE_DELIVERY         Directory to copy the daily brief into
#   PI_PULSE_ANKI_SEARCH      Path to anki_search.py (optional)
#   PI_PROVIDER               Pi provider (default: ollama)
#   PI_MODEL                  Pi model    (default: kimi-k2.6:cloud)
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

TODAY=$(date +%F)
OUT="out/${TODAY}.md"
SESSION_DIR=".pulse-sessions/${TODAY}"
LOG_DIR="logs/${TODAY}"
PI_PROVIDER="${PI_PROVIDER:-ollama}"
PI_MODEL="${PI_MODEL:-kimi-k2.6:cloud}"
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
export TRACKED ADJACENT BRIDGE FOLLOWUP
export SCOUT_MAX_INTERESTS SCOUT_QUERIES_PER_INTEREST EXPAND_PARALLEL

mkdir -p .tmp .tmp/expand out "$LOG_DIR" \
  "$SESSION_DIR/distill" "$SESSION_DIR/scout" \
  "$SESSION_DIR/plan" "$SESSION_DIR/expand"
if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  mkdir -p "$PI_PULSE_DELIVERY"
fi

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Find the newest .jsonl under a directory (recursive).
newest_session() {
  find "$1" -type f -name '*.jsonl' -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null \
    | head -n 1
}

# 0. Sanity: make sure ollama is up. Cloud-routed models still need the
# local daemon to proxy.
if [[ "$PI_PROVIDER" == "ollama" ]]; then
  if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
    log "ollama not reachable on 11434; starting in background"
    nohup ollama serve >/dev/null 2>&1 &
    sleep 5
  fi
fi

# 1. Collect
log "collecting notes (${NOTES_SINCE}d)"
uv run sources/collect_obsidian.py --since "$NOTES_SINCE" > .tmp/chats_recent.md 2>"$LOG_DIR/collect-obsidian.err"
log "collecting sesh sessions (${SESH_SINCE}d)"
uv run sources/collect_sesh.py     --since "$SESH_SINCE" --exclude-cwd "$PWD" > .tmp/sesh_recent.md 2>"$LOG_DIR/collect-sesh.err"
log "collecting anki signals"
uv run sources/collect_anki.py                            > .tmp/anki_signals.md 2>"$LOG_DIR/collect-anki.err" || true

# 1b. Build recent-pulses bundle (scout and plan both consume this).
log "building recent-pulses bundle (${HISTORY_DAYS}d, today excluded)"
uv run sources/build_recent_pulses.py --days "$HISTORY_DAYS" \
   > .tmp/recent_pulses.md \
   2>"$LOG_DIR/build-recent.err"

# 2. Distill (no tools)
log "distill stage: ${PI_PROVIDER}/${PI_MODEL}"
distill_start=$SECONDS
pi -p "$(cat prompts/distill_context.md)" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --no-skills \
   --session-dir "$SESSION_DIR/distill" \
   @.tmp/chats_recent.md @.tmp/sesh_recent.md \
   @.tmp/anki_signals.md @memory/interests.md \
   > .tmp/interests_today.md \
   2>"$LOG_DIR/distill.err"
log "distill finished in $((SECONDS - distill_start))s"
distill_session=$(newest_session "$SESSION_DIR/distill")
if [[ -n "$distill_session" ]]; then
  uv run sources/inspect_session.py "$distill_session" --label "distill" \
    > "$LOG_DIR/distill.log.md"
fi
if [[ ! -s .tmp/interests_today.md ]]; then
  log "ERROR: distill stage produced empty output. See $LOG_DIR/distill.err"
  exit 1
fi

# 3. Scout (web search/fetch enabled): discover fresh primary sources
# per interest cluster, emit structured signals.md.
log "scout stage: ${PI_PROVIDER}/${PI_MODEL} (interests<=${SCOUT_MAX_INTERESTS} queries<=${SCOUT_QUERIES_PER_INTEREST})"
scout_start=$SECONDS
SCOUT_PROMPT=$(sed -e "s|{{SCOUT_MAX_INTERESTS}}|${SCOUT_MAX_INTERESTS}|g" \
                   -e "s|{{SCOUT_QUERIES_PER_INTEREST}}|${SCOUT_QUERIES_PER_INTEREST}|g" \
                   prompts/scout_signals.md)
pi -p "$SCOUT_PROMPT" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --session-dir "$SESSION_DIR/scout" \
   @.tmp/interests_today.md @memory/interests.md \
   @memory/seen_urls.jsonl @.tmp/recent_pulses.md \
   > .tmp/signals.md \
   2>"$LOG_DIR/scout.err"
log "scout finished in $((SECONDS - scout_start))s"
scout_session=$(newest_session "$SESSION_DIR/scout")
if [[ -n "$scout_session" ]]; then
  uv run sources/inspect_session.py "$scout_session" --label "scout" \
    > "$LOG_DIR/scout.log.md"
fi
if [[ ! -s .tmp/signals.md ]]; then
  log "ERROR: scout stage produced empty signals. See $LOG_DIR/scout.err"
  exit 1
fi

# 4. Plan (no tools): rank scout signals into card slots with committed URLs.
log "plan stage: ${PI_PROVIDER}/${PI_MODEL} (caps T=${TRACKED} A=${ADJACENT} B=${BRIDGE} F=${FOLLOWUP})"
plan_start=$SECONDS
PLAN_PROMPT=$(sed -e "s|{{TRACKED}}|${TRACKED}|g" \
                  -e "s|{{ADJACENT}}|${ADJACENT}|g" \
                  -e "s|{{BRIDGE}}|${BRIDGE}|g" \
                  -e "s|{{FOLLOWUP}}|${FOLLOWUP}|g" \
                  prompts/compose_plan.md)
pi -p "$PLAN_PROMPT" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --no-skills \
   --session-dir "$SESSION_DIR/plan" \
   @.tmp/signals.md @.tmp/interests_today.md \
   @.tmp/recent_pulses.md @memory/seen_urls.jsonl \
   > .tmp/plan.md \
   2>"$LOG_DIR/plan.err"
log "plan finished in $((SECONDS - plan_start))s"
plan_session=$(newest_session "$SESSION_DIR/plan")
if [[ -n "$plan_session" ]]; then
  uv run sources/inspect_session.py "$plan_session" --label "plan" \
    > "$LOG_DIR/plan.log.md"
fi
if [[ ! -s .tmp/plan.md ]]; then
  log "ERROR: plan stage produced empty output. See $LOG_DIR/plan.err"
  exit 1
fi

# 5. Expand (per-card parallel; web search/fetch enabled).
log "splitting plan into per-slot files"
rm -rf .tmp/expand
mkdir -p .tmp/expand
MANIFEST_FILE=".tmp/expand/manifest.tsv"
if ! uv run sources/split_plan.py .tmp/plan.md .tmp/expand \
       > "$MANIFEST_FILE" 2>"$LOG_DIR/split-plan.err"; then
  log "ERROR: split_plan failed. See $LOG_DIR/split-plan.err"
  exit 1
fi
SLOT_COUNT=$(wc -l < "$MANIFEST_FILE" | tr -d ' ')
if [[ "$SLOT_COUNT" -eq 0 ]]; then
  log "ERROR: split_plan produced no slots. See $LOG_DIR/split-plan.err"
  exit 1
fi

log "expand stage: ${PI_PROVIDER}/${PI_MODEL} (slots=${SLOT_COUNT} parallel=${EXPAND_PARALLEL})"
expand_start=$SECONDS
export REPO_ROOT="$PWD"
export EXPAND_DIR="$PWD/.tmp/expand"
export SESSION_DIR PI_PROVIDER PI_MODEL
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
{
  cat .tmp/expand/theme.md
  while IFS=$'\t' read -r slot_id _slot_tag; do
    body=".tmp/expand/$slot_id/body.md"
    if [[ -s "$body" ]]; then
      cat "$body"
      echo
    fi
  done < "$MANIFEST_FILE"
} > "$OUT"

# 5c. Aggregate dropped slots into logs (never into the delivered brief).
dropped_count=0
{
  echo "# Dropped slots ${TODAY}"
  echo
  while IFS=$'\t' read -r slot_id slot_tag; do
    body=".tmp/expand/$slot_id/body.md"
    err=".tmp/expand/$slot_id/err.log"
    if [[ ! -s "$body" ]]; then
      reason=""
      if [[ -s "$err" ]]; then
        reason=$(grep -E '^DROPPED ' "$err" | head -1 | sed 's/^DROPPED //')
      fi
      if [[ -z "$reason" ]]; then
        reason="unknown (no DROPPED line on stderr; see $err)"
      fi
      echo "- slot=$slot_id tag=${slot_tag} ${reason}"
      dropped_count=$((dropped_count + 1))
    fi
  done < "$MANIFEST_FILE"
  if [[ "$dropped_count" -eq 0 ]]; then
    echo "(none)"
  fi
} > "$LOG_DIR/dropped.md"
log "expand drops: ${dropped_count}/${SLOT_COUNT}"

# 6. Aggregate summary
{
  echo "# pi-pulse run ${TODAY}"
  echo
  echo "- brief: \`${OUT}\`"
  if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
    echo "- delivery: \`${PI_PULSE_DELIVERY}/${TODAY}.md\`"
  fi
  echo "- session archive: \`${SESSION_DIR}/\`"
  echo "- card caps: tracked=${TRACKED} adjacent=${ADJACENT} bridge=${BRIDGE} followup=${FOLLOWUP}"
  echo "- scout caps: interests=${SCOUT_MAX_INTERESTS} queries=${SCOUT_QUERIES_PER_INTEREST}"
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

OUT_HTML="${OUT%.md}.html"
log "rendering HTML"
if ! uv run sources/render_html.py "$OUT" "$OUT_HTML" 2>"$LOG_DIR/render_html.err"; then
  log "WARN: html render failed; see $LOG_DIR/render_html.err"
  OUT_HTML=""
fi

if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  log "copying brief to $PI_PULSE_DELIVERY"
  cp "$OUT" "$PI_PULSE_DELIVERY/${TODAY}.md"
  if [[ -n "$OUT_HTML" && -f "$OUT_HTML" ]]; then
    cp "$OUT_HTML" "$PI_PULSE_DELIVERY/${TODAY}.html"
  fi
fi

log "done: $OUT"
log "summary: $LOG_DIR/summary.md"
