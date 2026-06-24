#!/usr/bin/env python3
"""Tests for the power-flow co-simulation (stdlib unittest).

Covers the linear solver, the DC power flow on a known network, breaker switching,
the cascade, islanding, the measurement mapping, and model validation. The live
ICCP path (publishing and reacting to breaker commands) is exercised by the run
script and the smoke checks, which need the built C tools.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))

import physics  # noqa: E402

GRID = os.path.join(ROOT, "config", "grid.json")
CONFIG = os.path.join(ROOT, "config", "scada.json")


def load_grid():
    with open(GRID) as f:
        return physics.Grid(json.load(f))


class TestLinearSolver(unittest.TestCase):
    def test_solves_known_system(self):
        x = physics.solve_linear([[2.0, 1.0], [1.0, 3.0]], [3.0, 5.0])
        self.assertAlmostEqual(x[0], 0.8, places=6)
        self.assertAlmostEqual(x[1], 1.4, places=6)

    def test_singular_raises(self):
        with self.assertRaises(ValueError):
            physics.solve_linear([[1.0, 1.0], [1.0, 1.0]], [1.0, 2.0])


class TestPowerFlow(unittest.TestCase):
    def test_baseline_flows(self):
        g = load_grid()
        g.solve_dc()
        f = {ln.id: round(ln.flow) for ln in g.lines}
        # the tuned demo grid: tie carries 90 MW, ring lines well within limits
        self.assertEqual(f["L5"], 90)
        self.assertEqual(f["L1"], 60)
        self.assertEqual(f["L2"], 30)
        self.assertEqual(g.overloaded_lines(), [])

    def test_flow_conservation_at_slack(self):
        g = load_grid()
        g.solve_dc()
        # total load is 210 MW; the slack (B1) injects it via L1, L4, L5
        slack_out = 0.0
        for ln in g.lines:
            if ln.frm == "B1":
                slack_out += ln.flow
            elif ln.to == "B1":
                slack_out -= ln.flow
        self.assertAlmostEqual(slack_out, 210.0, places=3)


class TestCascade(unittest.TestCase):
    def test_open_tie_cascades(self):
        g = load_grid()
        g.set_breaker("plc1_brk", False)          # open the main tie
        trips = g.settle()
        tripped = [t["line"] for t in trips]
        self.assertIn("L1", tripped)              # the parallel path overloads first
        self.assertIn("L4", tripped)              # then the cascade continues
        energized = [b for b in g.bus_ids if g.energized(b)]
        self.assertEqual(energized, ["B1"])       # load island collapses

    def test_trip_worst_is_one_at_a_time(self):
        g = load_grid()
        g.set_breaker("plc1_brk", False)
        g.solve_dc()
        first = g.trip_worst()
        self.assertEqual(first["line"], "L1")     # the most overloaded trips first
        self.assertIsNotNone(first)

    def test_stable_grid_no_trips(self):
        g = load_grid()
        g.solve_dc()
        self.assertIsNone(g.trip_worst())


class TestBreakerAndIslanding(unittest.TestCase):
    def test_set_breaker_toggles(self):
        g = load_grid()
        self.assertTrue(g.set_breaker("plc1_brk", False))   # changed
        self.assertFalse(g.set_breaker("plc1_brk", False))  # already open, no change
        self.assertTrue(g.set_breaker("plc1_brk", True))    # close again

    def test_island_is_deenergized(self):
        g = load_grid()
        # isolate B4 by opening both its lines (L3 B3-B4 and L4 B4-B1)
        for ln in g.lines:
            if ln.id in ("L3", "L4"):
                ln.in_service = False
        g.solve_dc()
        self.assertFalse(g.energized("B4"))
        self.assertTrue(g.energized("B1"))


class TestMeasurements(unittest.TestCase):
    def setUp(self):
        self.g = load_grid()
        self.g.solve_dc()

    def test_line_flow_and_loading(self):
        v, q = self.g.measure({"type": "line_flow", "line": "L5"})
        self.assertEqual(round(v), 90)
        pct, _ = self.g.measure({"type": "line_loading", "line": "L5"})
        self.assertAlmostEqual(pct, 60.0, places=1)         # 90 of 150 MW

    def test_bus_vmag_valid_then_dead(self):
        v, q = self.g.measure({"type": "bus_vmag", "bus": "B3", "nominal_kv": 138})
        self.assertEqual(q, "valid")
        self.assertTrue(120 < v <= 138 * 1.05)
        # black out B3 by opening every line into it
        for ln in self.g.lines:
            if "B3" in (ln.frm, ln.to):
                ln.in_service = False
        self.g.solve_dc()
        v2, q2 = self.g.measure({"type": "bus_vmag", "bus": "B3", "nominal_kv": 138})
        self.assertEqual(q2, "notvalid")
        self.assertEqual(v2, 0.0)

    def test_thermal(self):
        # L3 carries 30 MW of a 120 MW limit -> 25 percent -> 50 + 45*0.25
        temp, _ = self.g.measure({"type": "thermal", "line": "L3",
                                  "ambient_c": 50, "gain_c": 45})
        self.assertAlmostEqual(temp, 61.2, places=1)        # 50 + 45*0.25, rounded
        state, _ = self.g.measure({"type": "thermal_state", "line": "L3",
                                   "threshold_pct": 50})
        self.assertEqual(state, 0)                          # 25 percent is below 50


class TestValidate(unittest.TestCase):
    def base(self):
        with open(GRID) as f:
            return json.load(f)

    def test_good_grid(self):
        cfg = self.base()
        names = set()
        with open(CONFIG) as f:
            for st in json.load(f).get("stations", []):
                for p in st.get("points", []):
                    names.add(p["name"])
        self.assertEqual(physics.validate(cfg, names), [])

    def test_no_slack(self):
        cfg = self.base()
        for b in cfg["buses"]:
            b.pop("slack", None)
        self.assertTrue(any("slack" in e for e in physics.validate(cfg, None)))

    def test_breaker_unknown_line(self):
        cfg = self.base()
        cfg["breakers"][0]["line"] = "L99"
        self.assertTrue(any("unknown line" in e for e in physics.validate(cfg, None)))

    def test_measurement_point_not_in_model(self):
        cfg = self.base()
        cfg["measurements"][0]["point"] = "ghost_point"
        self.assertTrue(any("not in the point model" in e
                            for e in physics.validate(cfg, {"plc1_mw"})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
