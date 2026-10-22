"""
monitor.py - The monitoring service. Run it with:  python3 monitor.py

Every `interval_seconds` (default 60) it:
  1. checks every target at the same time (in parallel threads, so one
     slow target doesn't delay the rest),
  2. decides whether each result is degraded,
  3. saves each result to SQLite,
  4. logs an alert if a target changed between healthy and degraded.

Stop it with Ctrl+C.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from netmon import storage
from netmon.alerts import AlertTracker, evaluate, setup_alert_logger
from netmon.checks import check_target
from netmon.config import load_config, resolve_path


def run_cycle(config, conn, tracker):
    """Check all targets once and record the results."""
    targets = config["targets"]
    ts = storage.now_utc()

    # Run all checks at once; pool.map returns results in the same order as `targets`.
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda t: check_target(t, config["ping_count"]), targets))

    for target, result in zip(targets, results):
        degraded, reason = evaluate(result, target, config["thresholds"])
        storage.save_result(conn, ts, result, degraded, reason)

        change = tracker.update(target["name"], degraded, reason)
        if change:
            kind, message = change
            storage.save_alert(conn, ts, target["name"], kind, message)

        # One status line per target so you can watch it work.
        latency = f"{result['latency_ms']:.1f} ms" if result["latency_ms"] is not None else "--"
        dns = f"{result['dns_ms']:.0f} ms" if result["dns_ms"] is not None else "--"
        status = "DEGRADED" if degraded else "ok"
        print(f"  {target['name']:<16} latency {latency:>9}  loss {result['packet_loss_pct']:>5.1f}%"
              f"  dns {dns:>6}  {status}")


def main():
    config = load_config()
    conn = storage.connect(resolve_path(config["database_path"]))
    logger = setup_alert_logger(resolve_path(config["alert_log_path"]))
    tracker = AlertTracker(logger)

    print(f"Using {config['_source']}; checking {len(config['targets'])} targets "
          f"every {config['interval_seconds']} s. Press Ctrl+C to stop.")
    try:
        while True:
            started = time.monotonic()
            print(f"[{storage.now_utc()}] running checks...")
            run_cycle(config, conn, tracker)
            # Sleep for whatever is left of the interval so cycles stay ~60 s apart.
            elapsed = time.monotonic() - started
            time.sleep(max(0, config["interval_seconds"] - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
