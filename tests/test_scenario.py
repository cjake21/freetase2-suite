#!/usr/bin/env python3
"""Tests for the scenario engine's parsing and validation (stdlib unittest).

These cover the deterministic logic: loading the point model, validating a
scenario against it, and the ground-truth labelling defaults. The live ICCP path
(playing a scenario against the server) is exercised by the shell run script and
the smoke checks, which need the built C tools.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))

import scenario as sc  # noqa: E402

CONFIG = os.path.join(ROOT, "config", "scada.json")
SCEN_DIR = os.path.join(ROOT, "scenarios")


class TestPointModel(unittest.TestCase):
    def setUp(self):
        self.m = sc.PointModel(CONFIG)

    def test_types_and_stations(self):
        self.assertTrue(self.m.exists("plc1_mw"))
        self.assertTrue(self.m.is_float("plc1_mw"))        # real
        self.assertFalse(self.m.is_float("plc1_brk"))      # state
        self.assertEqual(self.m.station_of["plc1_mw"], "plc1")
        self.assertIn("plc3_temp", self.m.station_points("plc3"))

    def test_unknown_point(self):
        self.assertFalse(self.m.exists("does_not_exist"))


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.m = sc.PointModel(CONFIG)

    def good(self):
        return {"name": "t", "baseline": {"plc1_mw": 1.0},
                "timeline": [{"at": 0, "do": "inject", "point": "plc1_mw", "value": 9}]}

    def test_good_scenario_passes(self):
        self.assertEqual(sc.validate(self.good(), self.m), [])

    def test_unknown_action(self):
        s = self.good(); s["timeline"][0]["do"] = "frob"
        self.assertTrue(any("unknown action" in e for e in sc.validate(s, self.m)))

    def test_missing_field(self):
        s = self.good(); del s["timeline"][0]["value"]
        self.assertTrue(any("missing 'value'" in e for e in sc.validate(s, self.m)))

    def test_unknown_point(self):
        s = self.good(); s["timeline"][0]["point"] = "ghost"
        self.assertTrue(any("not in the point model" in e for e in sc.validate(s, self.m)))

    def test_bad_quality(self):
        s = self.good(); s["timeline"][0]["quality"] = "purple"
        self.assertTrue(any("bad quality" in e for e in sc.validate(s, self.m)))

    def test_comms_loss_needs_target(self):
        s = {"timeline": [{"at": 0, "do": "comms_loss"}]}
        self.assertTrue(any("station" in e for e in sc.validate(s, self.m)))

    def test_comms_loss_unknown_station(self):
        s = {"timeline": [{"at": 0, "do": "comms_loss", "station": "nope"}]}
        self.assertTrue(any("unknown station" in e for e in sc.validate(s, self.m)))

    def test_empty_timeline(self):
        self.assertTrue(any("no 'timeline'" in e for e in sc.validate({}, self.m)))

    def test_bad_baseline_point(self):
        s = self.good(); s["baseline"] = {"ghost": 1}
        self.assertTrue(any("baseline point" in e for e in sc.validate(s, self.m)))


class TestShippedScenarios(unittest.TestCase):
    def test_all_shipped_scenarios_valid(self):
        model = sc.PointModel(CONFIG)
        for fn in os.listdir(SCEN_DIR):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(SCEN_DIR, fn)) as f:
                s = json.load(f)
            self.assertEqual(sc.validate(s, model), [], "%s should validate" % fn)


class TestGroundTruthLabel(unittest.TestCase):
    """The recorder labels inject malicious by default and others benign, and an
    explicit label always wins."""
    def _runner(self):
        model = sc.PointModel(CONFIG)
        return sc.Runner({"name": "t", "seed": 0, "timeline": []}, model, agent=None)

    def test_default_and_override_labels(self):
        r = self._runner()
        with tempfile.TemporaryFile("w+") as out:
            r.out = out
            r.start = sc.time.time()
            r.record("inject", point="plc1_mw", value=1)            # default malicious
            r.record("set", point="plc1_mw", value=1)               # default benign
            r.record("operate", point="plc1_brk", value=0, label="malicious")
            out.seek(0)
            labels = [json.loads(line)["label"] for line in out if line.strip()]
        self.assertEqual(labels, ["malicious", "benign", "malicious"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
