# Connecting physical PLCs and RTUs

This guide is the step-by-step procedure for pointing the tool at real field
devices in a testbed: which files to edit, what to put in them, and how to verify
the data is flowing and control reaches the equipment. It assumes you have built
the tools ({doc}`../installation/local`).

```{admonition} Which deployment
Use the **`physical`** deployment. It runs in ingestion mode with the bench
simulators off, so the gateway talks to your real devices. It is a template: edit
`config/scada.json` for your points and create `ingest/tags.json` pointing at your
PLCs and RTUs (this guide shows how), then start `physical` from the console. For a
real trust boundary, set its security to `hardened`.
```

```{contents}
:local:
:depth: 2
```

## What you change, at a glance

You touch three files plus your network. Nothing in the C code changes.

| File | Purpose | What you add |
|------|---------|--------------|
| `config/scada.json` | the published ICCP point model and HMI layout | one station per device, one point per signal |
| `ingest/tags.json` | the field mapping | one device per PLC/RTU, one tag per point, with addresses and scaling |
| `suite/profiles.json` | the deployment to run | an ingestion deployment using your config and tags, with the simulators off |
| network / firewall | reachability | a route to the device subnet and outbound access to the device ports |

The two configuration files meet at the point `name`: a point named in
`config/scada.json` is fed by the tag of the same `name` in the tag database.

## Step 1: inventory your devices

For every PLC and RTU, write down:

- IP address and TCP port (Modbus default 502, DNP3 default 20000).
- Protocol (Modbus TCP or DNP3) and the device address (Modbus unit id, or DNP3
  outstation address).
- The register or index map: for each value you want to publish, which register
  (Modbus) or group/variation/index (DNP3) holds it, its data type, and the scaling
  to engineering units.
- For each output you want to command: the write target (Modbus coil or holding
  register, or DNP3 CROB index) and whether it should be direct or
  select-before-operate.

This map is the single most important input. The rest is transcribing it.

## Step 2: define the published points in `config/scada.json`

Add one station per device and one point per signal. `type` is `real` for analog
or `state` for status. Add `control` to a point you will command, and optional
`hi`/`lo` operator alarm limits.

```json
{
  "domain": "TestDomain",
  "stations": [
    {
      "id": "feeder1",
      "name": "FEEDER 1 PLC",
      "points": [
        { "name": "feeder1_mw",  "type": "real",  "label": "REAL POWER", "unit": "MW", "hi": 50.0 },
        { "name": "feeder1_kv",  "type": "real",  "label": "BUS VOLTAGE", "unit": "kV", "hi": 145.0, "lo": 130.0 },
        { "name": "feeder1_brk", "type": "state", "label": "BREAKER", "unit": "",
          "states": { "0": "OPEN", "1": "CLOSED" },
          "control": { "kind": "discrete", "mode": "sbo" } }
      ]
    }
  ]
}
```

```{note}
The point model is read when the server starts. After editing `config/scada.json`,
restart the deployment for new or removed stations and points to take effect.
```

## Step 3: map points to the field in the tag database

Copy an example and edit it. For Modbus start from `ingest/tags.4plc.example.json`;
for DNP3 from `ingest/tags.dnp3.example.json`; for a mix see `ingest/tags.demo.json`.

```bash
cp ingest/tags.4plc.example.json ingest/tags.json
```

Declare each device once under `devices`, then add a tag per point. Set the device
host to the real PLC, and the registers and scaling from your inventory.

### A Modbus device

```json
{
  "devices": {
    "feeder1": { "driver": "modbus", "host": "10.20.0.11", "port": 502, "unit": 1 }
  },
  "tags": [
    { "point": "feeder1_mw", "type": "float", "device": "feeder1",
      "kind": "holding", "register": 0, "decode": "float32", "word_order": "big",
      "scale": 1.0, "offset": 0.0 },

    { "point": "feeder1_kv", "type": "float", "device": "feeder1",
      "kind": "input", "register": 100, "decode": "int16", "scale": 0.1 },

    { "point": "feeder1_brk", "type": "int", "device": "feeder1",
      "kind": "input", "register": 10, "decode": "uint16",
      "control": { "kind": "coil", "register": 0 } }
  ]
}
```

Modbus tag fields: `kind` (`holding` = function code 3, `input` = 4), `register`
(start address), `decode` (`uint16`, `int16`, `uint32`, `int32`, `float32`),
`word_order` (`big` = ABCD or `little` = CDAB, for 32-bit values), and
`scale`/`offset` (engineering value = raw * scale + offset). For a control, add
`control` with `kind` (`coil` = FC 5, `holding` = FC 6, `float32` = FC 16) and the
write `register`.

### A DNP3 device

```json
{
  "devices": {
    "rtuA": { "driver": "dnp3", "host": "10.20.0.40", "port": 20000, "outstation": 10, "master": 1 }
  },
  "tags": [
    { "point": "rtuA_mw",  "type": "float", "device": "rtuA", "group": 30, "variation": 5, "index": 0 },
    { "point": "rtuA_brk", "type": "int",   "device": "rtuA", "group": 1,  "variation": 2, "index": 0,
      "control": { "index": 0, "sbo": true } }
  ]
}
```

DNP3 tag fields: `group` (1 binary input, 30 analog input), `variation` (analog
1/2/5/6, binary 2), `index`, and for control a `control` block with the CROB
`index` and `sbo` (true for select-before-operate, false for direct operate).

### Mixing protocols

One tag database can contain Modbus and DNP3 devices at the same time; the gateway
polls them all. See {doc}`../modules/configuration` for the full field reference.

## Step 4: validate the configuration

```bash
python3 scripts/validate_config.py config/scada.json ingest/tags.json
```

Fix any reported errors before running. The validator checks that point names match
between the two files, that types and protocol fields are valid, and that
controllable points have a control mapping.

## Step 5: use the `physical` deployment

A `physical` deployment ships in `suite/profiles.json` for exactly this case:
ingestion mode, no simulators, using `config/scada.json` and `ingest/tags.json`.
Once you have created `ingest/tags.json` (step 3), it is ready to start; the
console's pre-launch checks confirm the tag database is present and the config is
valid.

```json
"physical": {
  "mode": "ingestion",
  "protocol": "Modbus / DNP3",
  "security": "insecure",
  "config": "config/scada.json",
  "tags": "ingest/tags.json",
  "http_port": 8800,
  "tase2_port": 102
}
```

To name several physical sites separately, copy this block under different names
with their own `tags` files. For a real trust boundary set `"security": "hardened"`
(step 8). You can also run it without the console:

```bash
TAGS=ingest/tags.json ./scripts/55_run_scada.sh
```

## Step 6: network and firewall

The gateway is a master: it opens connections outbound to each device. Ensure:

- the host running the tool has a route to the device subnet,
- the firewall permits outbound TCP to each device port (502 for Modbus, 20000 for
  DNP3, or your configured ports),
- each PLC is running its Modbus TCP server, and each RTU its DNP3 outstation, and
  is reachable.

See {doc}`../guides/configuration` and the OT safety notes before connecting to live
equipment, and keep the field segment isolated.

## Step 7: run and verify

Start the deployment from the console (select it, press Start) or with
`tase2ctl run myfield`. Then watch the **deployment log** (console bottom panel) and
the **HMI**:

- The gateway should print `ICCP association online; polling N tag(s)`.
- In the HMI, your stations should read ONLINE with live values and GOOD quality.
- A point that reads NOT VALID means its device read is failing. See troubleshooting.

## Step 8: secure it for a real boundary

For anything beyond a closed bench, run the hardened profile: mutual TLS (Secure
ICCP) plus a command allowlist that limits who may operate.

```bash
./scripts/gen_certs.sh        # lab certificates; for production use your own CA
```

Set `"security": "hardened"` on your deployment. See {doc}`../guides/configuration`
and the project SECURITY.md.

## Troubleshooting

- **Point reads NOT VALID / station OFFLINE.** The device read is failing. Check the
  device host, port, and unit/outstation address, that the device is reachable, and
  that the register or index exists. The deployment log names the failing tag.
- **Analog value is wrong by a factor, or garbled.** Check `decode` and `scale`. For
  a 32-bit value that looks byte-swapped, flip `word_order`.
- **A control has no effect.** Confirm the `control` write target (coil/register or
  CROB index) is correct, that the point is selected first for select-before-operate,
  and that, in the hardened profile, the operator is on the command allowlist. The
  log prints the command and the raw value written.
- **Wrong engineering units after a command.** The gateway converts the operator's
  engineering value to raw device units using the tag `scale`/`offset`. Make sure the
  read scaling and the device's expectation match.
