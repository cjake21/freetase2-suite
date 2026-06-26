# HMI demo: interacting with the environment

This is a hands-on walkthrough of the SCADA HMI: how to use the runtime panel to
read live values and how to issue real controls that change state in the
environment. It uses the `testbed-demo` deployment, whose bundled virtual devices
emit real Modbus and DNP3, so every action travels the same path it would to real
hardware. For what happens on the wire, see {doc}`tase2-on-the-wire`; for the panel
reference, see {doc}`operations`.

```{contents}
:local:
:depth: 2
```

## Before you start

Bring up the deployment and open the HMI:

```bash
python3 suite/console.py            # launcher on http://127.0.0.1:8080
```

In the launcher, Start **`testbed-demo`**, then open the **SCADA HMI** link (or go to
`http://127.0.0.1:8800`). Confirm the status strip shows **LINK: NORMAL** or
**DEGRADED** and that **SCAN** reads RUN. One station (`plc3`, TRANSFORMER T1) is
deliberately offline in this deployment, so DEGRADED is expected and useful: it
shows what a lost device looks like.

The controllable points in `testbed-demo` are:

| Point | Station | Control | Mode | What it does |
|-------|---------|---------|------|--------------|
| `plc1_avr` | FEEDER BAY 1 | setpoint | direct | AVR voltage setpoint (a real/analog value) |
| `plc2_brk` | FEEDER BAY 2 | discrete | direct | breaker open/close, operates immediately |
| `plc1_brk` | FEEDER BAY 1 | discrete | select-before-operate | breaker with an interlock |
| `rtu1_brk` | DNP3 RTU 1 | discrete | select-before-operate | breaker over DNP3 |

To see only these, tick **ctrl** in the table filter row, or use the **SETPT / DIR /
SBO** tag in the control column to spot them.

## Demo 1: change an analog setpoint

This is the most common interaction: push a new engineering value to a device and
watch it come back over ICCP.

1. In the points table, click the **`plc1_avr`** row (AVR SETPOINT). The right
   detail panel opens with its current value, quality, time tag, and an **Operator
   Command** module.
2. In the command module, type a new value into the setpoint box, for example
   `7.5`, and press **Send**.
3. Watch the value. The command is in engineering units; the gateway converts it to
   raw device units, writes it down, and the device read-back returns over ICCP. The
   `plc1_avr` value updates to your new setpoint within a few seconds, in both the
   detail panel and the table.
4. Open the **event log** (bottom) and filter to **CMD**. You will see your
   `OPERATE plc1_avr = 7.5` line, then an **RX** line as the next Block 2 report
   carries the new value back.

```{tip}
The value travels a full round-trip (HMI to gateway to device, then device to
gateway to server to HMI), so expect a few seconds before the new number is
confirmed on screen. That delay is the real control loop, not the UI; the field
time tag and age on the point show when the device last reported.
```

## Demo 2: operate a breaker directly

A direct discrete control acts immediately, with no interlock.

1. Click the **`plc2_brk`** row (FEEDER BAY 2 breaker). The command module shows the
   state buttons, for example **OPEN** and **CLOSE**.
2. Press **OPEN**. The command goes straight to the device.
3. The breaker state returns over ICCP and the point value flips to OPEN within a
   few seconds. The event log records the `OPERATE` under **CMD**.
4. Press **CLOSE** to return it. If you have alarm limits configured on the feeder,
   opening the breaker may also raise or clear an alarm in the **active alarms**
   panel; that is the same data driving both views.

## Demo 3: select-before-operate (interlocked breaker)

Critical breakers use select-before-operate (SBO): you must select the point first,
which arms it for a short window, then operate. The server enforces this, so a
stray operate without a current selection is rejected.

1. Click the **`plc1_brk`** row (FEEDER BAY 1 breaker, tagged **SBO**). The command
   module shows a **Select** button and an "interlock: select before operate" note,
   not the operate buttons.
2. Press **Select**. The panel arms: it shows **ARMED** with a countdown and reveals
   the operate buttons plus a **Cancel**. The event log records a `SELECT` line.
3. While armed, press the operate button (for example **OPEN**). The operate is
   accepted because the selection is current, and the breaker changes state on the
   read-back.
4. To prove the interlock, select again and wait for the countdown to expire without
   operating, or press **Cancel**. The operate buttons retract; an operate now would
   be rejected by the server. `rtu1_brk` behaves the same way over DNP3.

## Watching the change land

Three places confirm an interaction took effect, in order:

1. **Event log (CMD)**: your command was issued from the HMI.
2. **Deployment log** in the launcher (Runtime filter): a `[ingest] command ...`
   line shows the gateway wrote the value down to the device.
3. **Points table / detail value**: the device read-back returns over ICCP and the
   point value updates, with a fresh time tag and a reset age.

If the value does not change, work backwards through those three: no CMD line means
the command was not sent from the HMI; a CMD line but no `[ingest] command` line
means the gateway did not accept or detect it; both present but no value change
points at the device or the read-back path.

## Notes and gotchas

- **Type, then Send.** The panel refreshes with each live report. Enter the setpoint
  and press Send in one go; the field keeps what you type while you are editing it.
- **Engineering units.** Setpoints and values are in engineering units (MW, kV, and
  so on). The gateway handles scaling to and from raw device registers, so you never
  type raw counts.
- **Offline stations cannot be controlled.** `plc3` is offline in `testbed-demo`; its
  points read NOT VALID and issuing a control has nothing to act on. This is the
  expected behavior for a lost device.
- **It is real traffic.** Even with virtual devices, the northbound path is real
  TASE.2 and the southbound path is real Modbus or DNP3. A capture on the loopback
  shows genuine frames for every action here. See {doc}`tase2-on-the-wire`.
```
