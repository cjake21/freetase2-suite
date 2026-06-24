#!/usr/bin/env python3
"""
tase2ctl: the unified control plane for the FreeTASE2 Suite.

One entry point that wraps every operating mode. A deployment (named in
suite/profiles.json) selects an operating mode and a security profile, and
tase2ctl launches the right stack for it by driving the proven run scripts with
the correct configuration and environment.

  tase2ctl list                 list the named deployments
  tase2ctl validate <name>      validate a deployment's config + tags
  tase2ctl run <name>           run a deployment in the foreground

Operating modes:
  simulation   server drives synthetic values, no ingestion, connects to nothing
               (training and capture; the classic lab behaviour)
  ingestion    server carries real field data from the ingestion gateway over
               Modbus or DNP3, and accepts control down to the devices
  scenario     a deterministic, scripted timeline (suite/scenario.py) is the value
               source: reproducible operations, attacks, and faults, with a
               ground-truth label timeline written out for datasets and scoring
  physics      a power-flow co-simulation (suite/physics.py) is the value source: a
               real grid model drives the points, and a breaker command makes flows
               redistribute and overloaded lines cascade

Security profiles:
  insecure     plaintext, open command path (ranges and attack demos)
  hardened     mutual TLS (Secure ICCP) plus a loopback command allowlist

The control console (suite/console.py) imports this module to start and stop
deployments from a GUI. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PROFILES = os.path.join(HERE, "profiles.json")

# operating mode -> the run script that implements it. Ingestion uses one unified
# launcher for any protocol mix; bench simulators are enabled via env from the
# deployment's "sims" list.
LAUNCHERS = {
    "simulation": "scripts/50_run_hmi.sh",
    "ingestion": "scripts/55_run_scada.sh",
    "scenario": "scripts/56_run_scenario.sh",
    "physics": "scripts/57_run_physics.sh",
}


def load_profiles():
    with open(PROFILES) as f:
        return json.load(f).get("deployments", {})


def get_deployment(name):
    deps = load_profiles()
    if name not in deps:
        sys.exit("unknown deployment %r (try: tase2ctl list)" % name)
    return deps[name]


def build_launch(name):
    """Return (argv, env) to launch a deployment. Used by the CLI and the console."""
    d = get_deployment(name)
    mode = d.get("mode", "ingestion")
    env = dict(os.environ)
    env["SCADA_CONFIG"] = os.path.join(ROOT, d["config"])
    env["HTTP_PORT"] = str(d.get("http_port", 8800))
    env["TASE2_PORT"] = str(d.get("tase2_port", 10502))
    env["PROFILE"] = d.get("security", "insecure")
    if d.get("tags"):
        env["TAGS"] = os.path.join(ROOT, d["tags"])

    # scenario mode: the deployment names a scenario file the engine plays.
    if d.get("scenario"):
        env["SCENARIO"] = os.path.join(ROOT, d["scenario"])
    # physics mode: the deployment names a grid model the co-simulation solves.
    if d.get("grid"):
        env["GRID"] = os.path.join(ROOT, d["grid"])

    # bench field-device simulators for the demo (no hardware): a deployment lists
    # which to start under "sims" (e.g. ["modbus","dnp3"]).
    sims = d.get("sims", [])
    if "modbus" in sims:
        env["MODBUS_SIM"] = "1"
    if "dnp3" in sims:
        env["DNP3_SIM"] = "1"

    if mode not in LAUNCHERS:
        sys.exit("deployment %r has unknown mode %r" % (name, mode))
    return ["bash", os.path.join(ROOT, LAUNCHERS[mode])], env


def cmd_list(_args):
    deps = load_profiles()
    width = max((len(n) for n in deps), default=4)
    print("%-*s  %-11s  %-9s  %s" % (width, "NAME", "MODE", "SECURITY", "DESCRIPTION"))
    for name, d in deps.items():
        print("%-*s  %-11s  %-9s  %s" % (
            width, name, d.get("mode", "?"), d.get("security", "insecure"),
            d.get("description", "")))


def cmd_validate(args):
    d = get_deployment(args.name)
    argv = [sys.executable, os.path.join(ROOT, "scripts", "validate_config.py"),
            os.path.join(ROOT, d["config"])]
    if d.get("tags"):
        argv.append(os.path.join(ROOT, d["tags"]))
    sys.exit(subprocess.call(argv))


def cmd_run(args):
    argv, env = build_launch(args.name)
    print("[tase2ctl] running deployment %r" % args.name)
    sys.exit(subprocess.call(argv, env=env))


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite control plane")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list deployments").set_defaults(func=cmd_list)
    v = sub.add_parser("validate", help="validate a deployment's config")
    v.add_argument("name"); v.set_defaults(func=cmd_validate)
    r = sub.add_parser("run", help="run a deployment")
    r.add_argument("name"); r.set_defaults(func=cmd_run)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
