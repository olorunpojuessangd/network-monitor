"""
storage.py - Saves and reads results using SQLite.

SQLite is a full SQL database stored in a single file (data/netmon.db).
It ships with Python, needs no server, and you can open the file with any
SQLite tool (e.g. `sqlite3 data/netmon.db`) to run your own queries.

Two tables:
  results - one row per target per check cycle (every 60 s)
  alerts  - one row each time a target becomes DEGRADED or RECOVERED
"""

import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,      -- UTC time, ISO 8601 format
    target          TEXT NOT NULL,      -- friendly name, e.g. "Router"
    host            TEXT NOT NULL,      -- IP or hostname that was checked
    latency_ms      REAL,               -- average round trip; NULL if no replies
    packet_loss_pct REAL NOT NULL,      -- 0 to 100
    dns_ms          REAL,               -- NULL for IP targets or failed lookups
    degraded        INTEGER NOT NULL,   -- 1 if any threshold was broken
    reason          TEXT                -- why it was degraded (human-readable)
);
CREATE INDEX IF NOT EXISTS idx_results_ts ON results(ts);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    target  TEXT NOT NULL,
    kind    TEXT NOT NULL,              -- "DEGRADED" or "RECOVERED"
    message TEXT NOT NULL
);
"""


def now_utc():
    """Current time as an ISO string, e.g. '2026-10-21T14:03:00+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path):
    """Open the database (creating tables on first run) and return a connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # lets us read columns by name
    # WAL mode lets the dashboard read while the monitor is writing.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_result(conn, ts, result, degraded, reason):
    conn.execute(
        "INSERT INTO results (ts, target, host, latency_ms, packet_loss_pct, dns_ms, degraded, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, result["target"], result["host"], result["latency_ms"],
         result["packet_loss_pct"], result["dns_ms"], int(degraded), reason),
    )
    conn.commit()


def save_alert(conn, ts, target, kind, message):
    conn.execute(
        "INSERT INTO alerts (ts, target, kind, message) VALUES (?, ?, ?, ?)",
        (ts, target, kind, message),
    )
    conn.commit()


def _since(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def recent_results(conn, hours):
    """All results from the last `hours` hours, oldest first, as plain dicts."""
    rows = conn.execute(
        "SELECT * FROM results WHERE ts >= ? ORDER BY ts", (_since(hours),)
    ).fetchall()
    return [dict(row) for row in rows]


def recent_alerts(conn, limit=50):
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
