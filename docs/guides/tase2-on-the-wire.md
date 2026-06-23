# Sending TASE.2 traffic over the wire

Everything the operator does in this tool produces real TASE.2 traffic. TASE.2 /
ICCP is carried over MMS (ISO 9506); there is no separate TASE.2 PDU on the wire.
What makes a capture TASE.2 is the object model and the transfer-set and report
behaviour layered on MMS. This page explains how monitoring and control map to MMS
services on the wire, how to observe them, and how the simulation mode keeps the
values virtual while the protocol stays real.

```{contents}
:local:
:depth: 2
```

## The two paths on the wire

### Monitoring (gateway to subscribers)

```text
device --(Modbus/DNP3)--> tase2_ingest --(MMS Write)--> server cache
server --(MMS unconfirmed InformationReport, Block 2)--> subscribers (HMI, ICCP peers)
```

1. The gateway reads a device value and writes it into the server with quality and
   a time tag. On the wire this is an **MMS Write** to the point's structure
   (`<name>$Value`, `<name>$Flags`, `<name>$TimeStamp`).
2. A subscriber (the HMI's Station B agent, or any ICCP client) has bound a data
   set to a `DSTransferSetNN` and enabled it. The server then emits **Block 2
   unconfirmed InformationReport** PDUs carrying each member's value, quality, and
   time tag, on the integrity interval and on change.

So the values you see in the HMI arrived as genuine ICCP reports, not an internal
copy.

### Control (operator to device)

```text
HMI operate --(MMS Write to Block 5 control object)--> server stores command
tase2_ingest --(MMS Read of the control object)--> reads the command
tase2_ingest --(Modbus write / DNP3 CROB)--> device acts
device read-back --> server --> Block 2 report --> HMI
```

1. An operator command is an **MMS Write** to the point's Block 5 device control
   object `<name>_ctl`. A direct operate writes `Command`; a select-before-operate
   first writes `SBO` (select), then `Command` (operate).
2. The gateway issues an **MMS Read** of `<name>_ctl` to pick up the command, then
   writes it down to the device (a Modbus register/coil write or a DNP3 CROB).
3. The device acts; the new state is read back up and published in the next report.

The command object and the monitoring point are different objects, so a command and
a field read-back never overwrite each other.

## The object model you will see

A capture against the server shows these objects (names come from
`config/scada.json`; see {doc}`../concepts/data-model`):

- VMD scope: `TASE2_Version`, `Supported_Features`.
- ICC domain: `Bilateral_Table_ID`, `Next_DSTransfer_Set`, the transfer-set status
  variables, `DSTransferSet01`..`08`, one indication point per configured point
  (`{ Value, Flags, TimeStamp }`), and one control object per controllable point
  (`<name>_ctl = { Command, Tag, Status, SBO }`).

## Observing the traffic

The server speaks MMS on its TCP port (10502 in the demos, 102 in production
deployments). Capture it with Wireshark or tshark, which decode MMS:

```bash
# capture the ICCP/MMS exchange on the loopback demo port
tshark -i lo -f "tcp port 10502" -Y mms
```

The repository also includes capture helper scripts and a sample TASE.2 pcap under
the project `docs/` directory. Loopback capture may require capture privileges; on
a lab host grant them to dumpcap or run the namespace capture scripts.

For the southbound side, capture Modbus (`tcp port 1502` in the demo, 502 in
production) or DNP3 (`tcp port 20000`) to see the field protocol that the gateway
turns into ICCP.

## Keeping it virtual: simulation mode and `simulateValues()`

You do not need any hardware, or even the ingestion gateway, to put real TASE.2 on
the wire. The server has a built-in synthetic value source.

### `simulateValues()`

`simulateValues()` is a function in the TASE.2 server that runs once per second.
For every point it rewrites the value from a synthetic function: a sine curve for
analog (real) points and a toggle for status (state) points, under the model lock,
exactly where field data would otherwise be written. The reporting that follows is
real: the server still sends genuine Block 2 InformationReport PDUs. Only the
*values* are synthetic.

This is what the **simulation mode** (`sim-demo`) uses. It connects to nothing and
ingests nothing, so it is safe on an open lab bench and is ideal for capture work,
parser and IDS testing, and training. The wire looks like a live ICCP node; the
numbers are made up.

### Turning the simulation off for real data

In **ingestion mode** the server runs with the `-n` flag, which disables
`simulateValues()`. Points then change only from the gateway's MMS Writes, which
carry real field values, quality, and time. The injection hold (`-o`) pins each
written value so it persists between polls and propagates in reports before
anything could overwrite it.

| Mode | `simulateValues()` | Value source | Connects to |
|------|--------------------|--------------|-------------|
| simulation (`sim-demo`) | on | synthetic sine/toggle | nothing |
| ingestion (`field-demo`) | off (`-n`) | field devices via the gateway | the configured devices |

### Virtual devices that still produce real protocol

Between "all synthetic" and "real PLCs" there is a middle ground used by the
universal demo: the bundled **Modbus slave simulator** and **DNP3 outstation
simulator**. With these, the gateway performs real Modbus and DNP3 exchanges (real
frames on TCP 1502 and 20000), turns them into real ICCP, and accepts control back
down to the simulated devices. Nothing is hardware, but every protocol on the wire
is genuine.

```{note}
The path from this virtual setup to a live testbed is only the tag database. Point
the device host/port (and registers/indexes) at your real PLCs and RTUs, turn the
simulators off, and the northbound TASE.2 behaviour is identical. See
{doc}`configuration` and {doc}`../modules/configuration`.
```

## A worked example

1. Start the universal demo from the launcher (deployment `field-demo`), or:
   ```bash
   MODBUS_SIM=1 DNP3_SIM=1 TAGS=ingest/tags.demo.json ./scripts/55_run_scada.sh
   ```
2. In another terminal, capture the ICCP exchange:
   ```bash
   tshark -i lo -f "tcp port 10502" -Y mms
   ```
3. In the HMI, select `plc1_brk`, press SELECT, then CLOSE. In the capture you will
   see the MMS Write to `plc1_brk_ctl$SBO` then `plc1_brk_ctl$Command`, the gateway
   reading the control object, and the subsequent Block 2 report carrying the new
   breaker state.
