# Lab notes: tase2-plc-gateway

Running notes for the real-PLC variant of the TASE.2 publisher. The README is the
polished overview; this file is the working log of what was decided, what is built,
and what is left.

## Why this repo exists

The lab repo (Free-Tase2-Server) is a closed simulator. Its point values come
from the server's `simulateValues()` loop, and it has no real data connector and
no ingestion layer. That is the right design for capture, parser testing, and
training, but it cannot carry real plant data.

This repo keeps the same TASE.2 server, client, HMI agent, bridge, and SCADA HMI,
and adds the one thing the lab leaves out: a way to feed the publisher from real
field devices. The key idea is that TASE.2 is the northbound, center-to-center
protocol. PLCs do not speak it. They sit one layer below and talk Modbus, DNP3,
or IEC 61850 to a front-end, and the front-end publishes northbound over ICCP.
Our server is that front-end's ICCP server.

## The seam we build on

In `src/tase2_server.c`:

- Points live in the server cache as `MmsValue` objects: `g_tm1`, `g_tm2`,
  `g_ts1`, `g_ts2`, plus the `dev1` control point.
- `simulateValues()` rewrites those once a second from sine and cosine curves.
  That is the synthetic value source we are replacing.
- `writeHandler()` already accepts external writes to the same points, and the
  injection-hold flag (`-o N`) keeps a written value pinned for N seconds so it
  propagates to subscribers before the simulation would otherwise overwrite it.

So the server already has a working ingress. We do not need to modify it to get
real data flowing. We just need something on the outside that reads PLCs and
writes those points.

## Decision: Option A (reuse the ICCP write path)

Two ways to inject real data were on the table:

- Option A: an external poller that writes points over ICCP, exactly like the HMI
  bridge does today.
- Option B: a southbound thread inside the server that updates `g_tm*`/`g_ts*`
  directly.

We chose Option A to start. It needs zero changes to the server, keeps the
ingestion process isolated so it can run with least privilege, and reuses code
that already works. Option B stays open for later if write latency ever matters.

## What is built so far

- `ingest/tase2_ingest.py`: the gateway. It reads a tag database, polls each tag
  from its field device, scales the raw value into engineering units, and writes
  it to the server by driving the existing `src/tase2_hmi_agent` subprocess with
  the same `WRITEF`/`WRITEI` commands the bridge uses. It waits for the ICCP
  association to come online before polling (and aborts with a clear message if
  the server is unreachable, rather than dropping writes into a broken pipe),
  stops if the agent later exits, and flushes its logs so they survive being
  redirected to a file or journal.
- Field-device readers:
  - `modbus`: a real, dependency-free Modbus TCP client over plain sockets. Reads
    holding registers (function code 3) and input registers (function code 4),
    decodes uint16/int16/uint32/int32/float32 with selectable `word_order`
    (big/ABCD default, little/CDAB) for the 32-bit types, and pools one connection
    per device.
  - `stub`: a development stand-in that returns a fixed value. It exists only to
    test the gateway plumbing before a PLC is on the bench. It is not the server
    simulation and it is not real data.
- `ingest/tags.example.json`: the tag database format (flat form). Each entry maps
  one ICCP point to one field-device source, with type, driver, address, scaling.
- `ingest/tags.4plc.example.json`: the device-centric form. Each PLC is declared
  once under `devices` (host/port/unit) and every tag references it by name. This
  is the readable way to wire several PLCs. The gateway watches the tag file and
  reloads it live, so adding a PLC is an edit-and-save, not a restart; a malformed
  edit is logged and the running config is kept. There is no network
  auto-discovery. Devices and register maps are always declared explicitly.
  Direction note: the Modbus reader is a master/poller that connects OUT to each
  PLC, so you point the gateway AT the PLCs, not the PLCs at the gateway.
- `scripts/60_run_ingest.sh`: starts the server on loopback port 102 (with the
  injection-hold set) and runs the gateway in front of it.

## How to try it without a PLC

```bash
./scripts/10_build.sh            # builds server + agent if not already built
./scripts/60_run_ingest.sh       # uses ingest/tags.example.json
```

The first tag in the example is a stub, so `tm1` lands on the server from the
gateway with no hardware involved. To watch it arrive over ICCP, run the HMI
(`./scripts/50_run_hmi.sh`) against the same server and look at the remote view,
or capture loopback in Wireshark as the README describes.

## How to point it at a real PLC

1. Copy `ingest/tags.example.json` to `ingest/tags.json`.
2. For each point, set `driver: "modbus"`, the device `host`/`port`/`unit`, the
   `register`, `kind` (holding or input), `count`, and `decode`, plus `scale` and
   `offset` to get engineering units.
3. Run `TAGS=ingest/tags.json ./scripts/60_run_ingest.sh`.

Keep this on a segmented network. The gateway only reads from devices today, which
is the safer direction. See the OT safety section in the README before connecting
to anything live.

## Config-driven point model + multi-station HMI (done)

The fixed `tm1/tm2/ts1/ts2` set is gone. `config/scada.json` is the single source
of truth for the published points and the HMI layout (stations -> points, each with
name/type/label/unit). From it:

- `scripts/gen_server_points.py` flattens it to a `name type` list the server reads
  with `-P`; the server (`src/tase2_server.c`) builds a RealQ/StateQ point per line
  dynamically instead of the four hard-coded globals. New `-n` flag disables the
  synthetic source so points move only from ingestion (real mode).
- `src/tase2_hmi_agent.c` now takes the point set on `SUBSCRIBE`/`SNAPSHOT`, so the
  data set and report mapping are dynamic.
- `hmi/bridge.py` reads the config, subscribes to the configured points, groups by
  station, and derives per-PLC comms from data freshness (a station is ONLINE while
  any of its points keeps updating). It exposes a `stations` list to the HMI.
- `hmi/static/*` is a dynamic, read-only station grid: one card per PLC, each point
  with value/unit and a GOOD/STALE quality tag, plus a global alarm strip and event
  log. Adding a station in the config grows the grid with no code change.
- `scripts/55_run_scada.sh` runs the whole stack (server `-n -P`, ingest, bridge);
  `ingest/tags.scada-demo.json` is an all-stub demo where plc3 is `down` so it reads
  OFFLINE, proving the freshness/comms indicator. `scripts/50_run_hmi.sh` runs the
  HMI over simulated values (server sim on, no ingest).

Comms semantics: live status (how many PLCs are currently connected) updates
continuously; the station count is config-driven and a new station takes effect on
the next stack start (the ICCP point model is fixed per association).

## Closed-loop control HMI -> PLC (done)

Commands now flow downstream as well, and the path never fights the monitoring
ingest because command and read-back are separate objects.

- A point gets a `control` block in `config/scada.json` (kind discrete or
  setpoint). The server then publishes a Block 5 device control object
  `<name>_ctl` { Command, Tag, Status } alongside the monitoring point.
- `src/tase2_hmi_agent.c` got generalized `OPERATE <device> <int> [tag]` and
  `SETPOINT <device> <float> [tag]`. The server's writeHandler stores the operated
  Command into the control cell so it is readable.
- `hmi/bridge.py` exposes controllable points and turns an HMI command into an
  operate on `<name>_ctl`. The HMI shows state buttons for discrete points and a
  setpoint box for setpoint points.
- `ingest/tase2_ingest.py` got Modbus writes (FC5 coil, FC6 register, FC16 float,
  the latter honouring word_order) and a control loop: each poll it reads
  `<name>_ctl` over ICCP (non-blocking, using the previous poll's response to
  avoid stalling under write load) and, when the command changes, writes it down to
  the PLC register from the tag's `control` block. The first observed command is
  taken as a baseline so startup does not force a command.
- Demo: stub controllable points loop back through `STUB_STATE`, so on the bench a
  CLOSE or a setpoint shows up on the read-back with no hardware.

The Modbus reader's MBAP framing was also fixed while adding writes (the old
read path miscounted header bytes and was never exercised without a real PLC).

## Production features added (done)

- Quality + time tags end to end. Indication points are now
  { Value, Flags, TimeStamp }. The gateway sends WRITEQ (Value + TASE.2 quality
  byte + Unix seconds): VALID on a good read with the acquisition time, NOTVALID
  holding the last value when a device read fails. The agent emits per-point q and
  t in reports, and the bridge/HMI show real validity and age instead of inferred
  freshness. Quality bits follow IEC 60870-6-802 / libtase2 (validity bits 2-3,
  current source bits 4-5, normal value bit 6, time stamp quality bit 7).
- Select-before-operate. A point's control can be mode "sbo". The server enforces
  select then operate: only the selecting connection may operate, within a 30 s
  timeout; a stray operate returns object-access-denied. The agent has SELECT and
  CANCEL, the bridge does the two-step and tracks an arm timer, and the HMI shows
  SELECT then armed operate + CANCEL with a countdown. Control object is now
  { Command, Tag, Status, SBO }.
- DNP3 master. `ingest/dnp3.py` is a stdlib DNP3 master (data link framing,
  CRC-16/DNP, transport, application READ/SELECT/OPERATE/DIRECT_OPERATE), reading
  binary input (group 1) and analog input (group 30), and operating a CROB
  (group 12). `Dnp3Reader` in the gateway uses it; control points operate a CROB
  by SBO or direct. `ingest/dnp3_outstation_sim.py` is a bench outstation, and
  `scripts/57_run_dnp3_demo.sh` runs the whole stack over DNP3 with no hardware.
- Tests: `tests/test_ingest.py` (CRC vector, frame round trip, Modbus decodes,
  config resolution, and a live DNP3 master to outstation round trip).

## Still to do

- Live reconfiguration of the server point model (adding a station currently needs a
  stack restart).
- Pass through richer device quality bit for bit, and field device timestamps where
  the protocol provides them.
- IEC 61850 MMS reader, and the fuller IEC 60870-6-802 type catalogue for broad
  commercial ICCP interop.
- Add DNP3 and IEC 61850 readers (registered alongside `modbus` in `DRIVERS`).
- Tighten the value encodings toward the full IEC 60870-6-802 type catalogue for
  interop with commercial ICCP centers.
- Harden the ICCP boundary: bilateral-table enforcement, peer authentication, and
  real TLS material.
