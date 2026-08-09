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
#   PI_PULSE_EGRESS_LOG  per-run JSONL egress log (optional; defaults from RUN_ID)
#
# Output:
#   $EXPAND_DIR/$slot_id/body.md   card body (## heading + prose)
#   $EXPAND_DIR/$slot_id/err.log   stderr (incl. DROPPED ... lines)
#
# The committed URL is read only from manifest.tsv. Fetching happens in the
# deterministic guard before Pi starts; the model receives the bounded page as
# an attachment and runs with no tools.

set -uo pipefail

slot_id="$1"
cd "$REPO_ROOT"

slot_dir="$EXPAND_DIR/$slot_id"
sess_dir="$SESSION_DIR/expand/$slot_id"
mkdir -p "$sess_dir"
: > "$slot_dir/err.log"

# split_plan.py has already parsed and verified this URL against signals.md.
# Do not re-parse slot.md here: the manifest is the trusted handoff boundary.
src_url=$(awk -F'\t' -v s="$slot_id" '$1 == s { print $3; exit }' \
  "$EXPAND_DIR/manifest.tsv")
if [[ -z "$src_url" ]]; then
  echo "DROPPED slot=$slot_id reason=no-committed-url" >> "$slot_dir/err.log"
  exit 0
fi

guard_dir="$REPO_ROOT/sources/brave-guard"
fetch_env=(
  -u BRAVE_API_KEY
  "REPO_ROOT=$REPO_ROOT"
  "PI_PULSE_EGRESS_STAGE=expand"
  "PI_PULSE_EGRESS_SLOT=$slot_id"
)

# Fetch exactly the committed URL outside the model. If direct extraction
# fails, preserve the historical one-search fallback and attach its bounded
# snippets. Only a double failure drops the slot.
used_fallback=0
if ! env "${fetch_env[@]}" "$guard_dir/content.js" "$src_url" \
     > "$slot_dir/page.md" 2> "$slot_dir/fetch.err"; then
  used_fallback=1
  title=$(sed -n 's/^[[:space:]]*-[[:space:]]*\*\*Title:\*\*[[:space:]]*//p' \
    "$slot_dir/slot.md" | head -n 1)
  query="${title:-$src_url}"
  if ! env "${fetch_env[@]}" "$guard_dir/search.js" "$query" -n 5 \
       > "$slot_dir/page.md" 2>> "$slot_dir/fetch.err"; then
    echo "DROPPED slot=$slot_id reason=fetch-failed" >> "$slot_dir/err.log"
    exit 0
  fi
fi

# The no-results marker is search.js output; a fetched page may legitimately
# contain that sentence, so only check it when the fallback ran.
if [[ ! -s "$slot_dir/page.md" ]] || { (( used_fallback )) \
   && grep -qx 'No results found\.' "$slot_dir/page.md"; }; then
  echo "DROPPED slot=$slot_id reason=fetch-failed" >> "$slot_dir/err.log"
  exit 0
fi

# Record how this card is grounded. A fallback card is written from search
# snippets rather than the committed primary source, which is a real quality
# degradation that produces no drop and is otherwise invisible in the run
# record. pulse.sh reports the census so it cannot pass unnoticed.
if (( used_fallback )); then
  echo "search-fallback" > "$slot_dir/grounding"
else
  echo "fetch" > "$slot_dir/grounding"
fi

# Resolve expand backend, falling back to the global PI_* for safety.
provider="${EXPAND_PROVIDER:-${PI_PROVIDER:-ollama}}"
model="${EXPAND_MODEL:-${PI_MODEL:-kimi-k2.6:cloud}}"
think_args=()
[[ -n "${EXPAND_THINKING:-}" ]] && think_args=(--thinking "$EXPAND_THINKING")

pi_command=(
  env -u BRAVE_API_KEY pi -p "$(cat prompts/compose_expand.md)"
  --provider "$provider" --model "$model"
)
if (( ${#think_args[@]} )); then
  pi_command+=("${think_args[@]}")
fi
pi_command+=(
  --no-tools --no-context-files --no-extensions --no-skills
  --session-dir "$sess_dir"
  @"$slot_dir/slot.md" @"$slot_dir/page.md" @.tmp/interests_web.md
)

if ! uv run sources/log_capability.py expand --slot "$slot_id" -- "${pi_command[@]}"; then
  echo "DROPPED slot=$slot_id reason=capability-log-failed" >> "$slot_dir/err.log"
  exit 0
fi

"${pi_command[@]}" \
  > "$slot_dir/body.md" \
  2>> "$slot_dir/err.log" \
  || echo "DROPPED slot=$slot_id reason=pi-exit-nonzero" >> "$slot_dir/err.log"
