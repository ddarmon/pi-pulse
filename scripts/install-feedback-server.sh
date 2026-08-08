#!/usr/bin/env bash
# Install the feedback server as a TCC-identifiable macOS app + LaunchAgent.
# The checkout may remain under ~/Documents: the user grants this app access
# to Documents once, rather than granting Full Disk Access to bash or Python.

set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

REPO="$PWD"
APP="$HOME/Applications/Pi Pulse Feedback.app"
APP_EXEC="$APP/Contents/MacOS/PiPulseFeedback"
APP_ID="com.user.pi-pulse-feedback"
PLIST="$HOME/Library/LaunchAgents/com.user.pi-pulse-feedback.plist"
LOG_DIR="$HOME/Library/Logs/pi-pulse"
STATUS_DIR="$HOME/Library/Application Support/pi-pulse-feedback"
STATUS_FILE="$STATUS_DIR/authorization-status"
DOMAIN="gui/$UID"
LABEL="$DOMAIN/$APP_ID"
REBUILD=0
SKIP_AUTHORIZE=0

usage() {
  cat <<'EOF'
Usage: scripts/install-feedback-server.sh [--rebuild-app] [--skip-authorize]

  --rebuild-app     Recompile the native app wrapper. This changes its code
                    identity and therefore requests Documents access again.
  --skip-authorize  Keep the app's existing TCC decision without prompting.
EOF
}

while (($#)); do
  case "$1" in
    --rebuild-app) REBUILD=1 ;;
    --skip-authorize) SKIP_AUTHORIZE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$LOG_DIR" "$STATUS_DIR"

if (( REBUILD )) || [[ ! -x "$APP_EXEC" ]]; then
  tmp_app="$(mktemp -d "${TMPDIR:-/tmp}/pi-pulse-feedback-app.XXXXXX")/Pi Pulse Feedback.app"
  mkdir -p "$tmp_app/Contents/MacOS"

  /usr/bin/swiftc launchd/feedback-app-main.swift -o "$tmp_app/Contents/MacOS/PiPulseFeedback"
  cat > "$tmp_app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>PiPulseFeedback</string>
  <key>CFBundleIdentifier</key><string>com.user.pi-pulse-feedback</string>
  <key>CFBundleName</key><string>Pi Pulse Feedback</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Pi Pulse Feedback reads delivered briefs and writes your card ratings in the pi-pulse checkout.</string>
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

# Remove both the installed LaunchAgent and the temporary manual server used
# during diagnosis, so they cannot race for the feedback port.
launchctl bootout "$LABEL" 2>/dev/null || true
launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
if [[ -f .tmp/feedback-server.pid ]]; then
  old_pid="$(<.tmp/feedback-server.pid)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f .tmp/feedback-server.pid
fi

if (( ! SKIP_AUTHORIZE )); then
  rm -f "$STATUS_FILE"
  echo "Requesting Documents access for Pi Pulse Feedback…"
  open -W "$APP" --args --authorize --repo "$REPO"
  if [[ ! -f "$STATUS_FILE" ]] || [[ "$(<"$STATUS_FILE")" != "ok" ]]; then
    echo "ERROR: Pi Pulse Feedback was not granted Documents access." >&2
    [[ -f "$STATUS_FILE" ]] && sed 's/^/  /' "$STATUS_FILE" >&2
    exit 77
  fi
  echo "Documents access granted."
fi

sed -e "s|{{REPO}}|$REPO|g" \
    -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{APP_EXEC}}|$APP_EXEC|g" \
    -e "s|{{PATH}}|/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin|g" \
    launchd/com.user.pi-pulse-feedback.plist.template > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$LABEL"
launchctl kickstart "$LABEL"
sleep 2

state="$(launchctl print "$LABEL" 2>/dev/null | awk -F' = ' '/^[[:space:]]*state =/{print $2; exit}')"
pid="$(launchctl print "$LABEL" 2>/dev/null | awk -F' = ' '/^[[:space:]]*pid =/{print $2; exit}')"
if [[ "$state" != "running" ]] || [[ -z "$pid" ]]; then
  echo "ERROR: feedback LaunchAgent did not stay running." >&2
  launchctl print "$LABEL" >&2 || true
  echo "Logs: $LOG_DIR/feedback-server.{out,err}.log" >&2
  exit 1
fi

echo "Feedback server is running under launchd (pid $pid)."
echo "Logs: $LOG_DIR/feedback-server.{out,err}.log"
