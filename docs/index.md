# FreeTASE2 Suite

An open TASE.2 / ICCP (IEC 60870-6) publishing node for power and OT security
testbeds. It reads live data from PLCs and RTUs over Modbus or DNP3, publishes it
northbound as real TASE.2 traffic with per-point quality and time tags, shows it
on a SCADA style web HMI, and sends operator commands back down to the field with
direct operate or select-before-operate. It runs both as a soft target for attack
demonstrations and as a hardened, mutual-TLS node for testing defenses.

This portal is the reference documentation for installing, operating, configuring,
extending, and developing the tool.

```{toctree}
:maxdepth: 2
:caption: Documentation

overview/index
installation/index
getting-started/index
concepts/index
guides/index
modules/index
api/index
developer/index
resources/index
```

```{note}
This is testbed software. Do not connect it to production equipment. Read the
security model in {doc}`overview/architecture` and {doc}`guides/configuration`
before connecting anything live.
```
