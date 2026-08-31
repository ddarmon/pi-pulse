#!/usr/bin/env bash
# Review and apply profile-suggest proposals (.tmp/profile_updates.md) to
# memory/interests.md, one at a time. Snapshots the profile to
# memory/interests-history/ before any write (same pattern as
# scripts/interview.sh) and prints a diff at the end. Proposals that
# cannot be located unambiguously are reported for manual application
# rather than guessed.
#
# Usage:
#   scripts/apply-updates.sh              # interactive accept/reject
#   scripts/apply-updates.sh --dry-run    # show what would change
#
# Makes zero model calls. Run scripts/suggest-profile.sh first to
# generate proposals.

set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

if [[ ! -f .tmp/profile_updates.md ]]; then
  echo "ERROR: .tmp/profile_updates.md not found. Run scripts/suggest-profile.sh first." >&2
  exit 1
fi

uv run sources/apply_updates.py "$@"
