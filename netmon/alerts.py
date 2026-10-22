"""
alerts.py - Decides whether a result is "degraded" and logs alerts.

A result is degraded if ANY of these is true:
  - packet loss is above the loss threshold (default 5%)
  - average latency is above the latency threshold (default 100 ms;
    a target can override it, e.g. the router uses 20 ms)
  - latency is far above this target's own learned normal (see baseline.py)
  - DNS lookup failed, or took longer than the DNS threshold (default 200 ms)

To avoid filling the log with the same alert every minute, we only log
when something CHANGES state:
  - per target:  healthy -> DEGRADED, or DEGRADED -> RECOVERED
  - network-wide: the diagnosis layer changes (an INCIDENT opens, changes,
    or is RESOLVED). Only these send desktop notifications, so one outage
    produces one popup, not one per target.
This is how real monitoring tools (and help desk tickets) usually work.
"""

import logging

from netmon.baseline import is_unusual
from netmon.notify import notify


def setup_alert_logger(log_path):
    """Log alerts to both the terminal and a file (logs/alerts.log)."""
    logger = logging.getLogger("netmon.alerts")
    logger.setLevel(logging.INFO)
    if not logger.handlers:  # don't add duplicate handlers if called twice
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        for handler in (logging.FileHandler(log_path), logging.StreamHandler()):
            handler.setFormatter(fmt)
            logger.addHandler(handler)
    return logger


def evaluate(result, target, thresholds, baseline_ms=None, baseline_cfg=None):
    """Return (degraded: bool, reason: str) for one result.
    `baseline_ms` is the target's normal latency (None = not enough history yet)."""
    problems = []

    loss_limit = target.get("packet_loss_pct", thresholds["packet_loss_pct"])
    latency_limit = target.get("latency_ms", thresholds["latency_ms"])
    dns_limit = target.get("dns_ms", thresholds["dns_ms"])
    latency = result["latency_ms"]

    if result["dns_failed"]:
        problems.append("DNS lookup failed")
    elif result["dns_ms"] is not None and result["dns_ms"] > dns_limit:
        problems.append(f"DNS {result['dns_ms']:.0f} ms > {dns_limit} ms")

    if result["packet_loss_pct"] > loss_limit:
        problems.append(f"loss {result['packet_loss_pct']:.0f}% > {loss_limit}%")

    if latency is not None and latency > latency_limit:
        problems.append(f"latency {latency:.0f} ms > {latency_limit} ms")
    elif baseline_cfg and is_unusual(latency, baseline_ms, baseline_cfg):
        problems.append(f"latency {latency:.0f} ms is {latency / baseline_ms:.1f}x "
                        f"normal ({baseline_ms:.0f} ms)")

    return bool(problems), "; ".join(problems)


class AlertTracker:
    """Remembers which targets are currently degraded so we only alert on changes."""

    def __init__(self, logger):
        self.logger = logger
        self.degraded_targets = set()

    def update(self, target_name, degraded, reason):
        """Return ("DEGRADED" | "RECOVERED", message) if the state changed, else None."""
        was_degraded = target_name in self.degraded_targets

        if degraded and not was_degraded:
            self.degraded_targets.add(target_name)
            message = f"{target_name} DEGRADED: {reason}"
            self.logger.warning(message)
            return "DEGRADED", message

        if not degraded and was_degraded:
            self.degraded_targets.discard(target_name)
            message = f"{target_name} RECOVERED"
            self.logger.info(message)
            return "RECOVERED", message

        return None


class IncidentTracker:
    """Tracks the network-wide diagnosis and notifies when it changes."""

    def __init__(self, logger, notifications=True):
        self.logger = logger
        self.notifications = notifications
        self.layer = "OK"  # assume healthy at startup

    def update(self, layer, summary):
        """Return ("INCIDENT" | "RESOLVED", message) if the layer changed, else None."""
        if layer == self.layer:
            return None
        self.layer = layer

        if layer == "OK":
            kind, message = "RESOLVED", "Network healthy again."
            self.logger.info("INCIDENT RESOLVED: " + message)
        else:
            kind, message = "INCIDENT", f"[{layer}] {summary}"
            self.logger.warning("INCIDENT " + message)

        if self.notifications:
            notify(f"Network monitor: {kind.lower()}", message)
        return kind, message
