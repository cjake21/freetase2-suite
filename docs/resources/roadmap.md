# Roadmap

## Done

- TASE.2 / ICCP server with a config-driven point model, quality, and time tags.
- Southbound ingestion over Modbus and DNP3, polling up and commanding down.
- Closed-loop control with direct operate and select-before-operate.
- Multi-station web HMI with real per-point quality.
- Reproducible build, config validator, container image, and CI.
- Interoperability gate against an independent MMS client.
- Parser hardening and a fuzz suite.
- Security profiles: insecure and hardened (mutual TLS plus command allowlist).
- Universal multi-protocol ingestion: one gateway ingests Modbus and DNP3 at the
  same time into one ICCP point model, with bench simulators for both.
- Operations Launcher (control console) and an industrial HMI/SCADA UI, with the
  documentation served from the console.

## In progress

- **Scenario engine and capture/replay.** A scriptable timeline of operator
  actions, faults, comms loss, and false-data injection, with per-scenario capture,
  so the node becomes a repeatable training and research instrument.

## Planned

- Caldera enablement: example abilities and a reference target configuration.
- One-time validation against a commercial ICCP test harness.
- Richer quality pass-through and field device timestamps where the protocol
  provides them.
- IEC 61850 MMS and IEC 60870-5-104 southbound drivers.
- Live reconfiguration of the point model without a restart.
- Redundancy and large-scale sizing.

## Direction: one tool, explicit modes

The tool grew alongside a closed lab simulator. The intent is to consolidate into a
single tool rather than maintain two code bases, because the gateway is already a
superset of the simulator (it can run on synthetic values or the stub driver for
training and capture, and on real field data for live testbeds).

The consolidation is framed as explicit operating modes surfaced in the GUI, not as
switching between branches:

- **Value source mode**: simulation (connects to nothing, for training and capture)
  or ingestion (real field data).
- **Security profile**: insecure or hardened.
- **Field protocol** per device: Modbus, DNP3, or stub.

```{warning}
The safety value of the simulator is that it connects to nothing. In a unified tool
this is preserved by making the active mode explicit, logging it at startup, showing
it in the HMI, and requiring real device configuration before the ingestion mode can
reach anything. A real-infrastructure deployment should still run as a separate,
network-segmented instance even when it is the same code base. Never let a UI toggle
silently point a synthetic build at real infrastructure or the reverse.
```
