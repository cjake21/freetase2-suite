#!/usr/bin/env python3
"""
physics: a power-flow co-simulation backend for the TASE.2 points.

In simulation mode the points trace sine waves, and in scenario mode they follow a
script. This backend goes a step further: it puts a small but real model of the
grid behind the points. Each tick it solves a DC power flow over a network of
buses and lines, maps the resulting line flows and bus quantities onto the ICCP
points, and watches for operator (or attacker) breaker commands. When a breaker
opens, the flow does not just blink off on one point: the power redistributes
across the rest of the network, and if that pushes another line past its limit,
that line trips too, and the failure can cascade exactly the way it does on a real
grid.

Why this matters. It turns a demo from "a number changed" into "I opened one
breaker and watched an overload ripple across three substations and black out a
bus." That is the difference between a screen full of values and a believable grid,
and it is what makes training and attack demos land. An injected false reading or
an unauthorized command now has physically consistent consequences.

How it fits. Like scenario mode, the server runs with simulation off and there is
no ingestion gateway. This engine is the value source: it solves the model and
writes every point over real ICCP, and it reads the control objects so a breaker
operate (from the HMI or from an attack) feeds straight back into the model. It
drives one ICCP agent over the same stdio line protocol as the bridge and the
gateway, so it needs no new protocol code.

The solver is a compact DC power flow (the standard linear approximation: real
power, line reactance, voltage angles) with cascading overload tripping, written in
plain Python with no numerical libraries, so the suite stays standard library only.
Voltage magnitudes are a documented approximation, since DC power flow models real
power and angles, not voltage. Python 3.7+.
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
AGENT_BIN = os.path.join(ROOT, "src", "tase2_hmi_agent")
DEFAULT_CONFIG = os.path.join(ROOT, "config", "scada.json")
DEFAULT_GRID = os.path.join(ROOT, "config", "grid.json")

HEARTBEAT_SEC = 2.0          # how often we re-solve and republish
QUALITY = {"valid": 0, "suspect": 4, "held": 8, "notvalid": 12}


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Linear algebra (small dense solve, no numpy)
# --------------------------------------------------------------------------- #

def solve_linear(A, b):
    """Solve A x = b for a small dense system by Gauss-Jordan elimination with
    partial pivoting. Raises ValueError if the system is singular."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("singular system")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col] / pv
            if f != 0.0:
                for c in range(col, n + 1):
                    M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


# --------------------------------------------------------------------------- #
# Grid model + DC power flow
# --------------------------------------------------------------------------- #

class Line:
    __slots__ = ("id", "frm", "to", "x", "limit", "in_service", "flow", "breaker")

    def __init__(self, d):
        self.id = d["id"]
        self.frm = d["from"]
        self.to = d["to"]
        self.x = float(d.get("x", 0.1))
        self.limit = float(d.get("limit_mw", 9999))
        self.in_service = True
        self.flow = 0.0
        self.breaker = None          # point name of the breaker that owns this line


class Grid:
    def __init__(self, cfg):
        self.base = float(cfg.get("base_mva", 100))
        self.overload_factor = float(cfg.get("overload_factor", 1.0))
        self.bus_ids = [b["id"] for b in cfg.get("buses", [])]
        slacks = [b["id"] for b in cfg.get("buses", []) if b.get("slack")]
        self.slack = slacks[0] if slacks else (self.bus_ids[0] if self.bus_ids else None)
        self.gen = {}
        self.load = {}
        for g in cfg.get("generators", []):
            self.gen[g["bus"]] = self.gen.get(g["bus"], 0.0) + float(g.get("mw", 0))
        for ld in cfg.get("loads", []):
            self.load[ld["bus"]] = self.load.get(ld["bus"], 0.0) + float(ld.get("mw", 0))
        self.lines = [Line(d) for d in cfg.get("lines", [])]
        self.line_by_id = {ln.id: ln for ln in self.lines}
        self.breakers = cfg.get("breakers", [])
        for br in self.breakers:
            ln = self.line_by_id.get(br["line"])
            if ln is not None:
                ln.breaker = br["point"]
        self.measurements = cfg.get("measurements", [])
        self.nominals = cfg.get("nominals", {})
        self.angles = {b: 0.0 for b in self.bus_ids}
        # dynamics the DC solve does not give directly
        self.nominal_hz = float(cfg.get("nominal_hz", 60.0))
        self.freq = self.nominal_hz          # system frequency (approximation)
        self.accum = {}                      # point name -> integrated MWh

    # ---- topology + solve ------------------------------------------------- #

    def _slack_component(self):
        """Buses reachable from the slack over in-service lines. Anything outside
        is an island with no source, i.e. blacked out."""
        if self.slack is None:
            return set()
        adj = {b: [] for b in self.bus_ids}
        for ln in self.lines:
            if ln.in_service:
                adj[ln.frm].append(ln.to)
                adj[ln.to].append(ln.frm)
        seen = {self.slack}
        stack = [self.slack]
        while stack:
            b = stack.pop()
            for nb in adj[b]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return seen

    def _injection_pu(self, bus):
        return (self.gen.get(bus, 0.0) - self.load.get(bus, 0.0)) / self.base

    def solve_dc(self):
        """Solve voltage angles and line flows for the current topology. Buses not
        connected to the slack are left as None (blacked out)."""
        comp = self._slack_component()
        self.angles = {b: None for b in self.bus_ids}
        nonslack = [b for b in self.bus_ids if b in comp and b != self.slack]
        if nonslack:
            idx = {b: i for i, b in enumerate(nonslack)}
            n = len(nonslack)
            B = [[0.0] * n for _ in range(n)]
            P = [self._injection_pu(b) for b in nonslack]
            for ln in self.lines:
                if not ln.in_service or ln.frm not in comp or ln.to not in comp:
                    continue
                b = 1.0 / ln.x
                fi, ti = ln.frm, ln.to
                if fi in idx and ti in idx:
                    B[idx[fi]][idx[fi]] += b
                    B[idx[ti]][idx[ti]] += b
                    B[idx[fi]][idx[ti]] -= b
                    B[idx[ti]][idx[fi]] -= b
                elif fi in idx:                 # ti is the slack (angle 0)
                    B[idx[fi]][idx[fi]] += b
                elif ti in idx:                 # fi is the slack
                    B[idx[ti]][idx[ti]] += b
            theta = solve_linear(B, P)
            for b in nonslack:
                self.angles[b] = theta[idx[b]]
        if self.slack in comp:
            self.angles[self.slack] = 0.0

        for ln in self.lines:
            a_f, a_t = self.angles.get(ln.frm), self.angles.get(ln.to)
            if ln.in_service and a_f is not None and a_t is not None:
                ln.flow = (a_f - a_t) / ln.x * self.base
            else:
                ln.flow = 0.0

    def overloaded_lines(self):
        return [ln for ln in self.lines if ln.in_service
                and abs(ln.flow) > ln.limit * self.overload_factor]

    def trip_worst(self):
        """Trip the single most overloaded line, if any, and return the trip event
        (or None). Tripping one line per call lets a cascade ripple across ticks so
        you can watch it spread on the HMI rather than collapse all at once."""
        over = self.overloaded_lines()
        if not over:
            return None
        ln = max(over, key=lambda l: abs(l.flow) / l.limit if l.limit else 0)
        ln.in_service = False
        return {"line": ln.id, "flow": round(abs(ln.flow), 1),
                "limit": ln.limit, "breaker": ln.breaker}

    def settle(self):
        """Solve and trip overloads repeatedly until stable (used offline and in
        tests). Returns every trip event. The live runner uses trip_worst once per
        tick instead, so a cascade unfolds over time."""
        events = []
        for _ in range(len(self.lines) + 2):
            self.solve_dc()
            ev = self.trip_worst()
            if ev is None:
                break
            events.append(ev)
        return events

    # ---- control ---------------------------------------------------------- #

    def set_breaker(self, point, closed):
        """Open or close the line a breaker owns. Returns True if the topology
        actually changed."""
        for ln in self.lines:
            if ln.breaker == point:
                if ln.in_service != closed:
                    ln.in_service = closed
                    return True
        return False

    def energized(self, bus):
        return self.angles.get(bus) is not None

    # ---- measurement mapping ---------------------------------------------- #

    def _vmag(self, bus, nom):
        """Approximate bus voltage magnitude in kV from the solved angle. DC power
        flow does not model voltage, so this is a documented approximation: a small
        sag proportional to the angle, clamped to a believable band; zero on a dead
        (de-energised) bus."""
        if not self.energized(bus):
            return 0.0
        sag = 1.0 - 0.10 * abs(self.angles[bus])
        return nom * max(0.9, min(1.05, sag))

    def _gen_output(self, bus):
        """Real-power output of a generator. A non-slack unit produces its scheduled
        MW; the slack unit produces whatever balances served load less the other
        in-service units (it absorbs imbalance and losses)."""
        comp = self._slack_component()
        if bus == self.slack:
            served = sum(v for b, v in self.load.items() if b in comp)
            other = sum(v for b, v in self.gen.items() if b != self.slack and b in comp)
            return max(0.0, served - other)
        return self.gen.get(bus, 0.0) if bus in comp else 0.0

    def update_dynamics(self, dt_sec):
        """Advance the per-tick dynamics the DC solve does not give directly: system
        frequency (a documented approximation that sags with unserved load and grid
        stress) and energy accumulators (MWh integrated from line flow). Call once
        per tick, after solve_dc()."""
        comp = self._slack_component()
        total = sum(self.load.values()) or 1.0
        served = sum(v for b, v in self.load.items() if b in comp)
        unserved_frac = max(0.0, (total - served) / total)
        stress = len(self.overloaded_lines())
        dev = -1.5 * unserved_frac - 0.02 * stress + random.uniform(-0.008, 0.008)
        self.freq = round(max(58.5, min(60.1, self.nominal_hz + dev)), 3)
        dt_h = dt_sec / 3600.0
        for spec in self.measurements:
            if spec.get("type") == "accumulator":
                ln = self.line_by_id.get(spec.get("line"))
                if ln is not None and ln.in_service:
                    self.accum[spec["point"]] = round(
                        self.accum.get(spec["point"], 0.0) + abs(ln.flow) * dt_h, 3)

    def measure(self, spec):
        """Compute one point's value (and quality) from the current solution."""
        kind = spec["type"]
        valid = "valid"
        if kind == "line_flow":
            return round(self.line_by_id[spec["line"]].flow, 2), valid
        if kind == "line_mvar":
            # DC power flow does not model reactive power; approximate it from the
            # real flow at a representative power factor (documented approximation).
            ln = self.line_by_id[spec["line"]]
            pf = max(0.5, min(0.999, float(spec.get("pf", 0.95))))
            return round(ln.flow * math.tan(math.acos(pf)), 2), valid
        if kind == "line_loading":
            ln = self.line_by_id[spec["line"]]
            pct = 100.0 * abs(ln.flow) / ln.limit if ln.limit else 0.0
            return round(pct, 1), valid
        if kind == "bus_vmag":
            bus = spec["bus"]
            nom = float(spec.get("nominal_kv", 138.0))
            return round(self._vmag(bus, nom), 2), (valid if self.energized(bus) else "notvalid")
        if kind == "gen_mw":
            bus = spec["bus"]
            return round(self._gen_output(bus), 2), (valid if bus in self._slack_component() else "notvalid")
        if kind == "gen_mvar":
            bus = spec["bus"]
            pf = max(0.5, min(0.999, float(spec.get("pf", 0.95))))
            return round(self._gen_output(bus) * math.tan(math.acos(pf)), 2), valid
        if kind == "frequency":
            return self.freq, valid
        if kind == "tap":
            # transformer tap regulates the served-side voltage; derive an integer
            # tap step from the voltage error against nominal.
            bus = spec["bus"]
            if not self.energized(bus):
                return 0, "notvalid"
            nom = float(spec.get("nominal_kv", 138.0))
            step = float(spec.get("step_kv", 1.5))
            lo, hi = int(spec.get("min", -16)), int(spec.get("max", 16))
            tap = int(round((nom - self._vmag(bus, nom)) / step)) if step else 0
            return max(lo, min(hi, tap)), valid
        if kind == "accumulator":
            return round(self.accum.get(spec["point"], 0.0), 3), valid
        if kind == "ace":
            # Area Control Error: tie interchange error plus frequency-bias term.
            net_actual = net_sched = 0.0
            for t in spec.get("ties", []):
                ln = self.line_by_id.get(t.get("line"))
                if ln is not None:
                    net_actual += ln.flow
                net_sched += float(t.get("sched", 0.0))
            bias = float(spec.get("bias_mw_per_hz", 50.0))
            ace = (net_actual - net_sched) - 10.0 * bias * (self.freq - self.nominal_hz)
            return round(ace, 2), valid
        if kind == "thermal":
            ln = self.line_by_id[spec["line"]]
            amb = float(spec.get("ambient_c", 50.0))
            gain = float(spec.get("gain_c", 45.0))
            pct = abs(ln.flow) / ln.limit if ln.limit else 0.0
            return round(amb + gain * pct, 1), valid
        if kind == "thermal_state":
            ln = self.line_by_id[spec["line"]]
            pct = 100.0 * abs(ln.flow) / ln.limit if ln.limit else 0.0
            return (1 if pct > float(spec.get("threshold_pct", 60.0)) else 0), valid
        raise ValueError("unknown measurement type %r" % kind)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate(grid_cfg, point_names):
    errors = []
    bus_ids = {b.get("id") for b in grid_cfg.get("buses", [])}
    if not bus_ids:
        errors.append("grid has no buses")
    slacks = [b for b in grid_cfg.get("buses", []) if b.get("slack")]
    if len(slacks) != 1:
        errors.append("grid needs exactly one slack bus (found %d)" % len(slacks))
    line_ids = set()
    for ln in grid_cfg.get("lines", []):
        line_ids.add(ln.get("id"))
        for end in ("from", "to"):
            if ln.get(end) not in bus_ids:
                errors.append("line %r %s bus %r is not a bus" % (ln.get("id"), end, ln.get(end)))
    for g in grid_cfg.get("generators", []) + grid_cfg.get("loads", []):
        if g.get("bus") not in bus_ids:
            errors.append("injection on unknown bus %r" % g.get("bus"))
    for br in grid_cfg.get("breakers", []):
        if br.get("line") not in line_ids:
            errors.append("breaker %r names unknown line %r" % (br.get("point"), br.get("line")))
        if point_names is not None and br.get("point") not in point_names:
            errors.append("breaker point %r is not in the point model" % br.get("point"))
    for m in grid_cfg.get("measurements", []):
        if point_names is not None and m.get("point") not in point_names:
            errors.append("measurement point %r is not in the point model" % m.get("point"))
        if m.get("line") and m["line"] not in line_ids:
            errors.append("measurement %r names unknown line %r" % (m.get("point"), m["line"]))
        if m.get("bus") and m["bus"] not in bus_ids:
            errors.append("measurement %r names unknown bus %r" % (m.get("point"), m["bus"]))
    return errors


# --------------------------------------------------------------------------- #
# ICCP agent (write points, read control objects)
# --------------------------------------------------------------------------- #

class Agent:
    def __init__(self, host, port, domain):
        if not os.path.isfile(AGENT_BIN):
            sys.exit("[physics] build first: ./scripts/10_build.sh")
        self.host, self.port, self.domain = host, str(port), domain
        self._lock = threading.Lock()
        self._reads = {}
        self.proc = None
        self.online = False
        self._spawn()

    def _spawn(self):
        self.online = False
        self.proc = subprocess.Popen(
            [AGENT_BIN, self.host, self.port, self.domain],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ev") == "online":
                self.online = True
            elif ev.get("ev") == "read":
                self._reads[ev.get("item")] = ev.get("value")
            elif ev.get("ev") == "error":
                log("[physics] agent: %s" % ev.get("msg", "error"))

    def wait_online(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.online:
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.1)
        return self.online

    def ensure_online(self, timeout=8.0, retries=5, backoff=1.0):
        """Come online, retrying a transient association rejection by respawning the
        agent. A new association can be refused when the server is mid-handshake with
        the HMI's clients (the same intermittent connection-rejected seen elsewhere);
        a respawn a moment later succeeds, so the co-simulation should not give up on
        the first refusal."""
        for attempt in range(1, retries + 1):
            if self.wait_online(timeout):
                return True
            if attempt < retries:
                log("[physics] ICCP association not online (attempt %d/%d); retrying"
                    % (attempt, retries))
                try:
                    self.proc.terminate()
                except Exception:
                    pass
                time.sleep(backoff)
                self._spawn()
        return self.online

    def _send(self, cmd):
        with self._lock:
            if self.proc.poll() is not None:
                raise IOError("ICCP agent exited (rc=%s)" % self.proc.returncode)
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()

    def write_q(self, point, value, is_float, quality, ts):
        v = repr(float(value)) if is_float else str(int(round(value)))
        self._send("WRITEQ %s %d %s %d %d" % (point, 1 if is_float else 0, v,
                                              int(quality), int(ts)))

    def write_batch(self, items, chunk=30):
        """Write a tick of points in batched MMS requests. items is a list of
        (point, value, is_float, quality, ts). A single MMS write tops out near a
        hundred variables, and each point is three (Value, Flags, TimeStamp), so the
        batch is split into chunks of <chunk> points. Even chunked this is a handful
        of round-trips per tick instead of one per point, which is what keeps a
        100+ point co-simulation from starving on the single-threaded server."""
        for i in range(0, len(items), chunk):
            parts = ["WRITEB"]
            for point, value, is_float, quality, ts in items[i:i + chunk]:
                v = repr(float(value)) if is_float else str(int(round(value)))
                parts.append("%s %d %s %d %d" % (point, 1 if is_float else 0, v,
                                                 int(quality), int(ts)))
            self._send(" ".join(parts))

    def request_read(self, item):
        self._send("READ " + item)

    def last_read(self, item):
        return self._reads.get(item)

    def select(self, point):
        self._send("SELECT %s_ctl" % point)

    def operate(self, point, command, tag="physics"):
        self._send("OPERATE %s_ctl %d %s" % (point, int(command), tag))

    def stop(self):
        try:
            self._send("QUIT")
        except IOError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


# --------------------------------------------------------------------------- #
# The co-simulation runner
# --------------------------------------------------------------------------- #

class Runner:
    def __init__(self, grid, model_types, agent, period=HEARTBEAT_SEC):
        self.grid = grid
        self.is_float = model_types          # point name -> True if real/float
        self.agent = agent
        self.period = period
        self._stop = threading.Event()
        self._ctl_primed = {}                # breaker point -> last seen command

    def _sync_breakers(self):
        """Set each breaker's control object to match the line's actual state at
        startup, and prime the baseline from that. The server initialises a control
        Command to 0, which also happens to be the 'open' command, so without this a
        first operator 'open' would match the baseline and do nothing. Syncing makes
        the baseline reflect reality (a closed breaker reads closed), so the first
        real operator command is detected. Select then operate works for both direct
        and select-before-operate controls."""
        for br in self.grid.breakers:
            point = br["point"]
            ln = self.grid.line_by_id.get(br["line"])
            state = 1 if (ln and ln.in_service) else 0
            try:
                self.agent.select(point)
                self.agent.operate(point, state, tag="sync")
            except IOError:
                return
            self._ctl_primed[point] = state

    def _service_breakers(self):
        """Read each breaker's control object and, if the operator (or an attacker)
        changed it, switch the line. The first command seen is the baseline, so we
        do not trip the grid on startup."""
        changed = False
        for br in self.grid.breakers:
            point = br["point"]
            obj = point + "_ctl"
            cmd = self.agent.last_read(obj)
            self.agent.request_read(obj)
            if cmd is None:
                continue
            if point not in self._ctl_primed:
                self._ctl_primed[point] = cmd
                continue
            if cmd != self._ctl_primed[point]:
                closed = bool(int(round(cmd)))
                if self.grid.set_breaker(point, closed):
                    log("[physics] breaker %s -> %s" % (point, "CLOSED" if closed else "OPEN"))
                    changed = True
                self._ctl_primed[point] = cmd
        return changed

    def _publish(self):
        ts = int(time.time())
        batch = []
        # analog and thermal measurements
        for spec in self.grid.measurements:
            point = spec["point"]
            value, qual = self.grid.measure(spec)
            batch.append((point, value, self.is_float.get(point, True), QUALITY[qual], ts))
        # breaker state points reflect line in-service
        for br in self.grid.breakers:
            ln = self.grid.line_by_id.get(br["line"])
            state = 1 if (ln and ln.in_service) else 0
            batch.append((br["point"], state, False, QUALITY["valid"], ts))
        # nominals (points the grid does not model, kept fresh and online)
        for point, val in self.grid.nominals.items():
            batch.append((point, val, self.is_float.get(point, True), QUALITY["valid"], ts))
        # one batched MMS write per tick instead of one round-trip per point
        self.agent.write_batch(batch)

    def run(self):
        if not self.agent.ensure_online(timeout=8, retries=5):
            log("[physics] ICCP association did not come online after retries; aborting")
            return 1
        self.grid.solve_dc()
        self.grid.update_dynamics(self.period)
        self._sync_breakers()
        log("[physics] co-simulation online: %d bus(es), %d line(s); solving every %.1fs"
            % (len(self.grid.bus_ids), len(self.grid.lines), self.period))
        self._report_flows()
        try:
            while not self._stop.is_set():
                changed = self._service_breakers()
                self.grid.solve_dc()
                event = self.grid.trip_worst()      # at most one trip per tick
                if event is not None:
                    log("[physics] OVERLOAD trip: line %s at %.1f MW over %.0f MW limit%s"
                        % (event["line"], event["flow"], event["limit"],
                           (" (breaker %s)" % event["breaker"]) if event["breaker"] else ""))
                    self.grid.solve_dc()            # refresh flows after the trip
                if changed or event is not None:
                    self._report_flows()
                self.grid.update_dynamics(self.period)
                self._publish()
                self._stop.wait(self.period)
        except KeyboardInterrupt:
            log("\n[physics] stopping")
        finally:
            self._stop.set()
        return 0

    def _report_flows(self):
        parts = []
        for ln in self.grid.lines:
            tag = "" if ln.in_service else " [OUT]"
            parts.append("%s=%.0fMW%s" % (ln.id, ln.flow, tag))
        log("[physics] flows: " + "  ".join(parts))

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------- #
# Point model (for validation + float/int typing)
# --------------------------------------------------------------------------- #

def load_point_types(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
    types = {}
    for st in cfg.get("stations", []):
        for p in st.get("points", []):
            types[p["name"]] = p.get("type", "real") == "real"
    return types


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_validate(args):
    with open(args.grid) as f:
        grid_cfg = json.load(f)
    point_names = set(load_point_types(args.config)) if os.path.isfile(args.config) else None
    errors = validate(grid_cfg, point_names)
    for e in errors:
        print("[ERROR] " + e)
    if errors:
        print("grid INVALID: %d error(s)" % len(errors))
        return 1
    print("grid OK: %d bus(es), %d line(s), %d breaker(s)"
          % (len(grid_cfg.get("buses", [])), len(grid_cfg.get("lines", [])),
             len(grid_cfg.get("breakers", []))))
    return 0


def cmd_run(args):
    with open(args.grid) as f:
        grid_cfg = json.load(f)
    types = load_point_types(args.config)
    errors = validate(grid_cfg, set(types))
    if errors:
        for e in errors:
            print("[ERROR] " + e)
        sys.exit("grid INVALID; fix the errors above before running")
    grid = Grid(grid_cfg)
    agent = Agent(args.server_host, args.server_port, args.domain)
    try:
        rc = Runner(grid, types, agent, args.period).run()
    finally:
        agent.stop()
    return rc


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite power-flow co-simulation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a grid model against the point model")
    v.add_argument("--grid", default=DEFAULT_GRID)
    v.add_argument("--config", default=os.environ.get("SCADA_CONFIG", DEFAULT_CONFIG))
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("run", help="run the co-simulation against a running server")
    r.add_argument("--grid", default=DEFAULT_GRID)
    r.add_argument("--config", default=os.environ.get("SCADA_CONFIG", DEFAULT_CONFIG))
    r.add_argument("--server-host", default=os.environ.get("TASE2_HOST", "127.0.0.1"))
    r.add_argument("--server-port", type=int,
                   default=int(os.environ.get("TASE2_PORT", "102")))
    r.add_argument("--domain", default=os.environ.get("TASE2_DOMAIN", "TestDomain"))
    r.add_argument("--period", type=float, default=HEARTBEAT_SEC)
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
