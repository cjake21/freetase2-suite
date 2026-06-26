#!/usr/bin/env python3
"""
scenario: the FreeTASE2 Suite scenario engine.

A scenario is a deterministic, seeded timeline of events that the engine plays
against a running TASE.2 server. It is the value source in "scenario" mode: the
server runs with simulation off and no ingestion gateway, and this engine drives
every point, so the whole run is reproducible. Play the same scenario twice and
you get the same values, the same operator actions, and the same attacks at the
same moments.

Why this exists. A security testbed is only as useful as it is repeatable. If you
want to train a blue team, regression-test an intrusion detection system, or
produce a labelled dataset, you need to be able to run the exact same sequence of
benign operations and attacks over and over. That is what a scenario is: a single
file that captures "at second 2 the tie-line flow reads normal, at second 8 an
attacker injects a false reading, at second 12 the breaker is commanded open."

What the engine does each run:
  * seeds every point to its baseline value and keeps it fresh with a heartbeat,
    so stations read ONLINE without a gateway,
  * walks the timeline and turns each event into real TASE.2/ICCP traffic on the
    wire (value injection, operator commands, comms loss), and
  * writes a ground-truth timeline (one JSON object per line) recording exactly
    what happened and when, with a benign/malicious label and an optional
    technique tag. That file is the key to the labelled-dataset and detection-
    scoring tools that build on this engine.

It drives one src/tase2_hmi_agent subprocess over the same stdio line protocol the
HMI bridge and the gateway use, so it needs no new protocol code and stays a clean,
self-contained module. Standard library only. Python 3.7+.
"""

import argparse
import json
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

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import physics  # noqa: E402  (sibling module: the grid co-simulation, used when a
#                              scenario names a grid so scripted attacks have
#                              physically consistent consequences)

# How often the heartbeat re-asserts every live point. It must stay comfortably
# under the HMI's freshness window (STALE_SEC, 12s) so points read ONLINE between
# timeline events. Dropping a point from the heartbeat is how "comms loss" works:
# the point stops being refreshed and ages out to stale on its own.
HEARTBEAT_SEC = 4.0

# TASE.2 quality byte (IEC 60870-6-802): validity lives in bits 2-3. These match
# the values the gateway and the bridge already use.
QUALITY = {"valid": 0, "suspect": 4, "held": 8, "notvalid": 12}

# Actions whose default intent is an attack, so their ground-truth records are
# labelled malicious unless the scenario says otherwise.
MALICIOUS_BY_DEFAULT = {"inject"}


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# The point model (loaded from scada.json so the engine knows every point's type
# and which station it belongs to)
# --------------------------------------------------------------------------- #

class PointModel:
    def __init__(self, path):
        with open(path) as f:
            cfg = json.load(f)
        self.domain = cfg.get("domain", "TestDomain")
        self.type = {}              # point name -> "real" | "state"
        self.station_of = {}        # point name -> station id
        self.points_of = {}         # station id -> [point names]
        for st in cfg.get("stations", []):
            sid = st["id"]
            self.points_of.setdefault(sid, [])
            for p in st.get("points", []):
                name = p["name"]
                self.type[name] = p.get("type", "real")
                self.station_of[name] = sid
                self.points_of[sid].append(name)

    def is_float(self, name):
        return self.type.get(name, "real") == "real"

    def exists(self, name):
        return name in self.type

    def station_points(self, sid):
        return self.points_of.get(sid, [])


# --------------------------------------------------------------------------- #
# The ICCP agent driver (one tase2_hmi_agent subprocess)
# --------------------------------------------------------------------------- #

class Agent:
    """A persistent tase2_hmi_agent the engine sends line commands to. Reused from
    the same pattern the bridge and gateway use, so every scenario event becomes
    real MMS traffic on the wire."""

    def __init__(self, host, port, domain):
        if not os.path.isfile(AGENT_BIN):
            sys.exit("[scenario] build first: ./scripts/10_build.sh")
        self.proc = subprocess.Popen(
            [AGENT_BIN, host, str(port), domain],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.online = False
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ev") == "online":
                self.online = True
            elif ev.get("ev") == "error":
                log("[scenario] agent: %s" % ev.get("msg", "error"))

    def wait_online(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.online:
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.1)
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

    def operate(self, point, command, tag="scenario"):
        self._send("OPERATE %s_ctl %d %s" % (point, int(command), tag))

    def setpoint(self, point, value, tag="scenario"):
        self._send("SETPOINT %s_ctl %r %s" % (point, float(value), tag))

    def select(self, point):
        self._send("SELECT %s_ctl" % point)

    def cancel(self, point):
        self._send("CANCEL %s_ctl" % point)

    def read(self, item):
        self._send("READ %s" % item)

    def snapshot(self, points):
        """Read the Block 1 metadata (version, features, bilateral table) plus the
        given points. This is what an attacker's discovery looks like on the wire."""
        self._send("SNAPSHOT " + " ".join(points))

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
# Scenario parsing and validation
# --------------------------------------------------------------------------- #

# Every action the timeline understands, with the fields it needs. Kept here so
# validation and the docs stay honest about the vocabulary.
ACTIONS = {
    "annotate":      [],                       # just a marker in the ground truth
    "set":           ["point", "value"],       # benign sustained value change
    "inject":        ["point", "value"],       # false-data injection (held value)
    "pulse":         ["point", "value", "seconds"],   # transient value, then restore
    "ramp":          ["point", "to", "seconds"],      # glide to a value over time
    "operate":       ["point", "command"],     # Block 5 discrete operate
    "setpoint":      ["point", "value"],       # Block 5 analog setpoint
    "comms_loss":    [],                       # drop a station or point(s)
    "restore_comms": [],                       # bring them back
    "quality":       ["point", "quality"],     # force a quality flag, value unchanged
    "scan":          [],                       # reconnaissance/collection reads
    "flood":         ["target"],               # denial of service: rapid messages
    "end":           [],                       # stop the run early
}


def validate(scenario, model):
    """Return a list of human-readable errors (empty means valid)."""
    errors = []
    timeline = scenario.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        errors.append("scenario has no 'timeline'")
        timeline = []

    for name in scenario.get("baseline", {}):
        if not model.exists(name):
            errors.append("baseline point %r is not in the point model" % name)

    for i, step in enumerate(timeline):
        where = "step %d" % i
        do = step.get("do")
        if do not in ACTIONS:
            errors.append("%s: unknown action %r" % (where, do))
            continue
        if "at" not in step:
            errors.append("%s (%s): missing 'at' time" % (where, do))
        for field in ACTIONS[do]:
            if field not in step:
                errors.append("%s (%s): missing %r" % (where, do, field))
        # point/station references must resolve
        if "point" in step and not model.exists(step["point"]):
            errors.append("%s (%s): point %r is not in the point model"
                          % (where, do, step["point"]))
        if do == "scan":
            for p in step.get("points", []):
                if not model.exists(p):
                    errors.append("%s (scan): point %r is not in the point model" % (where, p))
        if do == "flood" and "target" in step and not model.exists(step["target"]):
            errors.append("%s (flood): target %r is not in the point model"
                          % (where, step["target"]))
        if step.get("quality") and step["quality"] not in QUALITY:
            errors.append("%s (%s): bad quality %r (want %s)"
                          % (where, do, step["quality"], "|".join(QUALITY)))
        if do in ("comms_loss", "restore_comms"):
            sid = step.get("station")
            if sid is not None and not model.station_points(sid):
                errors.append("%s (%s): unknown station %r" % (where, do, sid))
            if sid is None and not step.get("points"):
                errors.append("%s (%s): needs a 'station' or a 'points' list" % (where, do))

    grid_path = scenario.get("grid")
    if grid_path:
        if not os.path.isabs(grid_path):
            grid_path = os.path.join(ROOT, grid_path)
        try:
            with open(grid_path) as f:
                gcfg = json.load(f)
            for e in physics.validate(gcfg, set(model.type)):
                errors.append("grid: " + e)
        except (OSError, ValueError) as e:
            errors.append("grid: cannot load %s: %s" % (grid_path, e))
    return errors


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #

class Runner:
    def __init__(self, scenario, model, agent, out=None, attacker=None):
        self.s = scenario
        self.model = model
        self.agent = agent                      # the legitimate value source (RTU/gateway)
        # An optional second association for the attack traffic. When a scenario
        # sets "attacker": true, recon reads, false-data writes, unauthorized
        # commands, and floods come from this connection, while the steady
        # telemetry stays on the primary, so a capture shows two associations and a
        # detector has the realistic signal of a new peer behaving badly.
        self.attacker = attacker
        self.mal = attacker or agent
        self.out = out                          # ground-truth file handle or None
        self.rng = random.Random(scenario.get("seed", 0))
        self.start = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.hb = float(scenario.get("period", HEARTBEAT_SEC))
        # live point state the heartbeat re-asserts
        self.value = {n: 0.0 for n in model.type}
        self.qual = {n: 0 for n in model.type}
        self.dropped = set()                    # points the heartbeat skips (comms loss)
        self.scripted = set()                   # points a script pinned over physics
        for name, v in scenario.get("baseline", {}).items():
            self.value[name] = v

        # Optional power-flow backing: when the scenario names a grid, the physics
        # solution is the value source and scripted actions ride on top of it, so an
        # operate causes a real cascade and an injection masks the true value.
        self.grid = None
        self.grid_meas = {}
        self.grid_breaker_line = {}
        self.grid_nominals = {}
        grid_path = scenario.get("grid")
        if grid_path:
            if not os.path.isabs(grid_path):
                grid_path = os.path.join(ROOT, grid_path)
            with open(grid_path) as f:
                gcfg = json.load(f)
            self.grid = physics.Grid(gcfg)
            self.grid_meas = {m["point"]: m for m in gcfg.get("measurements", [])}
            self.grid_breaker_line = {b["point"]: b["line"] for b in gcfg.get("breakers", [])}
            self.grid_nominals = gcfg.get("nominals", {})
            self.grid.solve_dc()

    # ---- ground truth ----------------------------------------------------- #

    def record(self, do, point=None, value=None, quality="valid", label=None,
               technique=None, note=None):
        """Write one ground-truth line and a readable console line. This is the
        labelled timeline the dataset and detection-scoring tools consume."""
        if label is None:
            label = "malicious" if do in MALICIOUS_BY_DEFAULT else "benign"
        t = round(time.time() - self.start, 3) if self.start else 0.0
        rec = {"t": t, "wall": round(time.time(), 3), "do": do, "label": label}
        if point is not None:
            rec["point"] = point
            rec["station"] = self.model.station_of.get(point)
        if value is not None:
            rec["value"] = value
        rec["quality"] = quality
        if technique:
            rec["technique"] = technique
        if note:
            rec["note"] = note
        if self.out:
            self.out.write(json.dumps(rec) + "\n")
            self.out.flush()
        flag = "ATTACK" if label == "malicious" else "  --  "
        log("[%6.1fs] %s %-13s %s" % (t, flag, do,
            note or (("%s=%s" % (point, value)) if point is not None else "")))

    # ---- writing points --------------------------------------------------- #

    def _actor(self, step, default_malicious):
        """Which association issues an action. Malicious actions use the attacker
        connection when the scenario defines one; an explicit 'from' overrides."""
        want = step.get("from")
        if want == "attacker" or (want is None and default_malicious):
            return self.mal
        return self.agent

    def _assert(self, name):
        """Push one point's current value, quality, and a fresh time tag. A point a
        script has pinned (false data) is re-asserted from the attacker connection,
        so the spoof appears to come from the attacker, not the legitimate feed."""
        agent = self.mal if (self.attacker and name in self.scripted) else self.agent
        agent.write_q(name, self.value[name], self.model.is_float(name),
                      self.qual[name], int(time.time()))

    def _refresh_physics(self):
        """When the scenario has a grid, the physics solution is the value source.
        Solve, let at most one overloaded line trip per tick (so a cascade ripples
        across the screen), and refresh every grid-driven point that a scripted
        action has not pinned. Points an attacker injected stay pinned, so a spoofed
        value keeps lying while the real grid changes underneath it."""
        if self.grid is None:
            return
        self.grid.solve_dc()
        ev = self.grid.trip_worst()
        if ev is not None:
            self.grid.solve_dc()
            self.record("cascade", note="line %s tripped (%.0f MW over %.0f)"
                        % (ev["line"], ev["flow"], ev["limit"]), label="benign")
        for point, spec in self.grid_meas.items():
            if point in self.scripted or point in self.dropped:
                continue
            val, q = self.grid.measure(spec)
            self.value[point] = val
            self.qual[point] = QUALITY[q]
        for bp, line in self.grid_breaker_line.items():
            if bp in self.scripted or bp in self.dropped:
                continue
            ln = self.grid.line_by_id.get(line)
            self.value[bp] = 1 if (ln and ln.in_service) else 0
            self.qual[bp] = QUALITY["valid"]
        for point, val in self.grid_nominals.items():
            if point in self.scripted or point in self.dropped:
                continue
            self.value[point] = val
            self.qual[point] = QUALITY["valid"]

    def heartbeat(self):
        """Keep every live point fresh so the HMI shows stations ONLINE. A point
        in self.dropped is skipped, which is exactly what makes it go stale."""
        while not self._stop.is_set():
            with self._lock:
                self._refresh_physics()
                for name in self.model.type:
                    if name not in self.dropped:
                        try:
                            self._assert(name)
                        except IOError:
                            return
            self._stop.wait(self.hb)

    # ---- the actions ------------------------------------------------------ #

    def do_set(self, step, malicious=False):
        name, val = step["point"], step["value"]
        q = QUALITY[step.get("quality", "valid")]
        with self._lock:
            self.value[name] = val
            self.qual[name] = q
            self.dropped.discard(name)
            self.scripted.add(name)             # pin over physics until released
            self._assert(name)
        self.record("inject" if malicious else "set", name, val,
                    step.get("quality", "valid"), step.get("label"),
                    step.get("technique"), step.get("note"))

    def do_pulse(self, step):
        name, val, secs = step["point"], step["value"], float(step["seconds"])
        prior = self.value.get(name, 0.0)
        with self._lock:
            self.scripted.add(name)
            self.value[name] = val
            self.qual[name] = QUALITY[step.get("quality", "valid")]
            self._assert(name)
        self.record("inject", name, val, step.get("quality", "valid"),
                    step.get("label"), step.get("technique"),
                    step.get("note") or "pulse begin")
        self._sleep(secs)
        with self._lock:
            self.value[name] = prior
            self.qual[name] = QUALITY["valid"]
            self.scripted.discard(name)         # release back to physics/baseline
            self._assert(name)
        self.record("set", name, prior, "valid", "benign", note="pulse restore")

    def do_ramp(self, step):
        name = step["point"]
        target = float(step["to"])
        secs = float(step["seconds"])
        period = float(step.get("step", 1.0))
        start_val = float(self.value.get(name, 0.0))
        steps = max(1, int(round(secs / period)))
        with self._lock:
            self.scripted.add(name)             # the ramp owns this point now
        self.record("ramp", name, start_val, step.get("quality", "valid"),
                    step.get("label"), step.get("technique"),
                    step.get("note") or ("ramp to %s over %ss" % (target, secs)))
        for k in range(1, steps + 1):
            if self._stop.is_set():
                break
            frac = k / float(steps)
            with self._lock:
                self.value[name] = start_val + (target - start_val) * frac
                self._assert(name)
            self._sleep(period)
        self.record("set", name, round(self.value[name], 4), "valid",
                    step.get("label", "benign"), note="ramp end")

    def do_operate(self, step):
        name, cmd = step["point"], int(step["command"])
        sbo = bool(step.get("sbo", False))
        actor = self._actor(step, step.get("label") == "malicious")
        if sbo:
            actor.select(name)
            self._sleep(0.3)
        actor.operate(name, cmd, step.get("tag", "scenario"))
        # if the operated point is a breaker on the grid, switch the line so the
        # co-simulation redistributes flow and may cascade
        if self.grid is not None and name in self.grid_breaker_line:
            with self._lock:
                self.grid.set_breaker(name, bool(cmd))
        self.record("operate", name, cmd, "valid", step.get("label"),
                    step.get("technique"),
                    step.get("note") or ("operate %s = %d%s"
                                         % (name, cmd, " (SBO)" if sbo else "")))

    def do_setpoint(self, step):
        name, val = step["point"], float(step["value"])
        sbo = bool(step.get("sbo", False))
        actor = self._actor(step, step.get("label") == "malicious")
        if sbo:
            actor.select(name)
            self._sleep(0.3)
        actor.setpoint(name, val, step.get("tag", "scenario"))
        self.record("setpoint", name, val, "valid", step.get("label"),
                    step.get("technique"), step.get("note"))

    def _targets(self, step):
        sid = step.get("station")
        if sid is not None:
            return self.model.station_points(sid)
        return step.get("points", [])

    def do_comms_loss(self, step):
        names = self._targets(step)
        with self._lock:
            for name in names:
                self.dropped.add(name)
                self.qual[name] = QUALITY["notvalid"]
                self._assert(name)            # one NOT-VALID write so it flips fast
        self.record("comms_loss", None, None, "notvalid", step.get("label"),
                    step.get("technique"),
                    step.get("note") or ("comms loss: %s" % ", ".join(names)))

    def do_restore_comms(self, step):
        names = self._targets(step)
        with self._lock:
            for name in names:
                self.dropped.discard(name)
                self.qual[name] = QUALITY["valid"]
                self._assert(name)
        self.record("restore_comms", None, None, "valid", step.get("label"),
                    None, step.get("note") or ("comms restored: %s" % ", ".join(names)))

    def do_quality(self, step):
        name = step["point"]
        qname = step["quality"]
        with self._lock:
            self.qual[name] = QUALITY[qname]
            self.scripted.add(name)
            self._assert(name)
        self.record("quality", name, self.value.get(name), qname,
                    step.get("label"), step.get("technique"), step.get("note"))

    def do_annotate(self, step):
        self.record("annotate", note=step.get("note", ""), label=step.get("label", "benign"))

    def do_scan(self, step):
        """Reconnaissance and collection: the attacker reads the model. With
        'discover' it also reads the Block 1 metadata (version, supported features,
        bilateral table). This is the browsing a detector should catch: a peer
        reading objects it has no operational need to read."""
        points = step.get("points")
        if step.get("all") or not points:
            points = list(self.model.type.keys())
        agent = self._actor(step, True)
        try:
            if step.get("discover"):
                agent.snapshot(points)          # metadata plus the points in one sweep
            else:
                for name in points:
                    agent.read(name)
        except IOError:
            return
        self.record("scan", note=step.get("note") or ("read %d object(s)" % len(points)),
                    label=step.get("label", "malicious"), technique=step.get("technique"))

    def do_flood(self, step):
        """Denial of service: hammer a control or point with rapid messages for a
        few seconds. A marker is recorded about once a second so the labelled window
        covers the whole flood."""
        target = step["target"]
        seconds = float(step.get("seconds", 5.0))
        rate = float(step.get("rate", 10.0))
        kind = step.get("kind", "operate")      # operate | write
        agent = self._actor(step, True)
        interval = 1.0 / max(1.0, rate)
        is_float = self.model.is_float(target)
        deadline = time.time() + seconds
        toggle, next_mark = 0, 0.0
        while time.time() < deadline and not self._stop.is_set():
            try:
                if kind == "write":
                    agent.write_q(target, toggle, is_float, QUALITY["valid"], int(time.time()))
                else:
                    agent.operate(target, toggle, "flood")
            except IOError:
                break
            toggle ^= 1
            if time.time() >= next_mark:
                self.record("flood", target, toggle,
                            label=step.get("label", "malicious"),
                            technique=step.get("technique", "T0814"),
                            note=step.get("note") or ("flood %s" % target))
                next_mark = time.time() + 1.0
            self._sleep(interval)

    # ---- the run loop ----------------------------------------------------- #

    def _sleep(self, secs):
        """Interruptible sleep so QUIT/Ctrl+C ends the run promptly."""
        self._stop.wait(max(0.0, secs))

    def run(self):
        if not self.agent.wait_online(timeout=10):
            log("[scenario] ICCP association did not come online; aborting")
            return 1
        if self.attacker and not self.attacker.wait_online(timeout=10):
            log("[scenario] attacker association did not come online; continuing on one")
            self.attacker = None
            self.mal = self.agent
        self.start = time.time()
        log("[scenario] running %r (seed %s); ICCP online, %d point(s)"
            % (self.s.get("name", "scenario"), self.s.get("seed", 0), len(self.value)))

        # seed every point at its baseline before the timeline starts
        with self._lock:
            for name in self.model.type:
                self._assert(name)

        hb = threading.Thread(target=self.heartbeat, daemon=True)
        hb.start()

        timeline = sorted(self.s.get("timeline", []), key=lambda s: s.get("at", 0))
        dispatch = {
            "annotate": self.do_annotate,
            "set": lambda s: self.do_set(s, malicious=False),
            "inject": lambda s: self.do_set(s, malicious=True),
            "pulse": self.do_pulse,
            "ramp": self.do_ramp,
            "operate": self.do_operate,
            "setpoint": self.do_setpoint,
            "comms_loss": self.do_comms_loss,
            "restore_comms": self.do_restore_comms,
            "quality": self.do_quality,
            "scan": self.do_scan,
            "flood": self.do_flood,
        }
        try:
            for step in timeline:
                if self._stop.is_set():
                    break
                target_t = float(step.get("at", 0))
                now_t = time.time() - self.start
                if target_t > now_t:
                    self._sleep(target_t - now_t)
                do = step["do"]
                if do == "end":
                    self.record("end", note=step.get("note", "scenario end"))
                    break
                try:
                    dispatch[do](step)
                except IOError as e:
                    log("[scenario] agent gone: %s" % e)
                    break
        except KeyboardInterrupt:
            log("\n[scenario] interrupted")
        finally:
            self._stop.set()
        log("[scenario] done")
        return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def load_scenario(path):
    with open(path) as f:
        return json.load(f)


def cmd_validate(args):
    model = PointModel(args.config)
    scenario = load_scenario(args.scenario)
    errors = validate(scenario, model)
    for e in errors:
        print("[ERROR] " + e)
    if errors:
        print("scenario INVALID: %d error(s)" % len(errors))
        return 1
    print("scenario OK: %d timeline step(s)" % len(scenario.get("timeline", [])))
    return 0


def cmd_run(args):
    model = PointModel(args.config)
    scenario = load_scenario(args.scenario)
    errors = validate(scenario, model)
    if errors:
        for e in errors:
            print("[ERROR] " + e)
        sys.exit("scenario INVALID; fix the errors above before running")

    out = open(args.out, "w") if args.out else None
    if out:
        out.write(json.dumps({"ground_truth": scenario.get("name", "scenario"),
                              "seed": scenario.get("seed", 0),
                              "started": round(time.time(), 3)}) + "\n")
        out.flush()
        log("[scenario] ground truth -> %s" % args.out)

    agent = Agent(args.server_host, args.server_port, model.domain)
    attacker = (Agent(args.server_host, args.server_port, model.domain)
                if scenario.get("attacker") else None)
    try:
        rc = Runner(scenario, model, agent, out, attacker).run()
    finally:
        agent.stop()
        if attacker:
            attacker.stop()
        if out:
            out.close()
    return rc


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite scenario engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a scenario against the point model")
    v.add_argument("scenario")
    v.add_argument("--config", default=os.environ.get("SCADA_CONFIG", DEFAULT_CONFIG))
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("run", help="play a scenario against a running server")
    r.add_argument("scenario")
    r.add_argument("--config", default=os.environ.get("SCADA_CONFIG", DEFAULT_CONFIG))
    r.add_argument("--server-host", default=os.environ.get("TASE2_HOST", "127.0.0.1"))
    r.add_argument("--server-port", type=int,
                   default=int(os.environ.get("TASE2_PORT", "102")))
    r.add_argument("--out", default=os.environ.get("SCENARIO_OUT"),
                   help="write the ground-truth timeline here (JSON lines)")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
