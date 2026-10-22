"""
config.py - Loads settings from a JSON file.

We look for `config.json` first (your personal copy, which is git-ignored),
and fall back to `config.example.json` (the template committed to the repo).
That way your real router IP and any future private settings never get
pushed to GitHub.
"""

import json
from pathlib import Path

# The project root is the folder that contains this `netmon` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config():
    """Return the config as a Python dict."""
    for name in ("config.json", "config.example.json"):
        path = PROJECT_ROOT / name
        if path.exists():
            with open(path) as f:
                config = json.load(f)
            config["_source"] = name  # remember which file we used (for logging)
            return config
    raise FileNotFoundError("No config.json or config.example.json found in " + str(PROJECT_ROOT))


def resolve_path(relative_path):
    """Turn a path from the config (like 'data/netmon.db') into an absolute path
    inside the project, creating its parent folder if needed."""
    path = PROJECT_ROOT / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
