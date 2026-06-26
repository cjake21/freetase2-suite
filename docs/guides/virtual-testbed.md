# Adding virtual PLCs and RTUs

This guide covers running the tool with no hardware: adding virtual stations and
devices, and adjusting the simulated values they produce. Use it for training,
capture, parser and IDS testing, and for building out a scenario before real
equipment is available. The companion guide for real devices is
{doc}`physical-testbed`.

```{admonition} Which deployment
Use **`testbed-demo`** for a simulated testbed with virtual devices that emit real
Modbus and DNP3 (it starts the bundled simulators). Edit `config/scada.json`,
`ingest/tags.demo.json`, and the simulator value tables. Use **`sim-demo`** for
purely synthetic values with no devices and no field protocol (edit
`config/scada.json` only). `field-hardened` is `testbed-demo` over mutual TLS.
```

```{contents}
:local:
:depth: 2
```

## Three ways to be virtual

There are three levels of "virtual", from purely synthetic to virtual devices that
emit real field protocol. Pick per station, or mix them.

| Approach | Field protocol on the wire | Files you edit | Rebuild? |
|----------|----------------------------|----------------|----------|
| A. Simulation mode (`simulateValues`) | none (the server invents values) | `config/scada.json`; `src/tase2_server.c` to change the curves | rebuild only to change the curves |
| B. Virtual devices via bench simulators | real Modbus and/or DNP3 | `config/scada.json`, the tag database, the simulator's value table | no (restart the simulator) |
| C. Stub points | none (in memory) | `config/scada.json`, the tag database | no (the tag file live-reloads) |

In all cases the northbound TASE.2 / ICCP traffic is real. See
{doc}`tase2-on-the-wire`.

## A. Simulation mode: synthetic values for every point

In simulation mode the server runs `simulateValues()` once a second and rewrites
every published point from a synthetic function. There is no gateway and no device;
the node connects to nothing. This is the `sim-demo` deployment.

### Add virtual stations and points

Edit `config/scada.json` exactly as you would for a real device (see
{doc}`physical-testbed` step 2): add a station and its points. You do not add
anything to a tag database, because there is no ingestion in simulation mode. On
the next start of `sim-demo`, the new points appear in the HMI, driven by the
synthetic source.

### Adjust the simulated values

The synthetic curves live in `simulateValues()` in `src/tase2_server.c`:

```c
if (g_points[i].isReal)
    MmsValue_setFloat(el, (float)(11.0 + 5.0 * sin((t + i * 3.0) / 5.0)));
else
    MmsValue_setInt32(el, ((int)(t / (i + 1)) % 2));
```

Change the base (`11.0`), amplitude (`5.0`), or period (`/ 5.0`) for analog points,
or the toggle rate for state points, then rebuild:

```bash
make -C src LIB61850_HOME="$PWD/deps/libiec61850"
```

```{note}
Simulation mode drives all points with the same generic shape (offset per point by
index). When you need specific or independently controllable virtual values, use
approach B or C below, which need no C rebuild.
```

## B. Virtual devices that emit real Modbus or DNP3

Here the ingestion gateway talks to a bundled simulator over the real field
protocol, so captures show genuine Modbus or DNP3 frames. This is what `testbed-demo`
uses. Adding a virtual device is the same as adding a real one, except the device
host points at the local simulator and you enable that simulator.

### Add a virtual Modbus device

1. In `config/scada.json`, add the station and points.
2. In the tag database (for example `ingest/tags.demo.json`), point a device at the
   Modbus simulator and add tags. Telemetry registers must be in the simulator's
   moving range (100 and up); control registers below 100 are stored and read back.
   ```json
   "devices": {
     "mb": { "driver": "modbus", "host": "127.0.0.1", "port": 1502, "unit": 1 }
   },
   "tags": [
     { "point": "plc5_mw",  "type": "float", "device": "mb", "kind": "input",   "register": 108, "decode": "int16", "scale": 0.1 },
     { "point": "plc5_brk", "type": "int",   "device": "mb", "kind": "holding", "register": 8,   "decode": "uint16",
       "control": { "kind": "holding", "register": 8 } }
   ]
   ```
3. Make the new telemetry register move by adding it to the simulator's value table
   in `ingest/modbus_outstation_sim.py`:
   ```python
   TELEMETRY = {
       100: (138, 22),    # existing
       102: (1382, 10),
       104: (92, 16),
       106: (1361, 9),
       108: (205, 30),    # new: ~20.5 MW (register value) -> scale 0.1 -> ~20.5 MW
   }
   ```
   Each entry is `register: (base value, amplitude)`; the simulator returns
   `base + amplitude * sin(t)`. The control register (8 here) needs no entry; it
   stores what is written and reads it back, so a command round-trips.
4. Ensure the deployment starts the Modbus simulator: include `"modbus"` in its
   `sims` list in `suite/profiles.json` (or set `MODBUS_SIM=1` when running
   `scripts/55_run_scada.sh`).

### Add a virtual DNP3 device

1. Add the station and points in `config/scada.json`.
2. Point a device at the DNP3 simulator and add tags:
   ```json
   "devices": {
     "rtu": { "driver": "dnp3", "host": "127.0.0.1", "port": 20000, "outstation": 10 }
   },
   "tags": [
     { "point": "rtu2_mw",  "type": "float", "device": "rtu", "group": 30, "variation": 5, "index": 2 },
     { "point": "rtu2_brk", "type": "int",   "device": "rtu", "group": 1,  "variation": 2, "index": 1,
       "control": { "index": 1, "sbo": true } }
   ]
   ```
3. Make the new analog index move by editing `analog_value()` in
   `ingest/dnp3_outstation_sim.py`:
   ```python
   def analog_value(self, idx):
       t = time.time() - self.t0
       if idx == 0: return 13.8 + 1.5 * math.sin(t / 3.0)
       if idx == 1: return 138.0 + 0.5 * math.cos(t / 5.0)
       if idx == 2: return 22.0 + 2.0 * math.sin(t / 4.0)   # new channel
       return self.analog.get(idx, 0.0)
   ```
   Binary inputs default to 0 and follow CROB operates, so a control read-back works
   without extra edits.
4. Ensure the deployment starts the DNP3 simulator: include `"dnp3"` in its `sims`
   list (or set `DNP3_SIM=1`).

### Adjust the simulated values

For virtual devices you adjust values by editing the simulator value tables above
(`TELEMETRY` for Modbus, `analog_value()` for DNP3). These are plain Python and need
no C rebuild; just restart the deployment (or the simulator). This is the
recommended way to shape virtual readings, because each channel is independent and
controls round-trip.

## C. Stub points: in-memory virtual values, no protocol

The `stub` driver fakes a value entirely in the gateway, with no protocol traffic.
It is the quickest way to add a virtual point and to script a degraded device.

1. Add the station and points in `config/scada.json`.
2. Add stub tags:
   ```json
   { "point": "plc6_mw",  "type": "float", "driver": "stub", "value": 13.8, "jitter": 1.5 },
   { "point": "plc6_brk", "type": "int",   "driver": "stub", "value": 0, "control": {} },
   { "point": "plc6_t",   "type": "float", "driver": "stub", "value": 60.0, "down": true }
   ```
   - `value` is the base reading.
   - `jitter` adds `jitter * sin(t)` so the value moves (and the station reads
     ONLINE on the freshness check).
   - `down: true` makes every read fail, so the point reads NOT VALID and its
     station goes OFFLINE. Use it to simulate a lost device.
   - `control: {}` makes a stub point commandable; the command loops back in memory,
     so the read-back reflects the operator command.
3. The tag database live-reloads, so changes to stub `value`/`jitter` take effect on
   the next poll without restarting. Adding a whole new station still needs a restart
   (the point model is read at start).

## Worked example: add a virtual Modbus PLC and a virtual DNP3 RTU

Add two stations to `config/scada.json`:

```json
{ "id": "plc5", "name": "VIRTUAL FEEDER 5",
  "points": [
    { "name": "plc5_mw",  "type": "real",  "label": "FLOW", "unit": "MW" },
    { "name": "plc5_brk", "type": "state", "label": "BREAKER",
      "states": { "0": "OPEN", "1": "CLOSED" },
      "control": { "kind": "discrete", "mode": "direct" } }
  ] },
{ "id": "rtu2", "name": "VIRTUAL RTU 2",
  "points": [
    { "name": "rtu2_mw",  "type": "real", "label": "FLOW", "unit": "MW" }
  ] }
```

Add their tags to `ingest/tags.demo.json` (the Modbus and DNP3 blocks shown in
sections B above), add the new simulator value-table entries, then validate and run:

```bash
python3 scripts/validate_config.py config/scada.json ingest/tags.demo.json
python3 suite/tase2ctl.py run testbed-demo      # testbed-demo already starts both simulators
```

Both new stations appear in the HMI with live values, and `plc5_brk` is operable.

## Validate and run

Always validate after editing, then start the deployment:

```bash
python3 scripts/validate_config.py config/scada.json ingest/<your tags>.json
python3 suite/console.py        # start the deployment from the console
```

See {doc}`../modules/configuration` for the complete tag field reference and
{doc}`operations` for driving the result.
