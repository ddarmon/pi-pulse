#!/usr/bin/env bash
# Weekly profile-suggest. Reads the last N days of distill memos (or
# recent briefs as a fallback) plus the card-feedback digest, and asks pi
# for 0--6 small, evidence-grounded proposals to the durable profile.
# Proposals only -- this never edits memory/interests.md. Apply accepted
# ones with scripts/apply-updates.sh.
#
# Usage:
#   scripts/suggest-profile.sh [DAYS]      # default 7 (or PI_PULSE_SUGGEST_DAYS)
#
# Output:
#   .tmp/profile_updates.md   the proposals
#
# Cost: one no-tools pi call. Sessions land in
# .pulse-sessions/suggest/YYYY-MM-DD-HHMM/ (off ~/.pi/agent/sessions/ so
# they do not feed tomorrow's distill via sesh).

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PI_PROVIDER="${PI_PROVIDER:-ollama}"
PI_MODEL="${PI_MODEL:-kimi-k2.6:cloud}"
# This task is mechanical extraction/comparison, not deep reasoning. At
# the model's default thinking level kimi burned its entire 16,384-token
# output budget on reasoning (~72k chars) and emitted no/truncated
# proposals. `low` did not help (still ~72k chars of thinking); only
# `off` actually suppresses it (verified: ~130 chars on a trivial call),
# leaving the whole output budget for proposals. Override with
# PI_SUGGEST_THINKING if a future model needs some reasoning.
THINKING="${PI_SUGGEST_THINKING:-off}"
DAYS="${1:-${PI_PULSE_SUGGEST_DAYS:-7}}"
TS=$(date +%Y-%m-%d-%H%M)
SESSION_DIR=".pulse-sessions/suggest/${TS}"
PROMPT_FILE="prompts/suggest_profile.md"
PROFILE="memory/interests.md"
OUT=".tmp/profile_updates.md"

if [[ ! -f "$PROFILE" ]]; then
  echo "ERROR: $PROFILE not found. Run scripts/interview.sh first." >&2
  exit 1
fi

mkdir -p "$SESSION_DIR" .tmp

echo "[suggest] refreshing feedback digest"
uv run sources/build_feedback_digest.py >/dev/null

echo "[suggest] building input bundle (${DAYS}d)"
uv run sources/build_suggest_input.py --days "$DAYS"

# Double-brace placeholder substitution per repo convention (sed, not
# envsubst).
PROMPT=$(sed -e "s|{{DAYS}}|${DAYS}|g" "$PROMPT_FILE")

echo "[suggest] launching pi (${PI_PROVIDER}/${PI_MODEL}, thinking=${THINKING})"
pi -p "$PROMPT" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --thinking "$THINKING" \
   --no-skills \
   --session-dir "$SESSION_DIR" \
   @"$PROFILE" @.tmp/suggest_input.md \
   > "$OUT"

n=$(grep -c '^PROPOSAL:' "$OUT" || true)
echo
if grep -q '^NO PROPOSALS' "$OUT"; then
  echo "[suggest] no proposals: profile is current."
else
  echo "[suggest] ${n} proposal(s) written to $OUT"
  echo "[suggest] review/apply with: scripts/apply-updates.sh"
fi
