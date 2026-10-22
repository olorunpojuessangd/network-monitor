"""
Unit tests for analyze.py: incident pairing and the time-of-day tracing.
Run with:  python3 -m unittest
"""

import unittest
from datetime import datetime

from analyze import incidents, observations, plural


def alert(ts, kind, message="[ISP] Router is fine but every public server is degraded"):
    return {"ts": ts, "target": "Network", "kind": kind, "message": message}


def hour(h, median, router, samples=60):
    return {"hour": h, "median": median, "router": router, "loss": 0.0, "samples": samples}


COVERAGE = {"start": datetime(2026, 10, 1).astimezone(), "end": datetime(2026, 10, 8).astimezone(),
            "cycles": 10000, "pct": 99.0, "gaps": []}


class IncidentTests(unittest.TestCase):
    def test_pairs_incident_with_resolution(self):
        found = incidents([alert("2026-10-22T19:00:00+00:00", "INCIDENT"),
                           alert("2026-10-22T19:30:00+00:00", "RESOLVED", "Network healthy again.")])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["minutes"], 30)
        self.assertEqual(found[0]["layer"], "ISP")

    def test_layer_change_mid_incident_is_one_incident(self):
        found = incidents([alert("2026-10-22T19:00:00+00:00", "INCIDENT", "[DNS] slow"),
                           alert("2026-10-22T19:10:00+00:00", "INCIDENT", "[ISP] down"),
                           alert("2026-10-22T19:20:00+00:00", "RESOLVED", "ok")])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["layer"], "DNS")

    def test_per_target_alerts_are_ignored(self):
        self.assertEqual(incidents([{"ts": "2026-10-22T19:00:00+00:00", "target": "Router",
                                     "kind": "DEGRADED", "message": "x"}]), [])


class TracingTests(unittest.TestCase):
    def notes(self, hours):
        return " ".join(observations(COVERAGE, [], 20.0, hours, [], []))

    def test_evening_spike_with_flat_router_is_upstream(self):
        hours = [hour(h, 20.0, 6.0) for h in range(19)] + [hour(h, 40.0, 6.0) for h in range(19, 22)]
        self.assertIn("upstream of your home network", self.notes(hours))

    def test_evening_spike_with_slow_router_is_local(self):
        hours = [hour(h, 20.0, 6.0) for h in range(19)] + [hour(h, 40.0, 15.0) for h in range(19, 22)]
        self.assertIn("starts inside your home network", self.notes(hours))

    def test_too_little_data_says_so(self):
        self.assertIn("Not enough data yet", self.notes([hour(0, 20.0, 6.0)]))

    def test_plural(self):
        self.assertEqual(plural(1, "gap"), "1 gap")
        self.assertEqual(plural(3, "gap"), "3 gaps")


if __name__ == "__main__":
    unittest.main()
