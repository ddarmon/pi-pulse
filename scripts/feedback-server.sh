#!/usr/bin/env bash
# Serve the feedback rating web UI (sources/feedback_server.py): browse
# out/*.html briefs from any device on the tailnet and tap ratings that
# are written straight back into out/*.feedback.md -- the same files the
# TUI reviewer edits and the next pulse.sh run auto-ingests.
#
# Usage:
#   scripts/feedback-server.sh                       # 127.0.0.1:8377
#   scripts/feedback-server.sh --host tailscale      # bind tailnet IPv4
#   scripts/feedback-server.sh --port 9000
#
# Host/port also come from PI_PULSE_FEEDBACK_HOST / PI_PULSE_FEEDBACK_PORT
# in .env (flags win). Stdlib-only Python; makes zero model calls.

set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi
export PI_PULSE_FEEDBACK_HOST PI_PULSE_FEEDBACK_PORT

exec python3 sources/feedback_server.py "$@"
