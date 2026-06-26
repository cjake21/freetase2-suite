# First run

The simplest way to run the tool is the Operations Launcher (the control console).
After {doc}`../installation/local`:

```bash
python3 suite/console.py        # open http://127.0.0.1:8080
```

In the console, select the **field-demo** deployment, review its pre-launch checks,
and press **Start**. Then open its SCADA HMI from the top-bar link (or
`http://127.0.0.1:8800`). See {doc}`../guides/operations` for the full launcher and
HMI walkthrough.

## What the field-demo runs

`field-demo` is the universal multi-protocol demo. One gateway ingests Modbus and
DNP3 at the same time, using the bundled Modbus slave simulator and DNP3 outstation
simulator, so it runs with no hardware:

- `plc1` and `plc2` are read over **Modbus** (live, moving values).
- `rtu1` is read over **DNP3** (live).
- `plc3` points at an unreachable device, so it reads **OFFLINE / NOT VALID**, which
  shows how comms and quality degrade.
- On `plc1`, the breaker is a select-before-operate control (press SELECT, then
  operate within the countdown) and the AVR is a direct setpoint.

## Running without the launcher

You can also start the same deployment directly:

```bash
python3 suite/tase2ctl.py run field-demo
# or, the underlying launcher with the bench simulators:
MODBUS_SIM=1 DNP3_SIM=1 TAGS=ingest/tags.demo.json ./scripts/55_run_scada.sh
```

The launcher validates the configuration and prints its startup, for example:

```text
[scada] profile: INSECURE (plaintext, open command path) - for ranges/attack demos
[scada] starting Modbus slave simulator on :1502
[scada] starting DNP3 outstation simulator on :20000
[scada] starting TASE.2 server on 127.0.0.1:102 (no sim)
[scada] starting ingestion gateway (tags: tags.demo.json)
[scada] starting HMI bridge on http://127.0.0.1:8800
```

Press `Ctrl+C` (or STOP ALL in the console) to stop everything.

## Pure simulation, no devices at all

The `sim-demo` deployment publishes synthetic values with `simulateValues()` and no
ingestion (connects to nothing), for capture and training. See
{doc}`../guides/tase2-on-the-wire`.

## Hardened profile

```bash
./scripts/gen_certs.sh
```

Then start the **field-hardened** deployment in the console (or
`PROFILE=hardened python3 suite/tase2ctl.py run field-hardened`). It runs the node
over mutual TLS with a command allowlist. See {doc}`../guides/configuration`.
