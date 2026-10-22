"""
config.py - Loads settings from a JSON file.

We look for `config.json` first (your personal copy, which is git-ignored),
and fall back to `config.example.json` (the template committed to the repo).
That way your real router IP and any future private settings never get
pushed to GitHub.

Any setting missing from your file is filled in from DEFAULTS below, so an
older config.json keeps working when new features are added.
"""

import ipaddress
import json
from pathlib import Path

# The project root is the folder that contains this `netmon` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "interval_seconds": 60,
    "ping_count": 10,
    "database_path": "data/netmon.db",
    "alert_log_path": "logs/alerts.log",
    "dashboard_port": 8080,
    "notifications": True,
    "thresholds": {"packet_loss_pct": 5, "latency_ms": 100, "dns_ms": 200},
    # Learned baseline: flag latency that is unusual for THIS target.
    "baseline": {
        "window_hours": 24,      # how much history defines "normal"
        "min_samples": 30,       # don't judge until we have this many results
        "multiplier": 2.0,       # unusual = more than 2x the normal (median) latency...
        "min_increase_ms": 10,   # ...AND at least 10 ms above it (ignores tiny jitter)
    },
    # Self-health: warnings about the monitor itself.
    "health": {
        "stale_after_cycles": 3,     # no new data for 3 intervals = monitor probably stopped
        "unresponsive_window_hours": 1,
        "unresponsive_min_samples": 10,
    },
}


def guess_role(host):
    """Pick a role for a target that doesn't set one in the config:
      private IP (e.g. 10.0.0.1, 192.168.1.1) -> "gateway"  (your router)
      public IP  (e.g. 1.1.1.1)               -> "internet" (beyond your ISP)
      hostname   (e.g. google.com)            -> "website"
    Roles let the diagnosis say WHICH layer of the network has a problem."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "website"
    return "gateway" if ip.is_private else "internet"


def apply_defaults(config):
    """Fill in missing settings (one level deep for nested sections) and target roles."""
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            config[key] = {**default, **config.get(key, {})}
        else:
            config.setdefault(key, default)
    for target in config["targets"]:
        target.setdefault("role", guess_role(target["host"]))
    return config


def load_config():
    """Return the config as a Python dict."""
    for name in ("config.json", "config.example.json"):
        path = PROJECT_ROOT / name
        if path.exists():
            with open(path) as f:
                config = json.load(f)
            config["_source"] = name  # remember which file we used (for logging)
            return apply_defaults(config)
    raise FileNotFoundError("No config.json or config.example.json found in " + str(PROJECT_ROOT))


def resolve_path(relative_path):
    """Turn a path from the config (like 'data/netmon.db') into an absolute path
    inside the project, creating its parent folder if needed."""
    path = PROJECT_ROOT / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
