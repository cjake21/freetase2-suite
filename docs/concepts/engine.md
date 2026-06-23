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

## Single-threaded server

The libIEC61850 build here is single threaded. The server drives the MMS stack and
its periodic work (simulation if enabled, reporting) from one loop, which keeps the
model lock simple and the behaviour deterministic for capture and testing.
