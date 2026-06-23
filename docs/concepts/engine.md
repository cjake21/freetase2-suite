# How the engine works

## Monitoring loop

```text
every POLL_SEC:
  for each tag:
    raw   = driver.read()              # Modbus / DNP3 / stub
    value = raw * scale + offset
    agent.WRITEQ(point, value, VALID, now)   # ICCP write with quality + time
  service control (see below)
```

If a device read fails, the gateway writes the last value with quality NOTVALID, so
the point shows bad quality instead of silently freezing. The server stores the
value, quality, and time in the point cache.

## Reporting

The server sends Block 2 InformationReports for each enabled transfer set, on the
integrity interval and on change. The bridge's subscriber agent receives them and
updates per-point value, quality, and time. A station reads ONLINE while any of its
points is VALID and recent.

## Control loop

```text
operator command (HMI or POST /api/control)
  -> bridge: OPERATE or SETPOINT on <point>_ctl   (SBO: SELECT first)
  -> server: stores Command (enforces SBO + allowlist)
  -> gateway: reads <point>_ctl, writes the command down to the device
       Modbus: coil / register / float
       DNP3:   CROB select+operate or direct operate
  -> device acts; next poll reads the new state up
  -> server reports it; HMI shows the new value
```

Because the command lives in `<point>_ctl` and the read-back lives in the
monitoring point, the gateway never overwrites a command with field data or vice
versa. Control takes a few poll cycles end to end.

## Synthetic value source: `simulateValues()`

The server has a built-in synthetic source, `simulateValues()`, that runs once per
second. For every point it rewrites the value from a synthetic function (a sine for
real points, a toggle for state points), under the model lock, in the same place
field data would otherwise land. The reporting that follows is real: genuine Block
2 InformationReport PDUs go out on the wire. Only the values are synthetic.

- **Simulation mode** (`sim-demo`) runs with `simulateValues()` on and no
  ingestion, so the node connects to nothing. It is the virtual mode for training,
  capture, and parser/IDS testing.
- **Ingestion mode** runs the server with `-n`, which disables `simulateValues()`.
  Points then change only from the gateway's writes (real field values), and the
  injection hold (`-o`) pins each written value between polls.

See {doc}`../guides/tase2-on-the-wire` for how this looks on the wire and how the
bundled simulators provide virtual devices that still produce real protocol.

## Single-threaded server

The libIEC61850 build here is single threaded. The server drives the MMS stack and
its periodic work (the synthetic source when enabled, and reporting) from one loop,
which keeps the model lock simple and the behaviour deterministic for capture and
testing.
