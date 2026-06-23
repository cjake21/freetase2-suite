#!/usr/bin/env python3
"""Flatten config/scada.json into the simple point list the TASE.2 server reads.

The server (-P) wants one "<name> <type> <control>" line per published point,
which is trivial and robust to parse in C. This keeps scada.json the single
source of truth for the point model while the server consumes a generated flat
list. The control field is '-' for a plain point, or 'discrete'/'setpoint' for a
commandable point (the server then also creates a <name>_ctl control object).

usage: gen_server_points.py <scada.json>   # prints to stdout
"""
import json
import sys

if len(sys.argv) != 2:
    sys.exit("usage: gen_server_points.py <scada.json>")

with open(sys.argv[1]) as f:
    cfg = json.load(f)

for station in cfg.get("stations", []):
    for p in station.get("points", []):
        t = p.get("type", "real")
        if t not in ("real", "state"):
            sys.exit("bad type %r for point %r" % (t, p.get("name")))
        ctl, mode = "-", "direct"
        if p.get("control"):
            ctl = p["control"].get("kind", "discrete")
            if ctl not in ("discrete", "setpoint"):
                sys.exit("bad control kind %r for point %r" % (ctl, p.get("name")))
            mode = p["control"].get("mode", "direct")
            if mode not in ("direct", "sbo"):
                sys.exit("bad control mode %r for point %r" % (mode, p.get("name")))
        print("%s %s %s %s" % (p["name"], t, ctl, mode))
