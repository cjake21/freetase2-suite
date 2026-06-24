# Changelog

All notable changes to this project are recorded here. The format follows
Keep a Changelog, and the project aims to follow semantic versioning.

## [Unreleased]

### Added
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
