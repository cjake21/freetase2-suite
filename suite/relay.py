#!/usr/bin/env python3
"""
relay: the inter-control-center tie. It carries data from one TASE.2 server to
another, which is what makes a federation of control centers, not just one node.

A federation has several control centers, each running its own server with its own
domain and point model. They do not measure each other's grid; they exchange agreed
data across a tie. This relay is that tie. For each link in config/federation.json
it subscribes to the source center, receives its Block 2 reports, and writes the
mapped points into the destination center over real ICCP. So control center B sees
control center A's tie-line flow appear on B's own screen, having crossed the
intertie as genuine protocol traffic, exactly the way a real interconnect works.

What a center shares is its bilateral agreement. Put a bilateral table on the source
server (-B) and the relay, like any other peer, only receives what the table allows,
so the same enforcement that scopes a partner scopes the tie.

It drives two src/tase2_hmi_agent subprocesses per link (a subscriber on the source,
a writer on the destination) over the same stdio line protocol as the bridge and the
gateway, so it needs no new protocol code. Standard library only. Python 3.7+.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
AGENT_BIN = os.path.join(ROOT, "src", "tase2_hmi_agent")
DEFAULT_FEDERATION = os.path.join(ROOT, "config", "federation.json")


def log(msg):
    print(msg, flush=True)


def point_types(config_path):
    """Map point name -> True if real/float, from a center's scada config."""
    with open(config_path) as f:
        cfg = json.load(f)
    types = {}
    for st in cfg.get("stations", []):
        for p in st.get("points", []):
            types[p["name"]] = p.get("type", "real") == "real"
    return types, cfg.get("domain", "TestDomain")


# --------------------------------------------------------------------------- #
# Agent: one tase2_hmi_agent, used as a subscriber or a writer
# --------------------------------------------------------------------------- #

class Agent:
    def __init__(self, host, port, domain, on_report=None):
        if not os.path.isfile(AGENT_BIN):
            sys.exit("[relay] build first: ./scripts/10_build.sh")
        self.on_report = on_report
        self.online = False
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [AGENT_BIN, host, str(port), domain],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
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
            kind = ev.get("ev")
            if kind == "online":
                self.online = True
            elif kind == "error":
                log("[relay] agent: %s" % ev.get("msg", "error"))
            elif kind == "report" and self.on_report:
                self.on_report(ev)

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

    def subscribe(self, points):
        self._send("SUBSCRIBE " + " ".join(points))

    def write_q(self, point, value, is_float, quality, ts):
        v = repr(float(value)) if is_float else str(int(round(value)))
        self._send("WRITEQ %s %d %s %d %d" % (point, 1 if is_float else 0, v,
                                              int(quality), int(ts)))

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
# One tie: subscribe the source, mirror mapped points into the destination
# --------------------------------------------------------------------------- #

class Tie:
    def __init__(self, link, centers):
        src = centers[link["from"]]
        dst = centers[link["to"]]
        self.name = "%s->%s" % (link["from"], link["to"])
        self.mapping = link["points"]               # source point -> dest point
        self.src_points = list(self.mapping.keys())

        _, src_domain = point_types(self._abs(src["config"]))
        dst_types, dst_domain = point_types(self._abs(dst["config"]))
        self.dst_is_float = dst_types

        self.writer = Agent(dst["host"], dst["port"], dst_domain)
        self.subscriber = Agent(src["host"], src["port"], src_domain,
                                on_report=self._on_report)
        self.mirrored = 0

    @staticmethod
    def _abs(path):
        """Center configs are written relative to the project root."""
        return path if os.path.isabs(path) else os.path.join(ROOT, path)

    def _on_report(self, ev):
        """A report arrived from the source: mirror each mapped point into the
        destination, carrying its value, quality byte, and time tag end to end."""
        q = ev.get("q", {})
        t = ev.get("t", {})
        ts = int(time.time())
        for src_pt, dst_pt in self.mapping.items():
            if src_pt not in ev or ev[src_pt] is None:
                continue
            try:
                self.writer.write_q(dst_pt, ev[src_pt],
                                    self.dst_is_float.get(dst_pt, True),
                                    int(q.get(src_pt, 0)), int(t.get(src_pt, ts) or ts))
                self.mirrored += 1
            except IOError:
                return

    def start(self):
        if not self.writer.wait_online(timeout=10):
            log("[relay] %s: destination did not come online" % self.name)
            return False
        if not self.subscriber.wait_online(timeout=10):
            log("[relay] %s: source did not come online" % self.name)
            return False
        self.subscriber.subscribe(self.src_points)
        log("[relay] tie %s online: mirroring %d point(s) across the intertie"
            % (self.name, len(self.mapping)))
        return True

    def stop(self):
        self.subscriber.stop()
        self.writer.stop()


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run(federation_path):
    with open(federation_path) as f:
        fed = json.load(f)
    centers = fed.get("centers", {})

    ties = []
    for link in fed.get("ties", []):
        if link.get("from") not in centers or link.get("to") not in centers:
            sys.exit("[relay] tie references an unknown center: %r" % link)
        ties.append(Tie(link, centers))

    if not ties:
        sys.exit("[relay] no ties defined in %s" % federation_path)

    started = [t for t in ties if t.start()]
    if not started:
        for t in ties:
            t.stop()
        return 1
    log("[relay] federation up: %d tie(s); Ctrl+C to stop" % len(started))
    try:
        while True:
            time.sleep(2.0)
            for t in started:
                if t.subscriber.proc.poll() is not None or t.writer.proc.poll() is not None:
                    log("[relay] tie %s lost an agent; stopping" % t.name)
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        log("\n[relay] shutting down")
    finally:
        for t in ties:
            t.stop()
    return 0


def cmd_validate(args):
    with open(args.federation) as f:
        fed = json.load(f)
    centers = fed.get("centers", {})
    errors = []
    for cid, c in centers.items():
        cfg = c.get("config")
        if not cfg or not os.path.isfile(Tie._abs(cfg)):
            errors.append("center %r has a missing config %r" % (cid, cfg))
    for link in fed.get("ties", []):
        for end in ("from", "to"):
            if link.get(end) not in centers:
                errors.append("tie names unknown center %r" % link.get(end))
        if not link.get("points"):
            errors.append("tie %s->%s shares no points" % (link.get("from"), link.get("to")))
    for e in errors:
        print("[ERROR] " + e)
    if errors:
        print("federation INVALID: %d error(s)" % len(errors))
        return 1
    print("federation OK: %d center(s), %d tie(s)"
          % (len(centers), len(fed.get("ties", []))))
    return 0


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite inter-control-center relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run all ties in a federation")
    r.add_argument("--federation", default=DEFAULT_FEDERATION)
    r.set_defaults(func=lambda a: run(a.federation))
    v = sub.add_parser("validate", help="check a federation config")
    v.add_argument("--federation", default=DEFAULT_FEDERATION)
    v.set_defaults(func=cmd_validate)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
