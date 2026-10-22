"""
Unit tests for the "intelligence" features: diagnosis, learned baselines,
self-health checks and role guessing. They use an in-memory SQLite database,
so no network or files are needed. Run with:  python3 -m unittest
"""

import unittest
from datetime import datetime, timedelta, timezone

from netmon import storage
from netmon.alerts import evaluate
from netmon.baseline import is_unusual, latency_baseline
from netmon.config import apply_defaults, guess_role
from netmon.diagnosis import diagnose
from netmon.health import monitor_status, unresponsive_targets

THRESHOLDS = {"packet_loss_pct": 5, "latency_ms": 100, "dns_ms": 200}
BASELINE_CFG = {"window_hours": 24, "min_samples": 30, "multiplier": 2.0, "min_increase_ms": 10}


def entry(name, role, degraded=False, reason="", dns_failed=False):
    return {"name": name, "role": role, "degraded": degraded, "reason": reason, "dns_failed": dns_failed}


def healthy_network():
    return [entry("Router", "gateway"), entry("1.1.1.1", "internet"), entry("8.8.8.8", "internet"),
            entry("Google", "website"), entry("Cloudflare", "website"), entry("Wikipedia", "website")]


def with_problems(**problems):
    """healthy_network() with some targets degraded, e.g. with_problems(Router="loss 50%")."""
    entries = healthy_network()
    for e in entries:
        if e["name"] in problems:
            e["degraded"], e["reason"] = True, problems[e["name"]]
            e["dns_failed"] = "DNS lookup failed" in e["reason"]
    return entries


class DiagnosisTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(diagnose(healthy_network())[0], "OK")

    def test_router_down_blames_lan_even_if_everything_else_is_bad(self):
        everything = {n: "loss 100%" for n in ["Router", "1.1.1.1", "8.8.8.8", "Google"]}
        self.assertEqual(diagnose(with_problems(**everything))[0], "LAN")

    def test_public_ips_down_blames_isp(self):
        self.assertEqual(diagnose(with_problems(**{"1.1.1.1": "loss 100%", "8.8.8.8": "loss 100%"}))[0], "ISP")

    def test_dns_failures_blame_dns(self):
        layer, _ = diagnose(with_problems(Google="DNS lookup failed", Cloudflare="DNS 900 ms > 200 ms"))
        self.assertEqual(layer, "DNS")

    def test_one_site_down_blames_site(self):
        layer, summary = diagnose(with_problems(Wikipedia="loss 100%"))
        self.assertEqual(layer, "SITE")
        self.assertIn("Wikipedia", summary)

    def test_one_public_ip_bad_is_partial(self):
        self.assertEqual(diagnose(with_problems(**{"1.1.1.1": "latency 150 ms > 100 ms"}))[0], "PARTIAL")

    def test_ignored_targets_dont_count(self):
        self.assertEqual(diagnose(with_problems(Wikipedia="loss 100%"), ignore={"Wikipedia"})[0], "OK")


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.conn = storage.connect(":memory:")

    def add_results(self, target, latencies, loss=0.0, reason="", minutes_ago=5):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        for latency in latencies:
            result = {"target": target, "host": "h", "latency_ms": latency,
                      "packet_loss_pct": loss, "dns_ms": None}
            storage.save_result(self.conn, ts, result, bool(reason), reason)

    def test_needs_enough_history(self):
        self.add_results("Router", [5.0] * 10)
        self.assertIsNone(latency_baseline(self.conn, "Router", BASELINE_CFG))

    def test_median_ignores_spikes(self):
        self.add_results("Router", [5.0] * 30 + [500.0] * 5)
        self.assertEqual(latency_baseline(self.conn, "Router", BASELINE_CFG), 5.0)

    def test_unusual_needs_both_ratio_and_absolute_increase(self):
        self.assertTrue(is_unusual(18, 5, BASELINE_CFG))     # 3.6x and +13 ms
        self.assertFalse(is_unusual(8, 3, BASELINE_CFG))     # 2.7x but only +5 ms
        self.assertFalse(is_unusual(35, 20, BASELINE_CFG))   # +15 ms but only 1.75x

    def test_evaluate_uses_baseline(self):
        result = {"latency_ms": 18.0, "packet_loss_pct": 0.0, "dns_ms": None, "dns_failed": False}
        degraded, reason = evaluate(result, {"latency_ms": 20}, THRESHOLDS, 5.0, BASELINE_CFG)
        self.assertTrue(degraded)
        self.assertIn("normal (5 ms)", reason)

    def test_unresponsive_target_flagged_only_if_others_answer(self):
        config = apply_defaults({"targets": []})
        self.add_results("BlocksPing", [None] * 10, loss=100.0, reason="loss 100% > 5%")
        self.assertEqual(unresponsive_targets(self.conn, config), {})  # nothing else answered: outage
        self.add_results("Router", [5.0] * 10)
        self.assertIn("BlocksPing", unresponsive_targets(self.conn, config))

    def test_dns_failures_are_not_called_misconfigured(self):
        config = apply_defaults({"targets": []})
        self.add_results("Router", [5.0] * 10)
        self.add_results("Site", [None] * 10, loss=100.0, reason="DNS lookup failed; loss 100% > 5%")
        self.assertEqual(unresponsive_targets(self.conn, config), {})

    def test_stale_data_warns(self):
        config = apply_defaults({"targets": []})
        self.add_results("Router", [5.0], minutes_ago=10)
        status = monitor_status(self.conn, config)
        self.assertTrue(status["stale"])
        self.assertIn("monitor.py may have stopped", status["warnings"][0])


class ConfigTests(unittest.TestCase):
    def test_guess_role(self):
        self.assertEqual(guess_role("10.0.0.1"), "gateway")
        self.assertEqual(guess_role("192.168.1.1"), "gateway")
        self.assertEqual(guess_role("1.1.1.1"), "internet")
        self.assertEqual(guess_role("google.com"), "website")

    def test_old_config_gets_new_defaults(self):
        config = apply_defaults({"targets": [{"name": "R", "host": "10.0.0.1"}],
                                 "baseline": {"multiplier": 3}})
        self.assertEqual(config["baseline"]["multiplier"], 3)       # yours is kept
        self.assertEqual(config["baseline"]["min_samples"], 30)     # missing one filled in
        self.assertEqual(config["targets"][0]["role"], "gateway")


if __name__ == "__main__":
    unittest.main()
