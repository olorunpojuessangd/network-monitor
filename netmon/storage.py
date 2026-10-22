"""
storage.py - Saves and reads results using SQLite.

SQLite is a full SQL database stored in a single file (data/netmon.db).
It ships with Python, needs no server, and you can open the file with any
SQLite tool (e.g. `sqlite3 data/netmon.db`) to run your own queries.

Three tables:
  results   - one row per target per check cycle (every 60 s)
  alerts    - one row each time a target becomes DEGRADED or RECOVERED, or a
              network-wide INCIDENT opens or is RESOLVED
  diagnoses - one row per cycle: which layer (if any) has a problem
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
-- speeds up "history for one target" queries used by the baseline
CREATE INDEX IF NOT EXISTS idx_results_target_ts ON results(target, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    target  TEXT NOT NULL,
    kind    TEXT NOT NULL,              -- DEGRADED / RECOVERED / INCIDENT / RESOLVED
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    layer   TEXT NOT NULL,              -- OK, LAN, ISP, DNS, SITE or PARTIAL
    summary TEXT NOT NULL               -- plain-English explanation
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


def save_diagnosis(conn, ts, layer, summary):
    conn.execute(
        "INSERT INTO diagnoses (ts, layer, summary) VALUES (?, ?, ?)",
        (ts, layer, summary),
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


def latency_history(conn, target, hours):
    """Latency values (ms) for one target over the last `hours` hours.
    Rows with no replies (NULL latency) are skipped."""
    rows = conn.execute(
        "SELECT latency_ms FROM results WHERE target = ? AND ts >= ? AND latency_ms IS NOT NULL",
        (target, _since(hours)),
    ).fetchall()
    return [row[0] for row in rows]


def target_summary(conn, hours):
    """Per target over the last `hours` hours: how many results, how many had
    100% loss, and how many were DNS failures. Used by health.py."""
    rows = conn.execute(
        """SELECT target,
                  COUNT(*)                                     AS n,
                  SUM(packet_loss_pct >= 100)                  AS lost,
                  SUM(COALESCE(reason, '') LIKE '%DNS lookup failed%') AS dns_failures
           FROM results WHERE ts >= ? GROUP BY target""",
        (_since(hours),),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_result_ts(conn):
    """Time of the newest result, or None if the database is empty."""
    return conn.execute("SELECT MAX(ts) FROM results").fetchone()[0]


def latest_diagnosis(conn):
    row = conn.execute("SELECT ts, layer, summary FROM diagnoses ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
