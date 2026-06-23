#!/usr/bin/env python3
"""Validate the SCADA point model and (optionally) a tag database, with clear
errors, before the stack starts.

usage:
  validate_config.py config/scada.json [ingest/tags.json]

Exits non-zero if there are errors. Warnings do not fail. The launch scripts call
this so a typo is caught up front with a readable message instead of a confusing
runtime failure.
"""
import json
import sys

KNOWN_DRIVERS = {"stub", "modbus", "dnp3"}
MODBUS_DECODES = {"uint16", "int16", "uint32", "int32", "float32"}
MODBUS_KINDS = {"holding", "input"}

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        err("file not found: %s" % path)
    except json.JSONDecodeError as e:
        err("%s: invalid JSON: %s" % (path, e))
    return None


def validate_scada(cfg):
    """Return {point_name: {"type":.., "control":kind|None, "mode":..}}."""
    points = {}
    if not cfg.get("domain"):
        warn("scada: no 'domain' set, defaulting to TestDomain")
    stations = cfg.get("stations")
    if not stations:
        err("scada: no 'stations' defined")
        return points
    seen_ids = set()
    for si, st in enumerate(stations):
        sid = st.get("id")
        if not sid:
            err("scada: station #%d has no 'id'" % si)
            continue
        if sid in seen_ids:
            err("scada: duplicate station id %r" % sid)
        seen_ids.add(sid)
        for p in st.get("points", []):
            name = p.get("name")
            if not name:
                err("scada: a point in station %r has no 'name'" % sid)
                continue
            if name in points:
                err("scada: duplicate point name %r" % name)
            t = p.get("type", "real")
            if t not in ("real", "state"):
                err("scada: point %r has bad type %r (want real|state)" % (name, t))
            if t == "state" and not p.get("states"):
                warn("scada: state point %r has no 'states' map (HMI will show raw integers)" % name)
            ctl = p.get("control")
            kind = mode = None
            if ctl is not None:
                if not isinstance(ctl, dict):
                    err("scada: point %r 'control' must be an object" % name)
                else:
                    kind = ctl.get("kind", "discrete")
                    mode = ctl.get("mode", "direct")
                    if kind not in ("discrete", "setpoint"):
                        err("scada: point %r control.kind %r (want discrete|setpoint)" % (name, kind))
                    if mode not in ("direct", "sbo"):
                        err("scada: point %r control.mode %r (want direct|sbo)" % (name, mode))
                    if t == "real" and kind == "discrete":
                        warn("scada: real point %r with discrete control (setpoint is usual)" % name)
                    if t == "state" and kind == "setpoint":
                        warn("scada: state point %r with setpoint control (discrete is usual)" % name)
            points[name] = {"type": t, "control": kind, "mode": mode}
    return points


def validate_tags(cfg, points):
    devices = cfg.get("devices", {}) if isinstance(cfg, dict) else {}
    tags = cfg.get("tags", cfg) if isinstance(cfg, dict) else cfg
    if not isinstance(tags, list):
        err("tags: no 'tags' list")
        return
    controllable_with_tag = set()
    for t in tags:
        name = t.get("point")
        if not name:
            err("tags: a tag has no 'point'")
            continue
        if name not in points:
            err("tags: point %r is not in the scada point model" % name)
            continue
        spec = dict(t)
        ref = t.get("device")
        if ref is not None:
            if ref not in devices:
                err("tags: point %r references unknown device %r" % (name, ref))
                continue
            merged = dict(devices[ref]); merged.update(t); spec = merged
        driver = spec.get("driver")
        if driver not in KNOWN_DRIVERS:
            err("tags: point %r has unknown driver %r (want %s)"
                % (name, driver, "|".join(sorted(KNOWN_DRIVERS))))
            continue
        if driver == "modbus":
            if "register" not in spec:
                err("tags: modbus point %r has no 'register'" % name)
            if spec.get("decode", "uint16") not in MODBUS_DECODES:
                err("tags: modbus point %r bad decode %r" % (name, spec.get("decode")))
            if spec.get("kind", "holding") not in MODBUS_KINDS:
                err("tags: modbus point %r bad kind %r (want holding|input)" % (name, spec.get("kind")))
            if "host" not in spec:
                err("tags: modbus point %r has no host (set it or a device)" % name)
        elif driver == "dnp3":
            if "index" not in spec:
                err("tags: dnp3 point %r has no 'index'" % name)
            if "host" not in spec:
                err("tags: dnp3 point %r has no host (set it or a device)" % name)
        # control consistency between tag and scada model
        tag_ctl = t.get("control") is not None
        scada_ctl = points[name]["control"] is not None
        if tag_ctl and not scada_ctl:
            warn("tags: point %r has a control block but is not controllable in scada.json" % name)
        if tag_ctl and scada_ctl:
            controllable_with_tag.add(name)
            if driver == "modbus" and "register" not in t.get("control", {}):
                err("tags: modbus control on %r needs control.register" % name)
            if driver == "dnp3" and "index" not in t.get("control", {}) and "index" not in spec:
                err("tags: dnp3 control on %r needs an index" % name)
    # every controllable scada point should have a control tag to carry it down
    for name, info in points.items():
        if info["control"] and name not in controllable_with_tag:
            warn("tags: controllable point %r has no control mapping; commands will not reach a device" % name)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: validate_config.py config/scada.json [tags.json]")
    scada = load(sys.argv[1])
    points = {}
    if scada is not None:
        points = validate_scada(scada)
    if len(sys.argv) >= 3:
        tags = load(sys.argv[2])
        if tags is not None:
            validate_tags(tags, points)

    for w in warnings:
        print("[warn] " + w)
    for e in errors:
        print("[ERROR] " + e)
    if errors:
        print("validation FAILED: %d error(s), %d warning(s)" % (len(errors), len(warnings)))
        sys.exit(1)
    print("validation OK: %d point(s), %d warning(s)" % (len(points), len(warnings)))


if __name__ == "__main__":
    main()
