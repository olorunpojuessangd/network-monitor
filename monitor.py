"""
monitor.py - The monitoring service. Run it with:  python3 monitor.py

Optional time limit, so a trial run ends by itself:
    python3 monitor.py --until 2026-10-29T09:00
After that date/time (your local time) it notifies you and exits.
scripts/start_service.sh uses this to run it for one week.

Every `interval_seconds` (default 60) it:
  1. checks every target at the same time (in parallel threads, so one
     slow target doesn't delay the rest),
  2. decides whether each result is degraded, comparing against fixed
     thresholds AND the target's learned normal latency,
  3. saves each result to SQLite and logs per-target alerts on changes,
  4. diagnoses which layer of the network has the problem (if any), and
     sends a desktop notification when that diagnosis changes,
  5. warns about its own config problems (targets that never answer ping).

Stop it with Ctrl+C.
"""

import argparse
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from netmon import storage
from netmon.alerts import AlertTracker, IncidentTracker, evaluate, setup_alert_logger
from netmon.baseline import latency_baseline
from netmon.checks import check_target
from netmon.config import load_config, resolve_path
from netmon.diagnosis import diagnose
from netmon.health import unresponsive_targets
from netmon.notify import notify


def run_cycle(config, conn, tracker, incidents, known_unresponsive):
    """Check all targets once, record the results, and diagnose the network."""
    targets = config["targets"]
    ts = storage.now_utc()

    # Run all checks at once; pool.map returns results in the same order as `targets`.
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda t: check_target(t, config["ping_count"]), targets))

    entries = []  # summary of this cycle, for the diagnosis
    for target, result in zip(targets, results):
        # Look up "normal" BEFORE saving, so the current result doesn't count toward it.
        baseline_ms = latency_baseline(conn, target["name"], config["baseline"])
        degraded, reason = evaluate(result, target, config["thresholds"],
                                    baseline_ms, config["baseline"])
        storage.save_result(conn, ts, result, degraded, reason)

        change = tracker.update(target["name"], degraded, reason)
        if change:
            storage.save_alert(conn, ts, target["name"], *change)

        entries.append({"name": target["name"], "role": target["role"], "degraded": degraded,
                        "reason": reason, "dns_failed": result["dns_failed"]})

        # One status line per target so you can watch it work.
        latency = f"{result['latency_ms']:.1f} ms" if result["latency_ms"] is not None else "--"
        dns = f"{result['dns_ms']:.0f} ms" if result["dns_ms"] is not None else "--"
        normal = f"{baseline_ms:.0f} ms" if baseline_ms is not None else "learning"
        status = "DEGRADED" if degraded else "ok"
        print(f"  {target['name']:<16} latency {latency:>9} (normal {normal:>8})"
              f"  loss {result['packet_loss_pct']:>5.1f}%  dns {dns:>6}  {status}")

    # Self-health: warn once when a target starts looking misconfigured.
    unresponsive = unresponsive_targets(conn, config)
    for name, advice in unresponsive.items():
        if name not in known_unresponsive:
            tracker.logger.warning("CONFIG " + advice)
    known_unresponsive.clear()
    known_unresponsive.update(unresponsive)

    # Diagnosis, ignoring misconfigured targets so they can't cause a permanent false alarm.
    layer, summary = diagnose(entries, ignore=unresponsive)
    storage.save_diagnosis(conn, ts, layer, summary)
    change = incidents.update(layer, summary)
    if change:
        storage.save_alert(conn, ts, "Network", *change)
    print(f"  Diagnosis: {layer} - {summary}")


def parse_args():
    parser = argparse.ArgumentParser(description="Network monitor service")
    parser.add_argument("--until", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM",
                        help="stop automatically at this local date/time")
    return parser.parse_args()


def finished(until):
    """True once the optional --until time has passed."""
    return until is not None and datetime.now() >= until


def main():
    args = parse_args()
    if finished(args.until):
        # Exit code 0 tells the background service this was a normal stop, not a crash,
        # so it won't restart us (see scripts/start_service.sh).
        print(f"Monitoring period ended at {args.until}. Nothing to do.")
        return

    config = load_config()
    conn = storage.connect(resolve_path(config["database_path"]))
    logger = setup_alert_logger(resolve_path(config["alert_log_path"]))
    tracker = AlertTracker(logger)
    incidents = IncidentTracker(logger, config["notifications"])
    known_unresponsive = set()

    print(f"Using {config['_source']}; checking {len(config['targets'])} targets "
          f"every {config['interval_seconds']} s. Press Ctrl+C to stop.")
    if args.until:
        print(f"Will stop automatically at {args.until}.")
    try:
        while not finished(args.until):
            started = time.monotonic()
            print(f"[{storage.now_utc()}] running checks...")
            run_cycle(config, conn, tracker, incidents, known_unresponsive)
            # Sleep for whatever is left of the interval so cycles stay ~60 s apart.
            elapsed = time.monotonic() - started
            time.sleep(max(0, config["interval_seconds"] - elapsed))
        logger.info("Monitoring period finished.")
        if config["notifications"]:
            notify("Network monitor: finished",
                   "Your monitoring period is over - time to review the results and fill in Findings.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
