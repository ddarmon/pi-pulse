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
umask 077
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

PI_PROVIDER="${PI_PROVIDER:-ollama}"
# This stage deliberately does NOT use the pipeline's kimi-k2.6:cloud.
# That model emits ~72k chars of inline chain-of-thought on this
# evaluative task regardless of --thinking (off only suppresses it on
# trivial prompts), saturating the fixed 16,384-token output cap and
# emitting no/truncated proposals. gemma4:31b-cloud is a non-reasoning
# instruct model: it produced 3 clean proposals in ~6s / 449 output
# tokens. Override with PI_SUGGEST_MODEL.
SUGGEST_MODEL="${PI_SUGGEST_MODEL:-gemma4:31b-cloud}"
# Optional thinking level; empty means don't pass --thinking at all
# (gemma is non-reasoning and needs none). Set if you point
# PI_SUGGEST_MODEL at a reasoning model that honors it.
THINKING="${PI_SUGGEST_THINKING:-}"
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
# The weekly suggest stage wants the FULL digest (all rated cards over
# the window), so it is explicitly uncapped -- unlike the daily plan
# path, which caps sections via PI_PULSE_FEEDBACK_DIGEST_MAX.
uv run sources/build_feedback_digest.py --max-per-section 0 >/dev/null

echo "[suggest] building input bundle (${DAYS}d)"
uv run sources/build_suggest_input.py --days "$DAYS"

# Double-brace placeholder substitution per repo convention (sed, not
# envsubst).
PROMPT=$(sed -e "s|{{DAYS}}|${DAYS}|g" "$PROMPT_FILE")

think_args=()
[[ -n "$THINKING" ]] && think_args=(--thinking "$THINKING")
echo "[suggest] launching pi (${PI_PROVIDER}/${SUGGEST_MODEL}${THINKING:+, thinking=$THINKING})"
env -u BRAVE_API_KEY pi -p "$PROMPT" \
   --provider "$PI_PROVIDER" --model "$SUGGEST_MODEL" \
   ${think_args[@]+"${think_args[@]}"} \
   --no-tools --no-context-files --no-extensions --no-skills \
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
