# FreeTASE2 Suite

A single, consolidated TASE.2 / ICCP (IEC 60870-6) tool for power and OT security
testbeds. It unifies the capabilities that were spread across the separate
projects (the closed lab simulator and the real-environment gateway) into one tool
with explicit operating modes and a control console that wraps everything.

```{note}
This repository is the consolidated build. The original projects
(`Free-Tase2-Server` and `tase2-plc-gateway`) are kept separate and unchanged as
base-level versions, in case you want a single-purpose build.
```

## What it does

One tool, selected by mode:

- **Simulation mode** publishes synthetic values and connects to nothing. For
  training, capture, and parser or IDS testing.
- **Ingestion mode** carries real field data from PLCs and RTUs over Modbus or
  DNP3, publishes it northbound as real TASE.2 with quality and time tags, shows it
  on a SCADA HMI, and sends operator commands back down to the field (direct operate
  or select-before-operate).
- **Scenario mode** plays a deterministic, scripted timeline of operations,
  attacks, and faults, the same way every run, and writes a ground-truth label
  timeline alongside it. For repeatable training, IDS regression, and building
  labelled datasets. See `docs/guides/scenarios`.

From a scenario run you can build a **labelled dataset**: `scripts/58_run_dataset.sh`
captures the traffic and joins it with the ground truth so every window of time is
marked benign or malicious with a technique tag. See `docs/guides/datasets`.

Each mode runs under a **security profile**: `insecure` (plaintext, open command
path, for ranges and attack demos) or `hardened` (mutual TLS plus a command
allowlist, for defense testing).

## Quick start

```bash
./scripts/10_build.sh          # build libIEC61850 (pinned) and the tools, once
python3 suite/console.py       # open http://127.0.0.1:8080
```

In the control console, pick a deployment and press Start, then open its SCADA HMI.
No hardware is required for the demos.

Prefer the command line:

```bash
python3 suite/tase2ctl.py list
python3 suite/tase2ctl.py run field-demo       # or sim-demo, scenario-demo, field-hardened
```

## The control plane

- **`suite/console.py`** is the management GUI. It lists deployments, starts and
  stops them, shows what is running, and links to the running SCADA HMI. This is the
  control plane the final packaged GUI builds on.
- **`suite/tase2ctl.py`** is the command-line equivalent.
- **`suite/profiles.json`** defines the named deployments (mode, security profile,
  point model, tag database, ports). Add your own here for a real testbed.

## Documentation

Full documentation is in `docs/` (Read the Docs style). Build it with
`make -C docs html` and open `docs/_build/html/index.html`. Security model and
profiles are in `SECURITY.md`. The staging plan toward the final packaged GUI tool
is in `STAGING.md`.

## Components

The proven components carry over unchanged: the TASE.2 server, the ICCP client
agent, the ingestion gateway (Modbus and DNP3), the SCADA HMI bridge and web HMI,
the DNP3 outstation simulator, the config validator, and the test, fuzz, and
interoperability suites. See `docs/concepts/components`.

## Safety

Simulation mode connects to nothing and is safe for an open lab. Ingestion mode
reaches real devices. Keep them on segmented networks, run the hardened profile for
any real trust boundary, and never let a mode switch point a synthetic build at real
infrastructure. See `SECURITY.md` and the OT safety guidance in the docs.

## License

GPL-3.0, see [`LICENSE`](LICENSE). Built on libIEC61850 (GPL-3.0).
