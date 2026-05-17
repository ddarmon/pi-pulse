#!/usr/bin/env bash
# pi-pulse entrypoint. Runs the four-stage pipeline:
#   1. Collect inputs (notes / sesh / Anki) into .tmp/
#   2. Distill via Pi headless (no tools)
#   3. Compose via Pi headless (web search enabled)
#   4. Append URLs to seen ledger and copy brief to delivery dir.
#
# Sessions for both pi calls are routed to .pulse-sessions/YYYY-MM-DD/
# (gitignored) so they do NOT pollute ~/.pi/agent/sessions/ and never
# feed tomorrow's distill via sesh discovery.
#
# Per-run logs land in logs/YYYY-MM-DD/:
#   distill.log.md  compose.log.md  summary.md  errors.log
#
# Configuration (env vars; see .env.example):
#   PI_PULSE_NOTES_DIR    Directory tree of YYYY/MM/DD/*.md notes
#   PI_PULSE_DELIVERY     Directory to copy the daily brief into
#   PI_PULSE_ANKI_SEARCH  Path to anki_search.py (optional)
#   PI_PROVIDER           Pi provider (default: ollama)
#   PI_MODEL              Pi model    (default: kimi-k2.6:cloud)
#   PI_PULSE_NOTES_SINCE  Days of notes history (default: 30)
#   PI_PULSE_SESH_SINCE   Days of sesh history  (default: 7)

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

mkdir -p .tmp out "$LOG_DIR" "$SESSION_DIR/distill" "$SESSION_DIR/compose"
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
uv run sources/collect_sesh.py     --since "$SESH_SINCE"  > .tmp/sesh_recent.md  2>"$LOG_DIR/collect-sesh.err"
log "collecting anki signals"
uv run sources/collect_anki.py                            > .tmp/anki_signals.md 2>"$LOG_DIR/collect-anki.err" || true

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

# 3. Compose (web search enabled via Pi's installed packages)
log "compose stage: ${PI_PROVIDER}/${PI_MODEL}"
compose_start=$SECONDS
pi -p "$(cat prompts/compose_brief.md)" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --session-dir "$SESSION_DIR/compose" \
   @.tmp/interests_today.md @memory/seen_urls.jsonl \
   > "$OUT" \
   2>"$LOG_DIR/compose.err"
log "compose finished in $((SECONDS - compose_start))s"
compose_session=$(newest_session "$SESSION_DIR/compose")
if [[ -n "$compose_session" ]]; then
  uv run sources/inspect_session.py "$compose_session" --label "compose" \
    > "$LOG_DIR/compose.log.md"
fi

# 4. Aggregate summary
{
  echo "# pi-pulse run ${TODAY}"
  echo
  echo "- brief: \`${OUT}\`"
  if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
    echo "- delivery: \`${PI_PULSE_DELIVERY}/${TODAY}.md\`"
  fi
  echo "- session archive: \`${SESSION_DIR}/\`"
  echo
  [[ -f "$LOG_DIR/distill.log.md" ]] && cat "$LOG_DIR/distill.log.md"
  [[ -f "$LOG_DIR/compose.log.md" ]] && cat "$LOG_DIR/compose.log.md"
} > "$LOG_DIR/summary.md"

# 5. Dedup + deliver
log "appending seen URLs"
uv run sources/append_seen.py "$OUT" >> memory/seen_urls.jsonl

if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  log "copying brief to $PI_PULSE_DELIVERY"
  cp "$OUT" "$PI_PULSE_DELIVERY/${TODAY}.md"
fi

log "done: $OUT"
log "summary: $LOG_DIR/summary.md"
