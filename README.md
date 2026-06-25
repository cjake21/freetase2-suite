# FreeTASE2 Suite

A single, free tool for power and OT security testbeds built around TASE.2 / ICCP
(IEC 60870-6), the protocol utilities use to exchange grid data between control
centers. There is no open implementation of the server side of TASE.2, so there has
been no realistic, free target to attack, defend, or study. This suite is that
target, and a great deal more around it: a complete TASE.2 node you can run on a
laptop, a SCADA HMI to watch it, a power-flow co-simulation behind the points,
scripted attacks grounded in real incidents, a labelled-dataset and detection-scoring
pipeline, enforced bilateral tables, and multi-control-center federation. One tool,
one launch.

> This repository is the consolidated build. The original projects
> (`Free-Tase2-Server` and `tase2-plc-gateway`) are kept separate and unchanged as
> base-level versions, in case you want a single-purpose build.

The Python side is standard library only. The native TASE.2 server is built on
libIEC61850. There are no paid components; comparable TASE.2 stacks are commercial
and closed.

## Quick start

```bash
./tase2-suite --build
```

That is the whole tool in one command: it builds the native tools the first time,
starts the control console, and opens it in your browser. Pick a deployment, press
**Start**, then open its SCADA HMI. No hardware is required for the demos.

Already built, or prefer the pieces:

```bash
./tase2-suite                  # launch the console and a browser
make                           # list the make targets (build, run, test, docs, docker)
python3 suite/console.py       # the console alone, no browser
```

Or run the whole thing in a container:

```bash
docker build -t freetase2-suite .
docker run --rm -p 8080:8080 -p 8800:8800 freetase2-suite   # console on :8080
```

Prefer the command line:

```bash
python3 suite/tase2ctl.py list
python3 suite/tase2ctl.py run physics-demo     # or any deployment below
```

## What it does

The tool runs in one of five operating modes, each expressed as named deployments you
pick in the console. Every mode publishes real TASE.2 / ICCP traffic on the wire.

| Mode | What drives the points | Use it for |
|------|------------------------|-----------|
| **Simulation** | the server's own synthetic values | the fastest look at the HMI; capture and parser/IDS testing |
| **Ingestion** | real field data over Modbus and DNP3 | the full field-to-control-center pipeline, with operator control back down |
| **Scenario** | a deterministic, scripted timeline | repeatable operations, attacks, and faults, with a ground-truth label timeline |
| **Physics** | a DC power-flow co-simulation | a live grid where opening a breaker redistributes flow and overloads cascade |
| **Federation** | two control centers and a live tie | a partner control center receiving another's data across the intertie |

The shipped deployments (the console groups them by purpose):

- **Demos and training**: `sim-demo`, `field-demo`, `physics-demo`
- **Attack scenarios**: `scenario-demo`, `cascade-demo`, `ukraine2015-attack`,
  `industroyer-attack`, `stealthy-attack`, `recon-attack`
- **Defense and federation**: `field-hardened`, `field-federated`, `federation-demo`
- **Physical testbed**: `physical`

Read more: [operations guide](docs/guides/operations.md),
[ingestion and configuration](docs/guides/configuration.md),
[scenarios](docs/guides/scenarios.md), [physics mode](docs/guides/physics.md),
[federation](docs/guides/federation.md).

## The detection and research pipeline

The point of the attack modes is to produce realistic traffic you can build detections
against. The flow is end to end:

1. **Run a scripted attack.** The built-in library reproduces real intrusions on the
   live grid, with the attack traffic on its own association (recon reads, false data,
   unauthorized commands, and floods come from a separate peer, the way a real attack
   looks). Each step is tagged with its MITRE ATT&CK for ICS technique. See the
   [attack library](docs/guides/attacks.md), which also explains, per attack, the exact
   TASE.2 / MMS indicators to look for.
2. **Capture and label it.** `scripts/58_run_dataset.sh` captures the run and joins it
   with the ground truth, so every time window is marked benign or malicious with a
   technique tag, with model-ready flow features. See [datasets](docs/guides/datasets.md).
3. **Score a detector.** `suite/score.py` grades a sensor's alerts (Suricata or a
   generic feed) against that ground truth and reports recall per technique, time to
   detect, and false positives. Starter rules are in [`detect/`](detect/). See
   [scoring](docs/guides/scoring.md).

Because the suite is the source of truth for what happened and when, the labels are
exact rather than guessed, which is what makes the datasets and scores trustworthy.

## Security and federation

Each mode runs under a **security profile**: `insecure` (plaintext, open command path,
for ranges and attack demos) or `hardened` (mutual TLS, also called Secure ICCP, plus a
command allowlist, for defense testing).

The server also **enforces a bilateral table** (`-B`): per-peer scoping of which data
each control center may read, control, and subscribe to. Reads, commands, injections,
and report members outside a peer's agreement are denied or withheld. Combined with the
**federation relay**, which carries scoped data across a tie between control centers,
this models a real interconnect with the scoping enforced rather than cosmetic. See
[`SECURITY.md`](SECURITY.md) and the [federation guide](docs/guides/federation.md).

## The control plane

- **`suite/console.py`** is the management GUI: it groups deployments by purpose, runs
  pre-launch checks, starts and stops a deployment, shows live logs, and links to the
  running SCADA HMI and the documentation. `./tase2-suite` launches it for you.
- **`suite/tase2ctl.py`** is the command-line equivalent (`list`, `validate`, `run`).
- **`suite/profiles.json`** defines the named deployments (mode, security profile, point
  model, tag database or scenario or grid or federation, ports). Add your own here.

## Documentation

Full documentation is in [`docs/`](docs/) (Read the Docs style). Build it with
`make docs` (or `make -C docs html`) and open `docs/_build/html/index.html`; the
control console also serves it at `/docs`. Good entry points:

- [Overview and architecture](docs/overview/index.md), [installation](docs/installation/index.md)
- Concepts: [components](docs/concepts/components.md), [data model](docs/concepts/data-model.md),
  [TASE.2 on the wire](docs/guides/tase2-on-the-wire.md)
- Guides: [operations](docs/guides/operations.md), [scenarios](docs/guides/scenarios.md),
  [attacks and indicators](docs/guides/attacks.md), [physics](docs/guides/physics.md),
  [datasets](docs/guides/datasets.md), [scoring](docs/guides/scoring.md),
  [federation](docs/guides/federation.md), [HMI demo](docs/guides/hmi-demo.md)
- [Security model and profiles](SECURITY.md), [staging plan](STAGING.md),
  [changelog](CHANGELOG.md)

## Components

- **TASE.2 / ICCP server** (`src/tase2_server.c`) on the libIEC61850 MMS engine:
  Block 1 reads, Block 2 reporting, Block 5 control with select-before-operate, the
  command allowlist, the enforced bilateral table, and TLS.
- **ICCP agent** (`src/tase2_hmi_agent.c`), a persistent client driven by line commands.
- **Ingestion gateway** (`ingest/`): Modbus TCP and a from-scratch DNP3 master, with
  bench simulators so it runs with no hardware.
- **SCADA HMI** (`hmi/`): a config-driven, multi-station web operator screen.
- **Scenario engine** (`suite/scenario.py`) and the **attack library** (`scenarios/`).
- **Power-flow co-simulation** (`suite/physics.py`, `config/grid.json`).
- **Dataset labeller** (`suite/dataset.py`) and **detection scorer** (`suite/score.py`).
- **Federation relay** (`suite/relay.py`).
- **Control plane** (`suite/console.py`, `suite/tase2ctl.py`) and the **launcher**
  (`tase2-suite`, `suite/launcher.py`).
- **Tests**: unit, interoperability (driven by an independent pyiec61850 stack), fuzz,
  and the engine tests. Run them with `make test`.

See [docs/concepts/components](docs/concepts/components.md) for the full tour.

## Safety

Simulation, scenario, and physics modes connect to nothing and are safe for an open
lab. Ingestion mode reaches real devices. Keep them on segmented networks, run the
hardened profile with an enforced bilateral table for any real trust boundary, and
never let a mode switch point a synthetic build at real infrastructure. This is testbed
software; do not connect it to production equipment. See [`SECURITY.md`](SECURITY.md)
and the OT safety guidance in the docs.

## License

GPL-3.0, see [`LICENSE`](LICENSE). Built on libIEC61850 (GPL-3.0).
