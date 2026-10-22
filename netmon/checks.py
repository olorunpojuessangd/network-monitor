"""
checks.py - The actual network measurements.

Two checks per target:
  1. DNS resolution time - how long it takes to turn a name like
     "google.com" into an IP address. Skipped for targets that are
     already IP addresses (like your router or 8.8.8.8).
  2. Ping - we call the operating system's own `ping` command and read
     its summary lines to get average latency and packet loss %.

We use the system `ping` instead of a Python library because sending raw
ICMP packets from Python needs admin/root rights; the `ping` command
already has that permission built in.
"""

import ipaddress
import re
import socket
import subprocess
import time

# Patterns that match the summary lines printed by `ping` on macOS and Linux:
#   macOS: "10 packets transmitted, 10 packets received, 0.0% packet loss"
#   Linux: "10 packets transmitted, 10 received, 0% packet loss, time 9012ms"
LOSS_PATTERN = re.compile(r"([\d.]+)% packet loss")
#   macOS: "round-trip min/avg/max/stddev = 11.2/14.8/20.1/2.3 ms"
#   Linux: "rtt min/avg/max/mdev = 11.2/14.8/20.1/2.3 ms"
# We capture the second number, which is the average.
AVG_PATTERN = re.compile(r"= [\d.]+/([\d.]+)/")


def is_ip_address(host):
    """True if `host` is already an IP (so there is nothing for DNS to resolve)."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def measure_dns(host):
    """Return DNS lookup time in milliseconds, or None if lookup failed.
    Returns None without measuring if `host` is already an IP address."""
    if is_ip_address(host):
        return None
    start = time.perf_counter()
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None  # DNS failed - the alert logic treats this as a problem
    return (time.perf_counter() - start) * 1000


def parse_ping_output(output):
    """Pull (avg_latency_ms, packet_loss_pct) out of ping's text output.
    Latency is None if no replies came back at all."""
    loss_match = LOSS_PATTERN.search(output)
    avg_match = AVG_PATTERN.search(output)
    loss = float(loss_match.group(1)) if loss_match else 100.0
    latency = float(avg_match.group(1)) if avg_match else None
    return latency, loss


def measure_ping(host, count):
    """Ping `host` `count` times. Returns (avg_latency_ms, packet_loss_pct)."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), host],
            capture_output=True,
            text=True,
            timeout=count + 10,  # ~1 second per ping, plus slack
        )
    except subprocess.TimeoutExpired:
        return None, 100.0
    # ping prints its summary to stdout even when some packets are lost
    return parse_ping_output(result.stdout)


def check_target(target, ping_count):
    """Run every check for one target and return a dict of results."""
    host = target["host"]
    dns_ms = measure_dns(host)
    dns_failed = not is_ip_address(host) and dns_ms is None
    if dns_failed:
        # If the name can't be resolved, ping would fail too - don't bother.
        latency_ms, loss_pct = None, 100.0
    else:
        latency_ms, loss_pct = measure_ping(host, ping_count)
    return {
        "target": target["name"],
        "host": host,
        "latency_ms": latency_ms,
        "packet_loss_pct": loss_pct,
        "dns_ms": dns_ms,
        "dns_failed": dns_failed,
    }
