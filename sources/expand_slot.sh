#!/usr/bin/env bash
# Run pi to expand a single planned card slot. Invoked once per slot
# by pulse.sh via xargs -P for bounded parallelism.
#
# Args:
#   $1  slot_id  (zero-padded slot number from split_plan.py manifest)
#
# Env (exported by pulse.sh):
#   REPO_ROOT        repo root (this script cd's there before invoking pi)
#   EXPAND_DIR       e.g. .tmp/expand (absolute or repo-relative)
#   SESSION_DIR      e.g. .pulse-sessions/YYYY-MM-DD
#   EXPAND_PROVIDER, EXPAND_MODEL  pi backend selection for the expand stage
#                    (fall back to PI_PROVIDER/PI_MODEL if unset)
#   EXPAND_THINKING  optional pi --thinking level (empty = flag not passed)
#
# Output:
#   $EXPAND_DIR/$slot_id/body.md   card body (## heading + prose)
#   $EXPAND_DIR/$slot_id/err.log   stderr (incl. DROPPED ... lines)
#
# This script never errors out the parent loop -- if pi fails, the
# slot's body.md is empty and pulse.sh aggregates that as a drop.

set -uo pipefail

slot_id="$1"
cd "$REPO_ROOT"

slot_dir="$EXPAND_DIR/$slot_id"
sess_dir="$SESSION_DIR/expand/$slot_id"
mkdir -p "$sess_dir"

# Resolve expand backend, falling back to the global PI_* for safety.
provider="${EXPAND_PROVIDER:-${PI_PROVIDER:-ollama}}"
model="${EXPAND_MODEL:-${PI_MODEL:-kimi-k2.6:cloud}}"
think_args=()
[[ -n "${EXPAND_THINKING:-}" ]] && think_args=(--thinking "$EXPAND_THINKING")

pi -p "$(cat prompts/compose_expand.md)" \
   --provider "$provider" --model "$model" \
   ${think_args[@]+"${think_args[@]}"} \
   --session-dir "$sess_dir" \
   @"$slot_dir/slot.md" @.tmp/interests_today.md @memory/seen_urls.jsonl \
   > "$slot_dir/body.md" \
   2> "$slot_dir/err.log" \
   || echo "DROPPED slot=$slot_id reason=pi-exit-nonzero" >> "$slot_dir/err.log"
