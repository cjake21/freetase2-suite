#!/usr/bin/env python3
"""Tests for the detection scorer (stdlib unittest).

Covers timestamp parsing, reading both the Suricata eve.json and the generic
alert formats, and the grading math, including the shipped example fixtures in
detect/ so the documented quick-start stays correct.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))

import score  # noqa: E402
import dataset  # noqa: E402

DETECT = os.path.join(ROOT, "detect")
GT = os.path.join(DETECT, "example_groundtruth.jsonl")
ALERTS = os.path.join(DETECT, "example_alerts.jsonl")


class TestParseTs(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(score.parse_ts(1000.5), 1000.5)
        self.assertEqual(score.parse_ts("1000.5"), 1000.5)

    def test_iso_with_offset(self):
        a = score.parse_ts("2026-06-24T19:38:41.500000+0000")
        b = score.parse_ts("2026-06-24T19:38:42.500000+0000")
        self.assertAlmostEqual(b - a, 1.0, places=3)

    def test_iso_zulu(self):
        self.assertAlmostEqual(
            score.parse_ts("2026-06-24T19:38:41Z"),
            score.parse_ts("2026-06-24T19:38:41+0000"), places=3)


class TestLoadAlerts(unittest.TestCase):
    def test_generic(self):
        alerts = score.load_alerts(ALERTS)
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["technique"], "T0855")

    def test_suricata_eve(self):
        lines = [
            json.dumps({"event_type": "flow"}),                       # ignored
            json.dumps({"timestamp": "2026-06-24T19:38:41.5+0000",
                        "event_type": "alert",
                        "alert": {"signature": "sig", "signature_id": 7,
                                  "metadata": {"mitre_technique_id": ["T0855"]}}}),
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("\n".join(lines))
            path = f.name
        try:
            alerts = score.load_alerts(path, "suricata")
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["sid"], 7)
            self.assertEqual(alerts[0]["technique"], "T0855")
        finally:
            os.remove(path)


class TestGrade(unittest.TestCase):
    def _intervals(self):
        _, events = dataset.load_ground_truth(GT)
        return dataset.build_malicious_intervals(events, end_wall=1014.0,
                                                 pre=0.5, post=1.5)

    def test_scorecard_numbers(self):
        intervals = self._intervals()
        alerts = score.load_alerts(ALERTS)
        sc = score.grade(intervals, alerts, tol=2.0)
        self.assertEqual(sc["attacks_total"], 2)
        self.assertEqual(sc["attacks_detected"], 1)
        self.assertEqual(sc["attacks_missed"], 1)
        self.assertEqual(sc["recall"], 0.5)
        self.assertEqual(sc["true_positive_alerts"], 1)
        self.assertEqual(sc["false_positive_alerts"], 1)

    def test_per_technique(self):
        sc = score.grade(self._intervals(), score.load_alerts(ALERTS), tol=2.0)
        self.assertEqual(sc["by_technique"]["T0855"]["recall"], 1.0)   # command caught
        self.assertEqual(sc["by_technique"]["T0856"]["recall"], 0.0)   # spoof missed
        self.assertEqual(sc["by_technique"]["T0855"]["technique_match"], 1)

    def test_latency_recorded(self):
        sc = score.grade(self._intervals(), score.load_alerts(ALERTS), tol=2.0)
        caught = next(a for a in sc["attack_detail"] if a["detected"])
        self.assertIsNotNone(caught["latency"])
        self.assertGreaterEqual(caught["latency"], 0.0)

    def test_no_alerts_is_zero_recall(self):
        sc = score.grade(self._intervals(), [], tol=2.0)
        self.assertEqual(sc["recall"], 0.0)
        self.assertEqual(sc["attacks_detected"], 0)
        self.assertEqual(sc["false_positive_alerts"], 0)


class TestReport(unittest.TestCase):
    def test_markdown_renders(self):
        _, events = dataset.load_ground_truth(GT)
        intervals = dataset.build_malicious_intervals(events, 1014.0)
        sc = score.grade(intervals, score.load_alerts(ALERTS))
        md = score.markdown_report(sc, "example")
        self.assertIn("Detection scorecard", md)
        self.assertIn("T0855", md)
        self.assertIn("Coverage by technique", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
