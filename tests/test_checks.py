"""
Unit tests for the parts that don't need a network: parsing ping output
and the degraded/recovered logic. Run with:  python3 -m unittest
"""

import logging
import unittest

from netmon.alerts import AlertTracker, evaluate
from netmon.checks import parse_ping_output

MACOS_OUTPUT = """
--- 1.1.1.1 ping statistics ---
10 packets transmitted, 9 packets received, 10.0% packet loss
round-trip min/avg/max/stddev = 11.2/14.8/20.1/2.3 ms
"""

LINUX_OUTPUT = """
--- 8.8.8.8 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 9012ms
rtt min/avg/max/mdev = 9.1/10.5/12.0/0.8 ms
"""

ALL_LOST_OUTPUT = """
--- 10.0.0.99 ping statistics ---
10 packets transmitted, 0 packets received, 100.0% packet loss
"""

THRESHOLDS = {"packet_loss_pct": 5, "latency_ms": 100, "dns_ms": 200}


def make_result(latency=10.0, loss=0.0, dns=20.0, dns_failed=False):
    return {"target": "T", "host": "example.com", "latency_ms": latency,
            "packet_loss_pct": loss, "dns_ms": dns, "dns_failed": dns_failed}


class ParsePingTests(unittest.TestCase):
    def test_macos(self):
        self.assertEqual(parse_ping_output(MACOS_OUTPUT), (14.8, 10.0))

    def test_linux(self):
        self.assertEqual(parse_ping_output(LINUX_OUTPUT), (10.5, 0.0))

    def test_no_replies(self):
        self.assertEqual(parse_ping_output(ALL_LOST_OUTPUT), (None, 100.0))


class EvaluateTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(evaluate(make_result(), {}, THRESHOLDS), (False, ""))

    def test_high_loss(self):
        degraded, reason = evaluate(make_result(loss=10), {}, THRESHOLDS)
        self.assertTrue(degraded)
        self.assertIn("loss", reason)

    def test_per_target_latency_override(self):
        # 30 ms is fine globally but too slow for a router with a 20 ms limit.
        self.assertFalse(evaluate(make_result(latency=30), {}, THRESHOLDS)[0])
        self.assertTrue(evaluate(make_result(latency=30), {"latency_ms": 20}, THRESHOLDS)[0])

    def test_dns_failure(self):
        degraded, reason = evaluate(make_result(dns=None, dns_failed=True, loss=100, latency=None),
                                    {}, THRESHOLDS)
        self.assertTrue(degraded)
        self.assertIn("DNS lookup failed", reason)


class AlertTrackerTests(unittest.TestCase):
    def test_alerts_only_on_state_change(self):
        tracker = AlertTracker(logging.getLogger("test"))
        self.assertIsNone(tracker.update("T", False, ""))
        self.assertEqual(tracker.update("T", True, "loss")[0], "DEGRADED")
        self.assertIsNone(tracker.update("T", True, "loss"))  # still bad: no repeat alert
        self.assertEqual(tracker.update("T", False, "")[0], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
