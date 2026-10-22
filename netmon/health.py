"""
health.py - The monitor checking on ITSELF, so it can tell you what it needs.

Two problems a monitor can't report through normal alerts:

1. "I've stopped." If monitor.py crashes or the computer sleeps, no alert
   fires - data just stops arriving. The dashboard calls `monitor_status()`
   and shows a warning when the newest result is older than a few intervals.

2. "One of my targets is misconfigured." Some sites block ping entirely, so
   they show 100% loss forever even though they work fine. If a target has
   NEVER answered a ping in the last hour, while other targets did, that's
   almost certainly a config problem, not an outage. We warn about it and
   leave it out of the diagnosis so it doesn't cause a permanent false alarm.
"""

from datetime import datetime, timezone

from netmon import storage


def unresponsive_targets(conn, config):
    """Return {target_name: advice} for targets that look misconfigured."""
    cfg = config["health"]
    rows = storage.target_summary(conn, cfg["unresponsive_window_hours"])

    # Only judge if SOME target got replies - if everything failed, that's an
    # outage (the diagnosis handles it), not a config problem.
    anything_answered = any(r["lost"] < r["n"] for r in rows)
    if not anything_answered:
        return {}

    flagged = {}
    for r in rows:
        never_answered = r["n"] >= cfg["unresponsive_min_samples"] and r["lost"] == r["n"]
        # A DNS failure also shows 100% loss - that's a real problem, not "blocks ping".
        if never_answered and r["dns_failures"] == 0:
            flagged[r["target"]] = (
                f"{r['target']} hasn't answered a single ping in the last "
                f"{cfg['unresponsive_window_hours']} h while other targets have. It probably "
                f"blocks ping (ICMP) - replace or remove it in config.json."
            )
    return flagged


def monitor_status(conn, config):
    """Everything the dashboard needs to show the monitor's own health."""
    last_ts = storage.latest_result_ts(conn)
    seconds_since = None
    stale = True  # no data at all counts as "not running"
    if last_ts:
        seconds_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_ts)).total_seconds()
        stale = seconds_since > config["interval_seconds"] * config["health"]["stale_after_cycles"]

    warnings = []
    if stale:
        warnings.append("No new data recently - monitor.py may have stopped or the computer "
                        "may be asleep. Restart it with: python3 monitor.py")
    warnings.extend(unresponsive_targets(conn, config).values())

    return {
        "last_result_ts": last_ts,
        "seconds_since_last": seconds_since,
        "stale": stale,
        "warnings": warnings,
        "diagnosis": storage.latest_diagnosis(conn),
    }
