"""
baseline.py - Learns what "normal" latency looks like for each target.

Fixed thresholds miss problems on fast connections: if your router normally
answers in 5 ms, a jump to 18 ms is a real change even though it's under
a 20 ms limit. So for each target we take the MEDIAN latency over the last
24 hours (the middle value - unlike the average, a few spikes don't drag it
around) and flag results that are far above it.

"Far above" needs BOTH conditions, to avoid false alarms:
  - more than `multiplier` times normal (default 2x), and
  - at least `min_increase_ms` above normal (default 10 ms), so 2 ms -> 5 ms
    on a very fast target isn't reported.
"""

import statistics

from netmon import storage


def latency_baseline(conn, target_name, cfg):
    """Median latency (ms) for this target over the window, or None if we don't
    have enough history yet (e.g. the first ~30 minutes after first start)."""
    values = storage.latency_history(conn, target_name, cfg["window_hours"])
    if len(values) < cfg["min_samples"]:
        return None
    return statistics.median(values)


def is_unusual(latency_ms, baseline_ms, cfg):
    """True if this latency is well above the target's normal."""
    if latency_ms is None or baseline_ms is None:
        return False
    return (latency_ms > baseline_ms * cfg["multiplier"]
            and latency_ms - baseline_ms >= cfg["min_increase_ms"])
