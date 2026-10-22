#!/bin/bash
# stop_service.sh - Stop the background monitor and remove its LaunchAgent.
# Your data (data/netmon.db) and logs are kept.

LABEL="local.network-monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped." || echo "It wasn't running."
rm -f "$PLIST"
echo "Removed $PLIST"
