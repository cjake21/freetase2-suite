# Changelog

All notable changes to this project are recorded here. The format follows
Keep a Changelog, and the project aims to follow semantic versioning.

## [Unreleased]

### Added
- Run any attack on either of two environments: scenarios now reference their points
  by role (the tie breaker, the tie flow, a bus voltage, a feeder breaker, the
  communications station) instead of by a fixed point name, and an environment
  (`config/environments.json`) maps each role to a real point and supplies the point
  model and the power-flow grid. Two environments ship: `simple` (the four-bus lab)
  and `realistic` (the regional `grid-demo` grid), both tuned so the scripted tie
  opening tips into a cascade. The scenario engine resolves roles at run time
  (`suite/scenario.py` `--env`), `tase2ctl run <attack> --env <name>` selects the
  environment, and each Attack Scenario in the control console has an environment
  dropdown next to its Start button. See `docs/guides/attacks` and
  `docs/guides/scenarios`.
- Utility-scale physics: a new `grid-demo` deployment drives a moderate regional
  grid (`config/grid_utility.json`, ~110 ICCP points) with the full telemetry
  taxonomy a real inter-control-centre feed carries (MW and MVAR, bus kV, system
  frequency, MWh accumulators, transformer taps and oil temperatures, tie schedules
  and Area Control Error). Point writes are batched so the HMI stays online at this
  scale. Regenerate the model with `scripts/gen_utility_model.py`. The demo list is
  consolidated to `sim-demo`, `testbed-demo` (renamed from `field-demo`), and
  `grid-demo`; the small four-bus `physics-demo` is removed, and its scripted cascade
  lives on in `cascade-demo`.
- Single launch and packaging: `tase2-suite` (and `suite/launcher.py`) is one
  command that checks the native build, starts the control console, and opens it in a
  browser. `pyproject.toml` adds project metadata and the `tase2-suite` console
  script, a root `Makefile` wraps the common tasks (build, run, test, docs, docker),
  and the container image now runs the console by default, so `docker run -p 8080:8080
  -p 8800:8800 freetase2-suite` is the whole tool in a box. Tests in
  `tests/test_launcher.py`.
- Attack library and dual-association traffic: built-in, multi-stage grid attacks
  grounded in real incidents and MITRE ATT&CK for ICS, written to resemble real
  intrusion traffic for detection building. The scenario engine gained a second
  `"attacker"` association (recon, false data, commands, and floods come from a
  separate peer, not the trusted feed), a `scan` action (MMS reads, discovery and
  collection), and a `flood` action (denial of service). Scenarios:
  `ukraine2015_blackout` (2015 Sandworm/BlackEnergy), `industroyer_sweep` (2016
  Industroyer/CRASHOVERRIDE), `stealthy_false_data` (manipulation of view and alarm
  suppression), and `recon_collection`, each physics-backed, with deployments
  `ukraine2015-attack`, `industroyer-attack`, `stealthy-attack`, `recon-attack`. The
  technique catalog (`suite/dataset.py`) and the labeller recognise the full set.
  Tests in `tests/test_scenario.py`. See `docs/guides/attacks`.
- Multi-control-center federation: `suite/relay.py` is an inter-control-center tie.
  For each link in `config/federation.json` it subscribes to the source center and
  mirrors the agreed points into the destination center over real ICCP, so a partner
  sees another center's data without measuring it locally. It connects as an ordinary
  peer, so a bilateral table on the source scopes what the tie carries. New partner
  model `config/scada_b.json`, launcher `scripts/61_run_federation.sh`, deployment
  `federation-demo`, and tests in `tests/test_relay.py`. See `docs/guides/federation`.
- Physics-backed scenarios: a scenario may name a `grid`, and then the power-flow
  co-simulation is the value source underneath the script (`suite/scenario.py` now
  reuses `suite/physics.py`). A scripted breaker `operate` switches a real line so
  the grid cascades, an `inject` pins its point over the physics so a spoof keeps
  lying while the grid collapses, and cascade trips are recorded as labelled
  consequences. New scenario `scenarios/fdi_cascade.json` and deployment
  `cascade-demo`. Tests in `tests/test_scenario.py`. See `docs/guides/scenarios`.
- Enforced bilateral table (`-B file`, `config/blt.conf`, the `field-federated`
  deployment): the server scopes each peer (by IP) to the objects it may read,
  control, and inject. Reads, operates, value injections, and Block 2 report members
  outside a peer's rule are denied or withheld, with default-deny for unknown peers
  and the handshake objects always readable. This is the per-peer data scoping the
  published table describes, turning a documented limitation into an enforced
  control. Verified against the independent pyiec61850 stack in `tests/test_blt.py`.
  See `docs/guides/federation` and `SECURITY.md`.
- Physics mode: a DC power-flow co-simulation (`suite/physics.py`, `config/grid.json`,
  the `physics-demo` deployment, launcher `scripts/57_run_physics.sh`) is the value
  source. It solves a grid model each tick, maps line flows and bus quantities onto
  the points, and reads breaker controls so an operator or attacker breaker command
  redistributes flow and overloaded lines cascade one at a time. The solver is plain
  Python (no numerical libraries), so the suite stays standard library only. Voltage
  magnitude is a documented approximation. Tests in `tests/test_physics.py`. See
  `docs/guides/physics`.
- Detection scoring: `suite/score.py` grades a sensor against a scenario's ground
  truth. It reads Suricata eve.json or a generic JSON-lines alert feed, matches
  alerts to the malicious intervals, and reports recall per MITRE ATT&CK for ICS
  technique, mean time to detect, and the false-positive rate, with a JSON
  scorecard and a markdown report. Starter Suricata rules and a runnable example
  live in `detect/`, the helper `scripts/59_score.sh` runs Suricata and grades it,
  and tests are in `tests/test_score.py`. See `docs/guides/scoring`.
- Labelled datasets: `suite/dataset.py` joins a packet capture of a scenario run
  with that scenario's ground-truth timeline, by timestamp, and writes a labelled
  dataset (per-window flow features including a TASE.2/MMS PDU count, benign or
  malicious labels with technique tags, a deterministic train/test split, and a
  manifest). Standard-library pcap reader, so no capture or parsing packages.
  Orchestrator `scripts/58_run_dataset.sh` captures and labels in one step, with
  tests in `tests/test_dataset.py`. See `docs/guides/datasets`.
- Scenario mode: a deterministic, seeded timeline (`suite/scenario.py`,
  `scenarios/*.json`) is the value source, with the server simulation off and no
  gateway. It seeds and heartbeats every point, plays a timeline of operations,
  attacks, and faults as real ICCP traffic, and writes a ground-truth label
  timeline (benign or malicious, with a technique tag) for datasets and detection
  scoring. New deployment `scenario-demo`, launcher `scripts/56_run_scenario.sh`,
  and tests in `tests/test_scenario.py`. See `docs/guides/scenarios`.
- Documentation portal under `docs/` (Sphinx + sphinx_rtd_theme + MyST Markdown):
  Overview, Installation, Getting Started, Core Concepts, Usage Guides, Module
  documentation, API Reference, Developer Information, and Resources. Built in CI
  with warnings treated as errors. Build locally with `make -C docs html`.
- Security profiles: `insecure` (default, for ranges and attack demos) and
  `hardened` (mutual TLS / Secure ICCP plus a loopback command allowlist), selected
  by `PROFILE` on the launch scripts. `scripts/gen_certs.sh` generates lab CA,
  server, and client certificates. The client agent now speaks TLS when
  `TASE2_TLS=1`. See `SECURITY.md`.
- Server command allowlist (`-L ip[,ip...]`): gates the command and injection
  direction by peer IP; reads and subscription stay open.
- Parser hardening across every untrusted-byte surface (DNP3 master and
  outstation, Modbus, and the HMI control API) so malformed input fails cleanly.
- Fuzz harness (`tests/test_fuzz.py`): random, truncated, and mutated inputs to the
  parsers, plus a live flood of the control API.

### Changed
- Control console: deployments are grouped by purpose (Demos & Training, Attack
  Scenarios, Defense & Federation, Physical Testbed), each with a one-line "use when"
  hint, and a Data & Detection panel surfaces the capture, label, and scoring tools,
  so a user can find what to run quickly. Deployments carry `category` and `use_when`
  in `suite/profiles.json`, and the console shows the correct launcher per mode.

### Added (Sprint 1)
- Reproducible build: libIEC61850 pinned to v1.6.1 and mbedtls to 3.6.0 in
  `scripts/10_build.sh` (override with `LIB61850_REF` / `MBEDTLS_VER`).
- Interop gate (`tests/test_interop.py`): drives the server with an independent
  MMS client stack (pyiec61850) to prove association, Block 1 reads, Block 2
  transfer-set configuration, and Block 5 select-before-operate including its
  enforcement.
- Config validator (`scripts/validate_config.py`) with human-readable errors,
  run by the launch scripts before starting.
- Container image (`Dockerfile`) and continuous integration
  (`.github/workflows/ci.yml`): build, unit tests, interop gate, headless smoke.
- Self-test entry point `scripts/70_selftest.sh`.

### Fixed
- Transfer-set attributes (DataSetName, Status, Interval, DSConditionsRequested)
  are now mirrored into the readable cache, so a client that writes then reads a
  transfer-set attribute sees the value it set (found by the interop gate).

## [0.1.0] - prior work

### Added
- TASE.2 / ICCP server on libIEC61850 MMS with a configurable point model
  (`config/scada.json`), per-point quality and time tags, and Block 2 reporting.
- Southbound ingestion gateway (`ingest/tase2_ingest.py`): Modbus TCP and DNP3
  masters, polling up and writing commands down, with a device-centric tag
  database and live reload.
- Closed-loop control: HMI command to TASE.2 Block 5 control object to field
  device and back, with direct operate and select-before-operate.
- Multi-station web HMI driven by config, with real per-PLC comms from field
  quality.
- DNP3 master (`ingest/dnp3.py`) and a bench outstation simulator
  (`ingest/dnp3_outstation_sim.py`).
- Unit tests (`tests/test_ingest.py`).
