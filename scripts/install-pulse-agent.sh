#!/usr/bin/env bash
# Install the scheduled daily pulse as a TCC-identifiable macOS app +
# LaunchAgent. The checkout may remain under ~/Documents: the user grants
# the "Pi Pulse" app access to Documents once, rather than granting broad
# access to bash, python, or node. Pointing launchd directly at bash makes
# a cold start deny node's guard-fetch reads with EPERM and abort the run.

set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

REPO="$PWD"
APP="$HOME/Applications/Pi Pulse.app"
APP_EXEC="$APP/Contents/MacOS/PiPulse"
APP_ID="com.user.pi-pulse"
PLIST="$HOME/Library/LaunchAgents/com.user.pi-pulse.plist"
LOG_DIR="$HOME/Library/Logs/pi-pulse"
STATUS_DIR="$HOME/Library/Application Support/pi-pulse"
STATUS_FILE="$STATUS_DIR/authorization-status"
DOMAIN="gui/$UID"
LABEL="$DOMAIN/$APP_ID"
REBUILD=0
SKIP_AUTHORIZE=0
HOUR=5
MINUTE=0

usage() {
  cat <<'EOF'
Usage: scripts/install-pulse-agent.sh [--rebuild-app] [--skip-authorize]
                                      [--hour H] [--minute M]

  --rebuild-app     Recompile the native app wrapper. This changes its code
                    identity and therefore requests Documents access again.
  --skip-authorize  Keep the app's existing TCC decision without prompting.
  --hour H          Daily run hour, 0-23 (default 5).
  --minute M        Daily run minute, 0-59 (default 0).

The rendered LaunchAgent inherits this shell's PATH so that pi, uv, sesh,
ollama, node, and curl resolve the same way they do interactively. Re-run
the installer after PATH-relevant toolchain changes (e.g. a new node
version under nvm).
EOF
}

while (($#)); do
  case "$1" in
    --rebuild-app) REBUILD=1 ;;
    --skip-authorize) SKIP_AUTHORIZE=1 ;;
    --hour) HOUR="${2:?--hour needs a value}"; shift ;;
    --minute) MINUTE="${2:?--minute needs a value}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ! [[ "$HOUR" =~ ^[0-9]+$ ]] || (( HOUR > 23 )); then
  echo "ERROR: --hour must be 0-23" >&2; exit 2
fi
if ! [[ "$MINUTE" =~ ^[0-9]+$ ]] || (( MINUTE > 59 )); then
  echo "ERROR: --minute must be 0-59" >&2; exit 2
fi

mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$LOG_DIR" "$STATUS_DIR"

if (( REBUILD )) || [[ ! -x "$APP_EXEC" ]]; then
  tmp_app="$(mktemp -d "${TMPDIR:-/tmp}/pi-pulse-app.XXXXXX")/Pi Pulse.app"
  mkdir -p "$tmp_app/Contents/MacOS"

  /usr/bin/swiftc launchd/pulse-app-main.swift -o "$tmp_app/Contents/MacOS/PiPulse"
  cat > "$tmp_app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>PiPulse</string>
  <key>CFBundleIdentifier</key><string>com.user.pi-pulse</string>
  <key>CFBundleName</key><string>Pi Pulse</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Pi Pulse reads your interest profile and writes the daily brief in the pi-pulse checkout.</string>
</dict>
</plist>
PLIST
  plutil -lint "$tmp_app/Contents/Info.plist" >/dev/null
  codesign --force --deep --sign - --identifier "$APP_ID" "$tmp_app" >/dev/null
  rm -rf "$APP"
  mv "$tmp_app" "$APP"
  echo "Installed native app: $APP"
else
  echo "Reusing native app and its existing TCC identity: $APP"
fi

launchctl bootout "$LABEL" 2>/dev/null || true
launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true

if (( ! SKIP_AUTHORIZE )); then
  rm -f "$STATUS_FILE"
  echo "Requesting Documents access for Pi Pulse…"
  open -W "$APP" --args --authorize --repo "$REPO"
  if [[ ! -f "$STATUS_FILE" ]] || [[ "$(<"$STATUS_FILE")" != "ok" ]]; then
    echo "ERROR: Pi Pulse was not granted Documents access." >&2
    [[ -f "$STATUS_FILE" ]] && sed 's/^/  /' "$STATUS_FILE" >&2
    exit 77
  fi
  echo "Documents access granted."
fi

sed -e "s|{{REPO}}|$REPO|g" \
    -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{APP_EXEC}}|$APP_EXEC|g" \
    -e "s|{{PATH}}|$PATH|g" \
    -e "s|{{HOUR}}|$HOUR|g" \
    -e "s|{{MINUTE}}|$MINUTE|g" \
    launchd/com.user.pi-pulse.plist.template > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$LABEL"

printf 'Scheduled pulse agent installed (runs daily at %02d:%02d).\n' "$HOUR" "$MINUTE"
echo "Trigger a run now with: launchctl kickstart $LABEL"
echo "Job logs: $LOG_DIR/pulse.{out,err}.log; per-run logs in $REPO/logs/<RUN_ID>/."
