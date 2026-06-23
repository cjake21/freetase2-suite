# What the tool is

`tase2-plc-gateway` is a complete, self-contained TASE.2 / ICCP node built for
testbeds. TASE.2 (IEC 60870-6) is the inter-control-center protocol used between
utilities and control centers. PLCs and RTUs do not speak it. They speak field
protocols such as Modbus and DNP3, and a front end publishes their data northbound
over TASE.2. This tool is that front end, plus everything you need to drive and
observe it.

It includes:

- A **TASE.2 / ICCP server** built on the libIEC61850 MMS engine. It publishes a
  configurable point model with quality and time tags, sends Block 2 reports, and
  accepts Block 5 device control including select-before-operate.
- A **southbound ingestion gateway** that polls field devices over Modbus TCP or
  DNP3, writes their values up into the server, and carries operator commands back
  down to the device.
- A **web SCADA HMI** that renders one station card per device, shows live values
  with real per-point quality, and issues controls.
- A **DNP3 outstation simulator** and stub drivers so the whole pipeline runs with
  no hardware.
- **Security profiles**, a **config validator**, a **test and fuzz suite**, CI, and
  a container image.

```{note}
Everything is standard library on the Python side and libIEC61850 on the C side.
There are no paid components. Comparable TASE.2 stacks are commercial and closed.
```

## What it is not

- It is not a production ICCP gateway certified for a regulated boundary. See
  {doc}`../resources/roadmap` and the limitations in {doc}`architecture`.
- It is not a full IEC 60870-6-802 type catalogue implementation. It implements the
  common indication and control objects with quality and time tags.
