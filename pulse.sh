#!/usr/bin/env bash
# pi-pulse entrypoint. Runs the four-stage pipeline:
#   1. Collect inputs (notes / sesh / Anki) into .tmp/
#   2. Distill via Pi headless (no tools)
#   3. Compose via Pi headless (web search enabled)
#   4. Append URLs to seen ledger and copy brief to delivery dir.
#
# Configuration (env vars; see .env.example):
#   PI_PULSE_NOTES_DIR   Directory tree of YYYY/MM/DD/*.md notes
#   PI_PULSE_DELIVERY    Directory to copy the daily brief into
#   PI_PULSE_ANKI_SEARCH Path to anki_search.py (optional)
#   PI_PROVIDER          Pi provider (default: ollama)
#   PI_MODEL             Pi model    (default: kimi-k2.6:cloud)
#   PI_PULSE_NOTES_SINCE Days of notes history (default: 30)
#   PI_PULSE_SESH_SINCE  Days of sesh history  (default: 7)

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
PI_PROVIDER="${PI_PROVIDER:-ollama}"
PI_MODEL="${PI_MODEL:-kimi-k2.6:cloud}"
NOTES_SINCE="${PI_PULSE_NOTES_SINCE:-30}"
SESH_SINCE="${PI_PULSE_SESH_SINCE:-7}"

mkdir -p .tmp out logs
if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  mkdir -p "$PI_PULSE_DELIVERY"
fi

log() { echo "[$(date +%H:%M:%S)] $*"; }

# 0. Sanity: make sure ollama is up. Cloud-routed models (e.g.
# kimi-k2.6:cloud) still need the local ollama daemon to proxy.
if [[ "$PI_PROVIDER" == "ollama" ]]; then
  if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
    log "ollama not reachable on 11434; starting in background"
    nohup ollama serve >/dev/null 2>&1 &
    sleep 5
  fi
fi

# 1. Collect
log "collecting notes (${NOTES_SINCE}d)"
uv run sources/collect_obsidian.py --since "$NOTES_SINCE" > .tmp/chats_recent.md
log "collecting sesh sessions (${SESH_SINCE}d)"
uv run sources/collect_sesh.py     --since "$SESH_SINCE"  > .tmp/sesh_recent.md
log "collecting anki signals"
uv run sources/collect_anki.py                            > .tmp/anki_signals.md || true

# 2. Distill (no tools)
log "distill stage: ${PI_PROVIDER}/${PI_MODEL}"
pi -p "$(cat prompts/distill_context.md)" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --no-skills \
   @.tmp/chats_recent.md @.tmp/sesh_recent.md \
   @.tmp/anki_signals.md @memory/interests.md \
   > .tmp/interests_today.md

# 3. Compose (web search enabled via Pi's installed packages)
log "compose stage: ${PI_PROVIDER}/${PI_MODEL}"
pi -p "$(cat prompts/compose_brief.md)" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   @.tmp/interests_today.md @memory/seen_urls.jsonl \
   > "$OUT"

# 4. Dedup + deliver
log "appending seen URLs"
uv run sources/append_seen.py "$OUT" >> memory/seen_urls.jsonl

if [[ -n "${PI_PULSE_DELIVERY:-}" ]]; then
  log "copying brief to $PI_PULSE_DELIVERY"
  cp "$OUT" "$PI_PULSE_DELIVERY/${TODAY}.md"
fi

log "done: $OUT"
