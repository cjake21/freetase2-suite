#!/usr/bin/env python3
"""
FreeTASE2 SCADA HMI bridge (config-driven, multi-station).

Serves a SCADA HMI whose station/point layout is driven entirely by
config/scada.json, and wires its interactions to a *real* TASE.2/ICCP exchange
against the FreeTASE2 server.

It drives two persistent ICCP clients (src/tase2_hmi_agent):

  * a writer     (Station A) - operator SET/RELEASE actions become real MMS
    writes to the configured points;
  * a subscriber (Station B) - it enables a transfer set over the configured
    point list and receives the server's Block 2 InformationReports, which feed
    the live station grid.

The HMI renders one card per station (one per PLC/feed) from whatever
config/scada.json declares, so adding a PLC to the config grows the HMI to N
stations with no code change. Each station's comms state is derived from data
freshness: a station is ONLINE while fresh report values are arriving for its
points and goes OFFLINE (STALE) when they stop.

Everything the operator does still turns into capturable TASE.2/MMS traffic on
TCP/102 (or whatever port the server is on), and the station grid only ever shows
what genuinely arrived over the wire.

Stdlib only - no external dependencies. Python 3.7+.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
AGENT_BIN = os.path.join(HERE, "..", "src", "tase2_hmi_agent")
DEFAULT_CONFIG = os.path.join(HERE, "..", "config", "scada.json")

# A station is considered ONLINE while at least one of its points has produced a
# fresh value within this window; otherwise it reads OFFLINE / STALE. This is the
# data-freshness comms model: live field data keeps a station up, loss of data
# brings it down. (A station whose points never change would look stale; every
# real station here carries at least one analog. Per-point quality/timestamp from
# the field is the production refinement - see docs.)
STALE_SEC = 12.0

# How long an operator selection stays armed in the HMI before it must be
# re-selected. Kept just under the server's select timeout (30 s) so the HMI arms
# down before the server does.
SBO_ARM_SEC = 28.0


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

class Config:
    """The published point model + station layout, loaded from scada.json."""

    def __init__(self, path):
        with open(path) as f:
            cfg = json.load(f)
        self.domain = cfg.get("domain", "TestDomain")
        self.stations = []
        self.points = []          # flat, in subscribe order
        self.point_index = {}     # name -> point dict
        for st in cfg.get("stations", []):
            station = {"id": st["id"], "name": st.get("name", st["id"]),
                       "point_names": []}
            for p in st.get("points", []):
                ctl = p.get("control") if isinstance(p.get("control"), dict) else None
                pt = {
                    "name": p["name"],
                    "type": p.get("type", "real"),
                    "label": p.get("label", p["name"]),
                    "unit": p.get("unit", ""),
                    "states": p.get("states"),
                    "station": st["id"],
                    # control kind ("discrete"/"setpoint") or None, and mode
                    # ("direct"/"sbo"); the server publishes the control object as
                    # <name>_ctl
                    "control": ctl.get("kind") if ctl else None,
                    "control_mode": ctl.get("mode", "direct") if ctl else None,
                    # optional operator alarm limits (engineering units)
                    "hi": p.get("hi"), "lo": p.get("lo"),
                }
                self.points.append(pt)
                self.point_index[pt["name"]] = pt
                station["point_names"].append(pt["name"])
            self.stations.append(station)
        if not self.points:
            sys.exit("[hmi] config %s declares no points" % path)

    @property
    def point_names(self):
        return [p["name"] for p in self.points]

    def is_float(self, name):
        return self.point_index[name]["type"] == "real"


# --------------------------------------------------------------------------- #
# ICCP agent (one tase2_hmi_agent subprocess)
# --------------------------------------------------------------------------- #

class Agent:
    """A persistent tase2_hmi_agent subprocess with a JSON-line reader thread."""

    def __init__(self, name, host, port, domain, on_event):
        self.name = name
        self.on_event = on_event
        self.online = False
        self.proc = subprocess.Popen(
            [AGENT_BIN, host, str(port), domain],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
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
            self.on_event(self.name, ev)

    def send(self, cmd):
        with self._lock:
            if self.proc.poll() is None and self.proc.stdin:
                try:
                    self.proc.stdin.write(cmd + "\n")
                    self.proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass

    def stop(self):
        self.send("QUIT")
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


# --------------------------------------------------------------------------- #
# HMI state, ICCP plumbing, SSE fan-out
# --------------------------------------------------------------------------- #

class Hmi:
    def __init__(self, cfg, host, port):
        self.cfg = cfg
        self.server = {"host": host, "port": port, "domain": cfg.domain}
        self.lock = threading.RLock()
        self.subscribers = set()  # SSE client queues

        self.meta = {
            "dataset": "ds_hmi", "transferset": "DSTransferSet01",
            "version": None, "features": None, "blt": None, "next_ts": None,
        }
        self.online = {"A": False, "B": False}

        # Per-point received state: value, TASE.2 quality byte, and field time tag
        # (Unix seconds). Quality and time come straight from the ICCP reports.
        self.values = {p["name"]: None for p in cfg.points}
        self.quality = {p["name"]: 0 for p in cfg.points}
        self.ts = {p["name"]: 0 for p in cfg.points}

        self.report = {"last_report_time": None, "count": 0, "cond": None}
        # SBO arm state: item -> wall-clock expiry of the operator's selection
        self.armed = {}

        self.writer = Agent("A", host, port, cfg.domain, self._on_agent_event)
        self.subscriber = Agent("B", host, port, cfg.domain, self._on_agent_event)
        # subscribe over exactly the configured point set
        self.subscriber.send("SUBSCRIBE " + " ".join(cfg.point_names))
        self.writer.send("SNAPSHOT " + " ".join(cfg.point_names))

    # ---- ICCP agent events -------------------------------------------------

    def _on_agent_event(self, who, ev):
        kind = ev.get("ev")
        with self.lock:
            if kind == "online":
                self.online[who] = True
            elif kind == "snapshot":
                for k in ("version", "features", "blt", "next_ts"):
                    if ev.get(k) is not None:
                        self.meta[k] = ev[k]
            elif kind == "report" and who == "B":
                q = ev.get("q", {})
                t = ev.get("t", {})
                for name in self.values:
                    if name in ev:
                        self.values[name] = ev[name]
                        self.quality[name] = q.get(name, 0)
                        self.ts[name] = t.get(name, 0)
                self.report["last_report_time"] = ev.get("time")
                self.report["cond"] = ev.get("cond")
                self.report["count"] += 1
            elif kind == "report":
                return  # ignore the writer's echo of the broadcast report
            elif kind == "select":
                item = self._item_of(ev.get("device"))
                if item and ev.get("err") == 0:
                    self.armed[item] = time.time() + SBO_ARM_SEC
                elif item:
                    self.armed.pop(item, None)  # select denied
            elif kind in ("cancel", "operate"):
                item = self._item_of(ev.get("device"))
                if item:
                    self.armed.pop(item, None)  # selection consumed or cancelled
        self._broadcast()

    @staticmethod
    def _item_of(device):
        """Map a control object name (<item>_ctl) back to the point name."""
        if device and device.endswith("_ctl"):
            return device[:-4]
        return None

    # ---- operator actions --------------------------------------------------

    def _control_point(self, item):
        pt = self.cfg.point_index.get(item)
        if pt is None:
            raise ValueError("unknown point %r" % item)
        if not pt.get("control"):
            raise ValueError("point %r is not controllable" % item)
        return pt

    def select(self, item):
        """SBO step 1: select the device. The server grants it to this connection
        for a timeout; the arm state is set from the agent's select event."""
        self._control_point(item)
        self.writer.send("SELECT %s_ctl" % item)
        self._broadcast()

    def cancel(self, item):
        self._control_point(item)
        self.writer.send("CANCEL %s_ctl" % item)
        self.armed.pop(item, None)
        self._broadcast()

    def command(self, item, value, tag="hmi"):
        """Operator command on a controllable point. Sends a TASE.2 Block 5
        operate to the point's control object (<item>_ctl). The ingest reads that
        command and writes it down to the PLC; the read-back returns over ICCP and
        shows up on the monitoring point. Command and read-back are different
        objects, so control does not fight the gateway.

        For an SBO device the point must already be selected (armed); the server
        enforces this too, so a stray operate is rejected on both ends."""
        pt = self._control_point(item)
        device = item + "_ctl"
        if pt.get("control_mode") == "sbo":
            if self.armed.get(item, 0) <= time.time():
                raise ValueError("point %r not selected (SBO)" % item)
        if pt["control"] == "setpoint":
            self.writer.send("SETPOINT %s %r %s" % (device, float(value), tag))
        else:
            self.writer.send("OPERATE %s %d %s" % (device, int(value), tag))
        self.armed.pop(item, None)
        self._broadcast()

    # ---- state + SSE -------------------------------------------------------

    @staticmethod
    def _validity(qbyte):
        """Validity is bits 2-3 of the TASE.2 quality byte."""
        return {0: "VALID", 4: "SUSPECT", 8: "HELD", 12: "NOTVALID"}.get(qbyte & 12, "VALID")

    def _point_good(self, name, now):
        """A point reads good when the link is up, its quality is VALID, it has a
        real time tag, and that tag is recent. Quality comes from the field via
        ICCP, not inferred. The recency check still catches a silent link."""
        if not self.online.get("B", False):
            return False
        return (self.quality[name] & 12) == 0 and self.ts[name] > 0 \
            and (now - self.ts[name]) < STALE_SEC

    def _station_view(self, now):
        """Per-station view the HMI renders. Comms is a per-PLC property: a station
        is ONLINE if any of its points is good (the PLC is delivering valid, recent
        field data). Per-point quality and time tag come straight from ICCP."""
        out = []
        for st in self.cfg.stations:
            goods = {name: self._point_good(name, now) for name in st["point_names"]}
            online = any(goods.values())
            pts = []
            for name in st["point_names"]:
                p = self.cfg.point_index[name]
                age = int(now - self.ts[name]) if self.ts[name] > 0 else None
                arm_left = int(self.armed[name] - now) if name in self.armed else 0
                pts.append({
                    "name": name, "label": p["label"], "unit": p["unit"],
                    "type": p["type"], "states": p["states"],
                    "value": self.values[name], "fresh": goods[name],
                    "quality": self._validity(self.quality[name]),
                    "ts": self.ts[name], "age": age,
                    "control": p["control"], "mode": p["control_mode"],
                    "armed": arm_left if arm_left > 0 else 0,
                    "hi": p["hi"], "lo": p["lo"],
                })
            out.append({
                "id": st["id"], "name": st["name"],
                "online": online, "points": pts,
            })
        return out

    def snapshot(self):
        with self.lock:
            now = time.time()
            return {
                "server": self.server,
                "online": dict(self.online),
                "meta": dict(self.meta),
                "report": dict(self.report),
                "stale_sec": STALE_SEC,
                "stations": self._station_view(now),
            }

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def _broadcast(self):
        snap = self.snapshot()
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(snap)
            except queue.Full:
                pass

    def tick(self):
        """Periodic rebroadcast so freshness/comms decay is reflected even when
        no new report arrives (e.g. a station going stale)."""
        self._broadcast()

    def stop(self):
        self.writer.stop()
        self.subscriber.stop()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    hmi = None  # set in main()

    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, self.hmi.snapshot())
        if path == "/api/events":
            return self._serve_events()
        if path.startswith("/static/"):
            name = os.path.basename(path)
            ctype = ("text/css" if name.endswith(".css")
                     else "application/javascript" if name.endswith(".js")
                     else "application/octet-stream")
            return self._serve_static(name, ctype)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/control":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length < 0 or length > 65536:        # cap; this is a control API, not an upload
                return self._send(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                return self._send(400, {"error": "body must be a JSON object"})
            action = body.get("action")
            if action == "command":
                self.hmi.command(body["item"], body["value"], body.get("tag", "hmi"))
            elif action == "select":
                self.hmi.select(body["item"])
            elif action == "cancel":
                self.hmi.cancel(body["item"])
            elif action == "snapshot":
                self.hmi.writer.send("SNAPSHOT " + " ".join(self.hmi.cfg.point_names))
            else:
                return self._send(400, {"error": "unknown action"})
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - never 500 the control API
            return self._send(500, {"error": "internal error: %s" % e})
        self._send(200, {"ok": True})

    def _serve_static(self, name, ctype):
        fpath = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(fpath):
            return self._send(404, {"error": "not found"})
        with open(fpath, "rb") as f:
            self._send(200, f.read(), ctype)

    def _serve_events(self):
        q = self.hmi.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"data: " + json.dumps(self.hmi.snapshot()).encode() + b"\n\n")
            self.wfile.flush()
            while True:
                try:
                    snap = q.get(timeout=15)
                    payload = b"data: " + json.dumps(snap).encode() + b"\n\n"
                except queue.Empty:
                    payload = b": keepalive\n\n"
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.hmi.unsubscribe(q)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="FreeTASE2 SCADA HMI bridge")
    ap.add_argument("--config", default=os.environ.get("SCADA_CONFIG", DEFAULT_CONFIG),
                    help="scada.json point/station model")
    ap.add_argument("--server-host", default=os.environ.get("TASE2_HOST", "127.0.0.1"))
    ap.add_argument("--server-port", type=int, default=int(os.environ.get("TASE2_PORT", "10502")))
    ap.add_argument("--http-host", default=os.environ.get("HTTP_HOST", "127.0.0.1"))
    ap.add_argument("--http-port", type=int, default=8800)
    args = ap.parse_args()

    if not os.path.isfile(AGENT_BIN):
        sys.exit("[hmi] build first: (cd src && make tase2_hmi_agent)")
    if not os.path.isfile(args.config):
        sys.exit("[hmi] config not found: %s" % args.config)

    cfg = Config(args.config)
    Handler.hmi = Hmi(cfg, args.server_host, args.server_port)

    # background ticker so comms staleness is reflected without a new report
    def ticker():
        while True:
            time.sleep(2.0)
            Handler.hmi.tick()
    threading.Thread(target=ticker, daemon=True).start()

    httpd = ThreadingHTTPServer((args.http_host, args.http_port), Handler)
    print("[hmi] SCADA HMI on http://%s:%d  (TASE.2 %s:%d domain %s, %d stations / %d points)" % (
        args.http_host, args.http_port, args.server_host, args.server_port,
        cfg.domain, len(cfg.stations), len(cfg.points)))
    print("[hmi] open the URL above; Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[hmi] shutting down")
    finally:
        Handler.hmi.stop()
        httpd.shutdown()


if __name__ == "__main__":
    main()
