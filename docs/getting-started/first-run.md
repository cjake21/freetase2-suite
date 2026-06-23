# First run

After {doc}`../installation/local`, start the full stack with stub devices:

```bash
./scripts/55_run_scada.sh
```

Then open `http://127.0.0.1:8800`.

What you will see:

- Four station cards. Three read ONLINE with moving values. Station `plc3` reads
  OFFLINE on purpose, because its stub is marked unreachable. This shows how comms
  status reacts to losing a device.
- On station `plc1`, the breaker is a select-before-operate control. Press SELECT,
  then the operate buttons appear with a countdown, then operate. The AVR setpoint
  is a direct control.

The launcher starts four processes and validates the configuration first:

```text
[scada] profile: INSECURE (plaintext, open command path) - for ranges/attack demos
[scada] starting TASE.2 server on 127.0.0.1:10502 (no sim)
[scada] starting ingestion gateway (tags: tags.scada-demo.json)
[scada] starting HMI bridge on http://127.0.0.1:8800
[scada] open http://127.0.0.1:8800  -  Ctrl+C to stop
```

Press `Ctrl+C` to stop everything.

## DNP3, also with no hardware

```bash
./scripts/57_run_dnp3_demo.sh
```

This starts the bundled DNP3 outstation simulator and runs the stack as a DNP3
master against it.

## Hardened profile

```bash
./scripts/gen_certs.sh
PROFILE=hardened ./scripts/55_run_scada.sh
```

This runs the node over mutual TLS with a command allowlist. See
{doc}`../guides/configuration`.
