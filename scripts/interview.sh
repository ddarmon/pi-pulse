#!/usr/bin/env bash
# pi-pulse profile interview. Launches an interactive pi session that
# updates memory/interests.md via structured Q&A. The previous file is
# snapshotted to memory/interests-history/YYYY-MM-DD-HHMM.md before the
# interview begins, so every edit is reversible.
#
# Usage:
#   scripts/interview.sh
#
# Behavior:
#   - If memory/interests.md does not exist, seed it from the example.
#   - Snapshot the current profile to memory/interests-history/.
#   - Launch pi interactively with prompts/interview.md and the
#     current profile both attached to the first user message.
#   - On exit, print a diff against the snapshot. If unchanged, the
#     snapshot is discarded.
#
# Env (loaded from .env if present):
#   PI_PROVIDER  default: ollama
#   PI_MODEL     default: kimi-k2.6:cloud
#
# Sessions land in .pulse-sessions/interview/YYYY-MM-DD-HHMM/ (kept off
# ~/.pi/agent/sessions/ so they do not feed tomorrow's distill via sesh).

set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

PI_PROVIDER="${PI_PROVIDER:-ollama}"
PI_MODEL="${PI_MODEL:-kimi-k2.6:cloud}"
TS=$(date +%Y-%m-%d-%H%M)
SESSION_DIR=".pulse-sessions/interview/${TS}"
HISTORY_DIR="memory/interests-history"
PROFILE="memory/interests.md"
SEED="memory/interests.md.example"
PROMPT="prompts/interview.md"

if [[ ! -f "$PROMPT" ]]; then
  echo "ERROR: $PROMPT not found." >&2
  exit 1
fi

mkdir -p "$SESSION_DIR" "$HISTORY_DIR"

if [[ ! -f "$PROFILE" ]]; then
  if [[ ! -f "$SEED" ]]; then
    echo "ERROR: $SEED not found; cannot seed interview." >&2
    exit 1
  fi
  echo "[interview] seeding $PROFILE from $SEED"
  cp "$SEED" "$PROFILE"
fi

SNAPSHOT="$HISTORY_DIR/${TS}.md"
cp "$PROFILE" "$SNAPSHOT"
PRE_HASH=$(shasum "$PROFILE" | awk '{print $1}')

echo "[interview] snapshot: $SNAPSHOT"
echo "[interview] session : $SESSION_DIR"
echo "[interview] launching pi (${PI_PROVIDER}/${PI_MODEL})"
echo

# The interview prompt is delivered as the first @file attachment, not
# via --system-prompt: pi's --system-prompt (and --append-system-prompt)
# is silently dropped by the ollama OpenAI-compat provider path --- the
# string never enters the /v1/chat/completions payload (verified by token
# accounting 2026-07-05). Do not switch back.
env -u BRAVE_API_KEY pi --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --no-skills \
   --session-dir "$SESSION_DIR" \
   @"$PROMPT" \
   @"$PROFILE" \
   "Begin the interview." || true

POST_HASH=$(shasum "$PROFILE" | awk '{print $1}')

echo
if [[ "$PRE_HASH" == "$POST_HASH" ]]; then
  echo "[interview] $PROFILE unchanged; discarding snapshot."
  rm -f "$SNAPSHOT"
else
  echo "[interview] $PROFILE updated. Diff vs $SNAPSHOT:"
  echo
  diff -u "$SNAPSHOT" "$PROFILE" || true
fi
