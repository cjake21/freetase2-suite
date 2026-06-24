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


class FakeAgent:
    """A recording agent so the runner logic can be tested without a live server."""
    def __init__(self, name="agent"):
        self.name = name
        self.reads = []
        self.operates = []
        self.writes = []
    def write_q(self, point, *a): self.writes.append(point)
    def operate(self, point, *a, **k): self.operates.append(point)
    def setpoint(self, *a, **k): pass
    def select(self, *a): pass
    def cancel(self, *a): pass
    def read(self, item): self.reads.append(item)
    def snapshot(self, points): self.reads.extend(points)
    def wait_online(self, *a, **k): return True
    def stop(self): pass


class TestAttackActions(unittest.TestCase):
    """The attacker association and the recon/DoS actions."""
    def _runner(self, scenario):
        model = sc.PointModel(CONFIG)
        return sc.Runner(scenario, model, FakeAgent("primary"),
                         attacker=FakeAgent("attacker"))

    def test_scan_reads_via_attacker(self):
        r = self._runner({"timeline": []})
        r.do_scan({"do": "scan", "points": ["plc1_mw", "plc2_mw"], "technique": "T0801"})
        self.assertEqual(r.attacker.reads, ["plc1_mw", "plc2_mw"])  # recon on attacker conn
        self.assertEqual(r.agent.reads, [])                         # not the telemetry conn

    def test_scan_all_reads_every_point(self):
        r = self._runner({"timeline": []})
        r.do_scan({"do": "scan", "all": True})
        self.assertEqual(set(r.attacker.reads), set(sc.PointModel(CONFIG).type))

    def test_flood_hammers_target_via_attacker(self):
        r = self._runner({"timeline": []})
        r.do_flood({"do": "flood", "target": "plc2_brk", "seconds": 0.4,
                    "rate": 50, "technique": "T0814"})
        self.assertGreater(len(r.attacker.operates), 3)             # many rapid commands
        self.assertTrue(all(p == "plc2_brk" for p in r.attacker.operates))

    def test_malicious_operate_uses_attacker(self):
        r = self._runner({"timeline": []})
        r.do_operate({"do": "operate", "point": "plc1_brk", "command": 0,
                      "label": "malicious"})
        self.assertEqual(r.attacker.operates, ["plc1_brk"])
        self.assertEqual(r.agent.operates, [])

    def test_injected_point_asserted_by_attacker(self):
        r = self._runner({"timeline": []})
        r.do_set({"do": "inject", "point": "plc1_mw", "value": 99}, malicious=True)
        # the spoof is written from the attacker connection, not the telemetry one
        self.assertIn("plc1_mw", r.attacker.writes)
        self.assertNotIn("plc1_mw", r.agent.writes)

    def test_no_attacker_routes_to_primary(self):
        model = sc.PointModel(CONFIG)
        r = sc.Runner({"timeline": []}, model, FakeAgent("primary"))
        r.do_scan({"do": "scan", "points": ["plc1_mw"]})
        self.assertEqual(r.agent.reads, ["plc1_mw"])               # single connection


class TestScenarioPhysics(unittest.TestCase):
    """The force multiplier: a scenario with a grid is backed by the power-flow
    co-simulation, so scripted attacks have physical consequences."""
    def _runner(self, timeline=None):
        model = sc.PointModel(CONFIG)
        scenario = {"name": "t", "seed": 1, "grid": "config/grid.json",
                    "timeline": timeline or []}
        return sc.Runner(scenario, model, FakeAgent())

    def test_grid_loaded(self):
        r = self._runner()
        self.assertIsNotNone(r.grid)
        self.assertIn("plc1_mw", r.grid_meas)
        self.assertEqual(r.grid_breaker_line.get("plc1_brk"), "L5")

    def test_physics_drives_points(self):
        r = self._runner()
        r._refresh_physics()
        self.assertEqual(round(r.value["plc1_mw"]), 90)     # the tie flow
        self.assertEqual(round(r.value["plc2_mw"]), 60)     # a ring line

    def test_injection_pins_over_physics(self):
        r = self._runner()
        r.do_set({"do": "inject", "point": "plc1_mw", "value": 12.3}, malicious=True)
        r._refresh_physics()                                # physics would say 90
        self.assertEqual(r.value["plc1_mw"], 12.3)          # but the spoof holds
        self.assertIn("plc1_mw", r.scripted)

    def test_operate_breaker_cascades(self):
        r = self._runner()
        r._refresh_physics()
        self.assertEqual(round(r.value["plc2_mw"]), 60)     # L1 carrying flow
        r.do_operate({"do": "operate", "point": "plc1_brk", "command": 0})
        self.assertFalse(r.grid.line_by_id["L5"].in_service)  # tie opened
        r._refresh_physics()                                # L1 overloads and trips
        self.assertEqual(r.value["plc2_brk"], 0)            # its breaker reads open
        self.assertEqual(round(r.value["plc2_mw"]), 0)      # and its flow is gone

    def test_no_grid_is_unaffected(self):
        model = sc.PointModel(CONFIG)
        r = sc.Runner({"name": "t", "timeline": []}, model, FakeAgent())
        self.assertIsNone(r.grid)
        r._refresh_physics()                                # a safe no-op


if __name__ == "__main__":
    unittest.main(verbosity=2)
