"""
notify.py - Pops up a desktop notification so you see problems without
watching the terminal or dashboard.

  macOS: uses the built-in `osascript` (AppleScript) command
  Linux: uses `notify-send` if it is installed
  other: does nothing

The message is passed as a separate argument (`argv`), never pasted into
the script text, so odd characters in a target name can't break or inject
into the AppleScript.
"""

import shutil
import subprocess
import sys

APPLESCRIPT = [
    "-e", "on run argv",
    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
    "-e", "end run",
]


def notify(title, message):
    """Show a notification. Never raises: a failed popup must not stop monitoring."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", *APPLESCRIPT, title, message], timeout=10, check=False)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
