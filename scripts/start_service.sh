#!/bin/bash
# start_service.sh - Run the monitor in the background for a set number of days.
#
#   ./scripts/start_service.sh        # 7 days (default)
#   ./scripts/start_service.sh 3      # 3 days
#
# How it works: macOS runs background programs with "launchd". We write a small
# settings file (a "LaunchAgent" .plist) into ~/Library/LaunchAgents that tells
# launchd to:
#   - start monitor.py now, and again whenever you log in (RunAtLoad)
#   - restart it if it crashes, but NOT after a normal exit (KeepAlive/SuccessfulExit)
#   - keep the Mac from idle-sleeping while it runs (via `caffeinate -i`)
#   - pass --until <date>, so the monitor exits normally when the period is over
# Undo everything with ./scripts/stop_service.sh

set -euo pipefail

DAYS="${1:-7}"
LABEL="local.network-monitor"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"          # the repo folder
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$(command -v python3)"                             # full path: launchd has no PATH
UNTIL="$(date -v+"${DAYS}"d '+%Y-%m-%dT%H:%M')"            # now + N days, local time

if [ ! -f "$PROJECT_DIR/config.json" ]; then
  echo "No config.json yet. Run: cp config.example.json config.json  (then set your router IP)"
  exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$PYTHON</string>
    <string>-u</string>
    <string>$PROJECT_DIR/monitor.py</string>
    <string>--until</string>
    <string>$UNTIL</string>
  </array>
  <key>WorkingDirectory</key>  <string>$PROJECT_DIR</string>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>  <false/>
  </dict>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>StandardOutPath</key>   <string>$PROJECT_DIR/logs/monitor.out.log</string>
  <key>StandardErrorPath</key> <string>$PROJECT_DIR/logs/monitor.err.log</string>
</dict>
</plist>
PLIST_END

# Replace any previous copy, then load the new one into your login session.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Monitor running in the background until $UNTIL."
echo "  Watch it:   tail -f \"$PROJECT_DIR/logs/monitor.out.log\""
echo "  Dashboard:  python3 dashboard.py   then open http://127.0.0.1:8080"
echo "  Stop early: ./scripts/stop_service.sh"
