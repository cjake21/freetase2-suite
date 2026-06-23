# Staging plan

This repository is the consolidated build, staged so the final packaged tool (a
fully wrapped GUI application) can be assembled on top of a proven base. It is the
working area for that integration. The original projects stay separate as
base-level versions.

## What is ready now

- **One tool, all capabilities.** The TASE.2 server, ICCP agent, ingestion gateway
  (Modbus and DNP3), SCADA HMI, DNP3 outstation simulator, config validator, and the
  test, fuzz, and interoperability suites are all here and working.
- **Explicit operating modes.** Simulation and ingestion, each under an insecure or
  hardened security profile, expressed as named deployments in
  `suite/profiles.json`.
- **Control plane.** `suite/tase2ctl.py` (CLI) and `suite/console.py` (web GUI)
  start, stop, and report deployments and link to the running SCADA HMI. This is the
  backend the final GUI drives.
- **Reproducible build, CI, container, and full documentation** carried over.

## Architecture for the final tool

```text
            +---------------------------+
            |  Control console (GUI)    |   suite/console.py + static/console.html
            |  pick mode, start/stop    |
            +-------------+-------------+
                          | drives
            +-------------v-------------+
            |  tase2ctl (control plane) |   suite/tase2ctl.py + profiles.json
            |  resolves a deployment    |
            +-------------+-------------+
                          | launches
   +----------------------v----------------------+
   |  server + (gateway) + bridge + SCADA HMI    |   existing, tested components
   +---------------------------------------------+
```

The final GUI is a presentation layer over `tase2ctl`. Nothing below the control
plane needs to change to wrap it.

## What is left to build the final packaged tool

1. **Package the console as the primary GUI.** Expand `suite/console.py` and its
   page into the full operator and management surface: embed or deep-link the SCADA
   HMI, add a mode and profile editor that writes `suite/profiles.json`, show live
   logs, and add device and point editors that write `config/scada.json` and the tag
   database.
2. **Single launch.** A `tase2-suite` entry point (or a desktop or Electron wrapper)
   that starts the console and opens a browser, so the whole tool is one command or
   one click.
3. **Mode safety interlocks.** A confirmation step and a clear banner when starting
   an ingestion deployment that can reach real devices, and a guard that refuses to
   start simulation against a real-device tag database.
4. **Merge the lab assets.** Fold any remaining unique pieces from
   `Free-Tase2-Server` (capture lab workflows, training scenarios) into simulation
   mode as additional deployments and docs.
5. **Scenario engine.** Wire the planned scriptable timeline (operator actions,
   faults, comms loss, false-data injection, capture and replay) into the console as
   runnable scenarios.
6. **Packaging.** Versioned releases, a published container, and hosted
   documentation.

## Notes

- The compiled binaries copied into `src/` make this repo immediately runnable; they
  are git-ignored and rebuilt by `scripts/10_build.sh`.
- Keep any real-infrastructure deployment as a separate, network-segmented instance
  even though it is the same code. See `SECURITY.md`.
