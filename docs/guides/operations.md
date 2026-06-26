# Operating the launcher and the HMI

This guide walks an operator through the two layers of the tool: the Operations
Launcher (the control console, where you choose and run a deployment) and the
ICCP/SCADA HMI (the live operational screen, where you monitor points and issue
controls). It also points to {doc}`tase2-on-the-wire`, which explains how each
action becomes real TASE.2 traffic.

```{contents}
:local:
:depth: 2
```

## Two layers: launcher state vs runtime state

There are two independent states, and the console keeps them visually separate:

- **Launcher state** is the console itself. While the console process is up, the
  top bar shows `LAUNCHER: ONLINE`. The launcher does not move data; it starts and
  stops deployments.
- **Runtime state** is the deployment process group (server, gateway, bridge, and
  any simulators). The top bar shows `RUNTIME: RUNNING` or `STOPPED`. This is the
  thing that actually publishes TASE.2 and talks to devices.

A deployment can be stopped while the launcher stays online. That separation is
deliberate: you always have a control surface, even when nothing is running.

## The Operations Launcher

### Start the console

```bash
python3 suite/console.py        # http://127.0.0.1:8080
```

### Read the top bar

| Field | Meaning |
|-------|---------|
| LAUNCHER | the console process (ONLINE while it runs) |
| RUNTIME | the deployment process state (RUNNING green / STOPPED gray); RUNNING is shown amber when the active profile is insecure |
| ACTIVE DEPLOYMENT | which deployment is running |
| PROTOCOL / MODE | southbound protocol and operating mode of the running deployment |
| SECURITY PROFILE | insecure (amber) or hardened (green) |
| SYSTEM TIME | local clock |
| STOP ALL | terminates the running deployment (enabled only while one runs) |

### Deployments and when to use them

The shipped deployments map to specific jobs, and the control console groups them by
purpose so you can find the right one quickly. Pick the one that matches what you are
doing; the two setup guides ({doc}`physical-testbed`, {doc}`virtual-testbed`) tell
you which files to edit for each.

**Demos and training** (see it work; nothing scripted):

| Deployment | Use it for | Mode | Profile |
|------------|-----------|------|---------|
| `sim-demo` | Fastest look at the HMI; synthetic data, connects to nothing; minimal wire traffic, good for tool-based interaction | simulation | insecure |
| `testbed-demo` | The full real pipeline: Modbus and DNP3 ingested into ICCP; a small lab testbed | ingestion | insecure |
| `grid-demo` | Rich, utility-realistic ICCP traffic from a regional grid model | physics | insecure |

**Attack scenarios** (scripted, repeatable; these feed the dataset and scoring tools):

| Deployment | Use it for | Mode | Profile |
|------------|-----------|------|---------|
| `scenario-demo` | A scripted run of injection, spoof, unauthorized command, and comms loss | scenario | insecure |
| `cascade-demo` | A scripted attack on a live grid that triggers a real cascading blackout | scenario + physics | insecure |

**Defense and federation** (hardening and per-peer scoping):

| Deployment | Use it for | Mode | Profile |
|------------|-----------|------|---------|
| `field-hardened` | Mutual TLS plus a command allowlist; test your defenses | ingestion | hardened |
| `field-federated` | An enforced bilateral table: per-peer read and control scoping | ingestion | insecure |

**Physical testbed**:

| Deployment | Use it for | Mode | Profile |
|------------|-----------|------|---------|
| `physical` | Real PLCs and RTUs (no simulators) | ingestion | insecure (set hardened for a real boundary) |

After a run, two terminal tools turn it into evidence: `scripts/58_run_dataset.sh`
captures and labels a dataset, and `scripts/59_score.sh` grades a detector against
the ground truth. See {doc}`datasets` and {doc}`scoring`.

Which one when:

- **Building or running a physical testbed, or adding a real PLC or RTU**: use
  `physical`. Edit `config/scada.json` and `ingest/tags.json`. See
  {doc}`physical-testbed`.
- **Running a simulated testbed, or adding a virtual device**: use `testbed-demo`
  (virtual devices with real protocol; edit `config/scada.json`,
  `ingest/tags.demo.json`, and the simulator value tables) or `sim-demo` (pure
  synthetic values; edit `config/scada.json` only). See {doc}`virtual-testbed`.

### Select a deployment and read the pre-launch checks

The left **Deployment Control** list is your control station. Each row shows the
deployment name, protocol, mode, security profile (color coded), config file, and
port bindings, with Start / Stop / Logs.

Click a row to load the right **detail panel**, which shows before you launch:

- the launch command and the script it runs,
- the config path, tag database, and port bindings,
- the last run time,
- **pre-launch health checks**: tools built, point model present, tag database
  present, configuration valid, TLS certificates present (hardened), docs built. A
  green square means ready; red means fix it first.
- **warnings before launch**: for example that an insecure profile has an open
  command path, or that ingestion mode reaches real devices.

```{warning}
Read the warnings. In ingestion mode the gateway will connect to whatever the tag
database points at. On a real network, confirm segmentation and the security
profile before pressing Start.
```

### Start and stop

Press **Start** on a deployment row. The console launches the deployment process
group and the top bar flips to RUNNING. Only one deployment runs at a time
(deployments share ports by default), so Start is disabled on the others while one
is running.

Press **Stop** on the running row, or **STOP ALL** in the top bar, to terminate the
whole process group (server, gateway, bridge, simulators).

### Watch the deployment log

The bottom **Deployment Log** panel tails the live output of the running
deployment. Use the **System / Runtime / Error** filters:

- **System**: launcher and startup lines (`[scada] ...`).
- **Runtime**: server, gateway, and bridge lines (`[ingest] ...`, `[tase2] ...`).
- **Error**: lines matching error, fail, denied, reject.

This is where you confirm the ICCP association came online, that the gateway is
polling, and that commands were pushed to devices.

### Open the SCADA HMI and the documentation

When a deployment is running, the **SCADA HMI** link in the top bar opens that
deployment's HMI. The **DOCS** link opens this documentation.

## The SCADA HMI

Open it from the launcher, or directly at `http://127.0.0.1:8800`.

### Status strip

The top strip is your at-a-glance operational summary:

| Field | Meaning |
|-------|---------|
| LINK | ICCP link: NORMAL (all stations online), DEGRADED (some), NO DATA, or OFFLINE |
| STATIONS | online / total |
| SCAN | RUN when reports are flowing, else HOLD |
| CRIT / WARN | active alarm counts by severity |
| LAST RPT | time of the last Block 2 report received |
| UTC | clock |

### Points table

The left table is the live point list across all stations. Columns: station,
point, description, value, quality, control type, and age. The table is sortable
(click a header) and filterable (search box, station selector, quality filter,
controllable-only).

Operational state is high contrast:

- A row carries a left **severity accent**: red for critical (NOT VALID or over an
  alarm limit), amber for warning, gray for stale/no-data.
- The **value** is colored by state, and the **quality** column shows GOOD,
  SUSPECT, HELD, NOT VALID, or STALE in the matching color.
- The **age** is how long since the field time tag; a growing age on a point that
  should be live is a sign of trouble.

### Point detail and evidence panel

Click a row to open the right **detail panel**. It is the evidence view for one
point: station and comms state, type, value, quality, the field time tag and age,
and the control configuration. For a controllable point it also shows the operator
controls.

### Issuing controls

Controls appear in the detail panel only for controllable points. There are three
forms:

1. **Direct operate** (discrete, `mode: direct`): the state buttons (for example
   OPEN and CLOSE) operate immediately.
2. **Setpoint** (real, `kind: setpoint`): enter a value and press SEND. The value
   is in engineering units; the gateway converts it to raw device units on the way
   down.
3. **Select-before-operate** (`mode: sbo`): press **SELECT** first. The point arms
   for a timeout (the panel shows ARMED with a countdown and a CANCEL button), then
   the operate buttons appear. The server enforces this: an operate without a
   current selection from the same client is rejected.

For a setpoint, type the value and press SEND in one action; the field keeps what
you type while you are editing it, even as live reports refresh the panel.

After you operate, the command travels to the device and the read-back returns over
ICCP, so the point's value updates within a few seconds. For a step-by-step
walkthrough of each control type against the `testbed-demo` points, see
{doc}`hmi-demo`. See {doc}`tase2-on-the-wire` for exactly what happens on the wire.

### Alarms

The **active alarms** panel is the dominant attention zone. Alarms are derived
live: station comms lost, a point NOT VALID, or a value over its high or under its
low limit. Each row shows severity (CRIT or WARN), an id, the condition, the value,
and the ack state. ACK ALL acknowledges the current set; an alarm re-arms if it
clears and recurs.

### Event log

The bottom **event log** is a timestamped timeline with severity tags, filterable
by type: ALM (alarms), CMD (operator commands), RX (reports received), SYS (station
comms changes). It is the audit trail of what happened and when.

## Where to go next

- {doc}`tase2-on-the-wire`: how monitoring and control become real TASE.2 traffic,
  and how the simulation mode keeps everything virtual.
- {doc}`configuration`: defining stations, points, controls, limits, and the tag
  database.
- {doc}`../api/rest`: driving the same actions over the HTTP API.
