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
#   PI_PROVIDER, PI_MODEL  pi backend selection
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

pi -p "$(cat prompts/compose_expand.md)" \
   --provider "$PI_PROVIDER" --model "$PI_MODEL" \
   --session-dir "$sess_dir" \
   @"$slot_dir/slot.md" @.tmp/interests_today.md @memory/seen_urls.jsonl \
   > "$slot_dir/body.md" \
   2> "$slot_dir/err.log" \
   || echo "DROPPED slot=$slot_id reason=pi-exit-nonzero" >> "$slot_dir/err.log"
