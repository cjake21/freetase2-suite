#!/usr/bin/env python3
"""Generate the utility-scale point model and grid model from one topology.

This emits two files that must stay perfectly in sync (every grid measurement and
breaker must name a published point, every point the grid drives must exist):

  config/scada_utility.json   the northbound TASE.2 point model + HMI layout
  config/grid_utility.json    the DC power-flow model behind those points

Defining the topology once here means the two files cannot drift, and growing the
model later is a matter of adding a substation, a line, or a few points in the
tables below and re-running this generator. It is a moderate regional system: two
generation plants, a 345 kV backbone, four 138 kV load substations, and two
external tie-lines to neighbouring areas, with the full telemetry taxonomy a real
inter-control-centre ICCP feed carries (MW and MVAR, bus kV, frequency, MWh
accumulators, transformer taps and temperatures, tie schedules and Area Control
Error, plus breaker status and control).

usage: gen_utility_model.py            # writes both files under config/
"""
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DOMAIN = "WESTERN_AREA"
NOMINAL_HZ = 60.0
BASE_MVA = 100

# --- topology -------------------------------------------------------------- #
# buses: id, nominal kV, is-slack
BUSES = [
    ("RIVER345", 345, True),     # River thermal plant (slack)
    ("MESA345", 345, False),     # Mesa combined-cycle plant
    ("CENTRAL345", 345, False),  # Central 345 kV hub
    ("EAST345", 345, False),     # East 345 kV substation
    ("NTIE345", 345, False),     # tie bus to the NORTH neighbour
    ("STIE345", 345, False),     # tie bus to the SOUTH neighbour
    ("OAKDALE138", 138, False),  # Oakdale 138 kV load substation
    ("PINE138", 138, False),     # Pine 138 kV load substation
    ("CEDAR138", 138, False),    # Cedar 138 kV load substation
    ("STEEL138", 138, False),    # Steelworks industrial 138 kV load
]
# generators: bus, scheduled MW (slack is balanced by the solver)
GENS = [("RIVER345", 0), ("MESA345", 180), ("STIE345", 60)]
# loads: bus, MW  (NTIE is an export, modelled as load at the tie bus)
LOADS = [("OAKDALE138", 120), ("PINE138", 90), ("CEDAR138", 70),
         ("STEEL138", 110), ("NTIE345", 80)]
# lines: id, from, to, reactance x, thermal limit MW
LINES = [
    ("L_RIV_CEN",   "RIVER345",   "CENTRAL345", 0.04, 400),
    ("L_MESA_CEN",  "MESA345",    "CENTRAL345", 0.05, 350),
    ("L_CEN_EAST",  "CENTRAL345", "EAST345",    0.05, 400),
    # L_RIV_EAST is the engineered N-1 vulnerability: it is the only other 345 kV
    # feed to East, so opening the Central-East tie forces East's load onto it and
    # it overloads and trips, islanding East / Pine / Steel / South-tie (a cascade
    # the attack scenarios trigger on the realistic environment).
    ("L_RIV_EAST",  "RIVER345",   "EAST345",    0.08, 130),
    ("L_CEN_NTIE",  "CENTRAL345", "NTIE345",    0.06, 250),
    ("L_EAST_STIE", "EAST345",    "STIE345",    0.06, 250),
    ("T_CEN_OAK",   "CENTRAL345", "OAKDALE138", 0.12, 200),
    ("T_CEN_CED",   "CENTRAL345", "CEDAR138",   0.14, 160),
    ("T_EAST_PINE", "EAST345",    "PINE138",    0.12, 180),
    ("T_EAST_STEEL","EAST345",    "STEEL138",   0.10, 220),
    ("L_OAK_CED",   "OAKDALE138", "CEDAR138",   0.15, 120),
]

# --- accumulators while we build ------------------------------------------- #
stations = []          # scada.json stations
measurements = []      # grid.json measurements
breakers = []          # grid.json breakers
nominals = {}          # grid.json nominals (points with no physics source)
_pts = []              # current station's points


def pt(name, typ, label, unit, **extra):
    p = {"name": name, "type": typ, "label": label, "unit": unit}
    p.update(extra)
    _pts.append(p)


def analog(name, label, unit, meas):
    pt(name, "real", label, unit)
    m = {"point": name}
    m.update(meas)
    measurements.append(m)


def status(name, label, states, line=None, control=None):
    p = {"name": name, "type": "state", "label": label, "unit": "", "states": states}
    if control:
        p["control"] = control
    _pts.append(p)
    if line:
        breakers.append({"point": name, "line": line})


def setpoint(name, label, unit, nominal, mode="direct"):
    pt(name, "real", label, unit, control={"kind": "setpoint", "mode": mode})
    nominals[name] = nominal


def station(sid, sname):
    global _pts
    if _pts:
        stations[-1]["points"] = _pts
    _pts = []
    stations.append({"id": sid, "name": sname})


def finish():
    if _pts:
        stations[-1]["points"] = _pts


def loading(name, label, line):
    analog(name, label, "%", {"type": "line_loading", "line": line})


def freqpt(name, label):
    analog(name, label, "Hz", {"type": "frequency"})


def alarm(name, label, value=0):
    _pts.append({"name": name, "type": "state", "label": label, "unit": "",
                 "states": {"0": "NORMAL", "1": "ALARM"}})
    nominals[name] = value


CB_STATES = {"0": "OPEN", "1": "CLOSED"}
COOL_STATES = {"0": "OFF", "1": "RUN"}

# --- generation plants ----------------------------------------------------- #
station("river", "RIVER GENERATING STATION")
analog("RIVER_GEN_MW",   "UNIT 1 OUTPUT",  "MW",   {"type": "gen_mw", "bus": "RIVER345"})
analog("RIVER_GEN_MVAR", "UNIT 1 REACTIVE","MVAR", {"type": "gen_mvar", "bus": "RIVER345", "pf": 0.92})
analog("RIVER_BUS_KV",   "345 BUS VOLTAGE","kV",   {"type": "bus_vmag", "bus": "RIVER345", "nominal_kv": 345})
analog("RIVER_MWH",      "UNIT 1 ENERGY",  "MWh",  {"type": "accumulator", "line": "L_RIV_CEN"})
freqpt("RIVER_HZ", "GOVERNOR SPEED")
setpoint("RIVER_AVR", "AVR SETPOINT", "PU", 1.0)
setpoint("RIVER_AGC", "AGC SETPOINT", "MW", 230.0)
status("RIVER_CB", "UNIT 1 BREAKER", CB_STATES, line="L_RIV_CEN",
       control={"kind": "discrete", "mode": "sbo"})
alarm("RIVER_ALARM", "UNIT ALARM")

station("mesa", "MESA COMBINED CYCLE")
analog("MESA_GEN_MW",   "UNIT 1 OUTPUT",   "MW",   {"type": "gen_mw", "bus": "MESA345"})
analog("MESA_GEN_MVAR", "UNIT 1 REACTIVE", "MVAR", {"type": "gen_mvar", "bus": "MESA345", "pf": 0.92})
analog("MESA_BUS_KV",   "345 BUS VOLTAGE", "kV",   {"type": "bus_vmag", "bus": "MESA345", "nominal_kv": 345})
analog("MESA_MWH",      "UNIT 1 ENERGY",   "MWh",  {"type": "accumulator", "line": "L_MESA_CEN"})
freqpt("MESA_HZ", "GOVERNOR SPEED")
setpoint("MESA_AVR", "AVR SETPOINT", "PU", 1.0)
setpoint("MESA_AGC", "AGC SETPOINT", "MW", 180.0)
status("MESA_CB", "UNIT 1 BREAKER", CB_STATES, line="L_MESA_CEN",
       control={"kind": "discrete", "mode": "sbo"})
alarm("MESA_ALARM", "UNIT ALARM")

# --- 345 kV transmission substations --------------------------------------- #
station("central", "CENTRAL 345 SUBSTATION")
analog("CENTRAL_BUS_KV",   "345 BUS VOLTAGE", "kV", {"type": "bus_vmag", "bus": "CENTRAL345", "nominal_kv": 345})
freqpt("CENTRAL_FREQ", "BUS FREQUENCY")
analog("CENTRAL_RIVER_MW",  "LINE FROM RIVER FLOW","MW",   {"type": "line_flow", "line": "L_RIV_CEN"})
analog("CENTRAL_RIVER_MVAR","LINE FROM RIVER REACTIVE","MVAR",{"type": "line_mvar", "line": "L_RIV_CEN"})
analog("CENTRAL_MESA_MW",   "LINE FROM MESA FLOW", "MW",   {"type": "line_flow", "line": "L_MESA_CEN"})
analog("CENTRAL_MESA_MVAR", "LINE FROM MESA REACTIVE","MVAR",{"type": "line_mvar", "line": "L_MESA_CEN"})
analog("CENTRAL_EAST_MW",  "LINE TO EAST FLOW",  "MW",   {"type": "line_flow", "line": "L_CEN_EAST"})
analog("CENTRAL_EAST_MVAR","LINE TO EAST REACTIVE","MVAR",{"type": "line_mvar", "line": "L_CEN_EAST"})
loading("CENTRAL_EAST_PCT", "LINE TO EAST LOADING", "L_CEN_EAST")
analog("CENTRAL_OAK_MW",   "OAKDALE XFMR FLOW",  "MW",   {"type": "line_flow", "line": "T_CEN_OAK"})
analog("CENTRAL_OAK_MVAR", "OAKDALE XFMR REACTIVE","MVAR",{"type": "line_mvar", "line": "T_CEN_OAK"})
loading("CENTRAL_OAK_PCT", "OAKDALE XFMR LOADING", "T_CEN_OAK")
analog("CENTRAL_CED_MW",   "CEDAR XFMR FLOW",    "MW",   {"type": "line_flow", "line": "T_CEN_CED"})
analog("CENTRAL_CED_MVAR", "CEDAR XFMR REACTIVE","MVAR", {"type": "line_mvar", "line": "T_CEN_CED"})
loading("CENTRAL_CED_PCT", "CEDAR XFMR LOADING", "T_CEN_CED")
status("CENTRAL_EAST_CB", "LINE TO EAST BREAKER", CB_STATES, line="L_CEN_EAST",
       control={"kind": "discrete", "mode": "sbo"})

station("east", "EAST 345 SUBSTATION")
analog("EAST_BUS_KV",    "345 BUS VOLTAGE",   "kV",   {"type": "bus_vmag", "bus": "EAST345", "nominal_kv": 345})
freqpt("EAST_FREQ", "BUS FREQUENCY")
analog("EAST_RIVER_MW",  "LINE TO RIVER FLOW","MW",   {"type": "line_flow", "line": "L_RIV_EAST"})
analog("EAST_RIVER_MVAR","LINE TO RIVER REACTIVE","MVAR",{"type": "line_mvar", "line": "L_RIV_EAST"})
loading("EAST_RIVER_PCT","LINE TO RIVER LOADING", "L_RIV_EAST")
analog("EAST_PINE_MW",   "PINE XFMR FLOW",    "MW",   {"type": "line_flow", "line": "T_EAST_PINE"})
analog("EAST_PINE_MVAR", "PINE XFMR REACTIVE","MVAR", {"type": "line_mvar", "line": "T_EAST_PINE"})
loading("EAST_PINE_PCT", "PINE XFMR LOADING", "T_EAST_PINE")
analog("EAST_STEEL_MW",  "STEEL XFMR FLOW",   "MW",   {"type": "line_flow", "line": "T_EAST_STEEL"})
analog("EAST_STEEL_MVAR","STEEL XFMR REACTIVE","MVAR",{"type": "line_mvar", "line": "T_EAST_STEEL"})
loading("EAST_STEEL_PCT","STEEL XFMR LOADING", "T_EAST_STEEL")
status("EAST_STEEL_CB", "STEEL FEEDER BREAKER", CB_STATES, line="T_EAST_STEEL",
       control={"kind": "discrete", "mode": "sbo"})


# --- 138 kV load substations (a transformer feed each) --------------------- #
def nominal_status(name, label, states, value):
    """A status point with no bulk-model source (a normally-closed feeder), kept
    fresh at a fixed state by the publisher's nominals path."""
    _pts.append({"name": name, "type": "state", "label": label, "unit": "", "states": states})
    nominals[name] = value


def load_sub(sid, sname, prefix, bus, feed_line, tie_line=None):
    station(sid, sname)
    analog(prefix + "_BUS_KV",   "138 BUS VOLTAGE",   "kV",   {"type": "bus_vmag", "bus": bus, "nominal_kv": 138})
    analog(prefix + "_LOAD_MW",  "STATION LOAD",      "MW",   {"type": "line_flow", "line": feed_line})
    analog(prefix + "_LOAD_MVAR","STATION REACTIVE",  "MVAR", {"type": "line_mvar", "line": feed_line, "pf": 0.93})
    loading(prefix + "_LOAD_PCT", "XFMR T1 LOADING", feed_line)
    analog(prefix + "_T1_TAP",   "XFMR T1 TAP",       "step", {"type": "tap", "bus": bus, "nominal_kv": 138, "step_kv": 1.5})
    analog(prefix + "_T1_TEMP",  "XFMR T1 OIL TEMP",  "C",    {"type": "thermal", "line": feed_line, "ambient_c": 45, "gain_c": 50})
    analog(prefix + "_MWH",      "STATION ENERGY",    "MWh",  {"type": "accumulator", "line": feed_line})
    # cooling pump status driven by transformer loading (a thermal_state point)
    _pts.append({"name": prefix + "_T1_COOL", "type": "state", "label": "XFMR T1 COOLING",
                 "unit": "", "states": COOL_STATES})
    measurements.append({"point": prefix + "_T1_COOL", "type": "thermal_state",
                         "line": feed_line, "threshold_pct": 55})
    if tie_line:
        status(prefix + "_TIE_CB", "138 TIE BREAKER", CB_STATES, line=tie_line,
               control={"kind": "discrete", "mode": "direct"})
    else:
        nominal_status(prefix + "_FDR1_CB", "FEEDER 1 BREAKER", CB_STATES, 1)
    nominal_status(prefix + "_FDR2_CB", "FEEDER 2 BREAKER", CB_STATES, 1)
    nominal_status(prefix + "_CAP1_CB", "CAP BANK 1", {"0": "OFF", "1": "ON"}, 1)
    alarm(prefix + "_LV_ALARM", "LOW VOLTAGE ALARM")


load_sub("oakdale", "OAKDALE 138 SUBSTATION", "OAKDALE", "OAKDALE138", "T_CEN_OAK",
         tie_line="L_OAK_CED")
load_sub("pine", "PINE 138 SUBSTATION", "PINE", "PINE138", "T_EAST_PINE")
load_sub("cedar", "CEDAR 138 SUBSTATION", "CEDAR", "CEDAR138", "T_CEN_CED")
load_sub("steel", "STEELWORKS 138 INDUSTRIAL", "STEEL", "STEEL138", "T_EAST_STEEL")

# --- external tie-lines to neighbouring areas ------------------------------ #
station("ntie", "NORTH INTERTIE")
analog("NTIE_MW",   "INTERTIE FLOW",     "MW",   {"type": "line_flow", "line": "L_CEN_NTIE"})
analog("NTIE_MVAR", "INTERTIE REACTIVE", "MVAR", {"type": "line_mvar", "line": "L_CEN_NTIE"})
loading("NTIE_PCT", "INTERTIE LOADING", "L_CEN_NTIE")
analog("NTIE_MWH",  "INTERCHANGE ENERGY","MWh",  {"type": "accumulator", "line": "L_CEN_NTIE"})
setpoint("NTIE_SCHED", "SCHEDULED INTERCHANGE", "MW", 80.0)
status("NTIE_CB", "INTERTIE BREAKER", CB_STATES, line="L_CEN_NTIE",
       control={"kind": "discrete", "mode": "sbo"})

station("stie", "SOUTH INTERTIE")
analog("STIE_MW",   "INTERTIE FLOW",     "MW",   {"type": "line_flow", "line": "L_EAST_STIE"})
analog("STIE_MVAR", "INTERTIE REACTIVE", "MVAR", {"type": "line_mvar", "line": "L_EAST_STIE"})
loading("STIE_PCT", "INTERTIE LOADING", "L_EAST_STIE")
analog("STIE_MWH",  "INTERCHANGE ENERGY","MWh",  {"type": "accumulator", "line": "L_EAST_STIE"})
setpoint("STIE_SCHED", "SCHEDULED INTERCHANGE", "MW", -60.0)
status("STIE_CB", "INTERTIE BREAKER", CB_STATES, line="L_EAST_STIE",
       control={"kind": "discrete", "mode": "sbo"})

# --- system / EMS area points ---------------------------------------------- #
station("sys", "AREA EMS")
analog("SYS_FREQ", "SYSTEM FREQUENCY", "Hz", {"type": "frequency"})
analog("SYS_ACE",  "AREA CONTROL ERROR", "MW",
       {"type": "ace", "bias_mw_per_hz": 50.0,
        "ties": [{"line": "L_CEN_NTIE", "sched": 80.0}, {"line": "L_EAST_STIE", "sched": -60.0}]})
pt("SYS_TIME_ERROR", "real", "TIME ERROR", "s")
nominals["SYS_TIME_ERROR"] = 0.0

finish()

# --- emit ------------------------------------------------------------------ #
scada = {
    "_comment": "GENERATED by scripts/gen_utility_model.py. A moderate regional "
                "utility ICCP feed: two plants, a 345 kV backbone, four 138 kV load "
                "substations, and two external tie-lines, with MW/MVAR, bus kV, "
                "frequency, MWh accumulators, transformer taps and temperatures, "
                "tie schedules and ACE, and breaker status/control. Edit the "
                "topology in the generator and re-run; do not hand-edit this file.",
    "domain": DOMAIN,
    "stations": stations,
}
grid = {
    "_comment": "GENERATED by scripts/gen_utility_model.py. DC power-flow model "
                "behind config/scada_utility.json. Same join-by-name contract as "
                "the demo grid; suite/physics.py solves it each tick and maps the "
                "measurements onto the published points.",
    "base_mva": BASE_MVA,
    "nominal_hz": NOMINAL_HZ,
    "overload_factor": 1.0,
    "buses": [{"id": b, "slack": True} if slack else {"id": b} for (b, _kv, slack) in BUSES],
    "generators": [{"bus": b, "mw": mw} for (b, mw) in GENS],
    "loads": [{"bus": b, "mw": mw} for (b, mw) in LOADS],
    "lines": [{"id": i, "from": f, "to": t, "x": x, "limit_mw": lim} for (i, f, t, x, lim) in LINES],
    "breakers": breakers,
    "measurements": measurements,
    "nominals": nominals,
}

scada_path = os.path.join(ROOT, "config", "scada_utility.json")
grid_path = os.path.join(ROOT, "config", "grid_utility.json")
with open(scada_path, "w") as f:
    json.dump(scada, f, indent=2)
    f.write("\n")
with open(grid_path, "w") as f:
    json.dump(grid, f, indent=2)
    f.write("\n")

npoints = sum(len(s["points"]) for s in stations)
print("wrote %s (%d stations, %d points)" % (os.path.relpath(scada_path, ROOT), len(stations), npoints))
print("wrote %s (%d buses, %d lines, %d measurements, %d breakers)"
      % (os.path.relpath(grid_path, ROOT), len(BUSES), len(LINES), len(measurements), len(breakers)))
