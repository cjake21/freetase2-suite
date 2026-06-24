# Components

## TASE.2 server (`src/tase2_server.c`)

Built on the libIEC61850 MMS engine. It builds the object model from a point list
generated from `config/scada.json`, holds the point cache, sends Block 2 reports,
and handles writes and Block 5 control including select-before-operate enforcement
and the command allowlist. Flags: `-P` points file, `-n` no simulation, `-o`
injection hold, `-L` command allowlist, `-T -C -K -A` TLS.

## ICCP agent (`src/tase2_hmi_agent.c`)

A persistent MMS client driven over a line protocol on stdin, emitting one JSON
event per line on stdout. Commands include `SUBSCRIBE`, `WRITEQ` (value with
quality and time), `OPERATE`, `SETPOINT`, `SELECT`, `CANCEL`, `READ`, `SNAPSHOT`.
The bridge and the gateway each drive their own agent. It speaks TLS when
`TASE2_TLS=1`.

## Ingestion gateway (`ingest/tase2_ingest.py`)

A Modbus and DNP3 master. Each poll it reads every tag from its device, scales the
value, and writes it up with quality and a time tag, and for controllable points it
reads the control object and writes any new command down to the device. Drivers are
registered in `DRIVERS`. See {doc}`../modules/index`.

## HMI bridge (`hmi/bridge.py`)

Loads `config/scada.json`, drives two agents (a writer and a subscriber), groups
points by station, derives per-station comms from point quality, and serves the web
HMI and the control API. Standard library only.

## Web HMI (`hmi/static/`)

A dynamic station grid rendered from the bridge state. One card per station, each
point with value, unit, and quality, plus a global alarm strip and event log.
Controllable points show operate controls, with a select and arm flow for SBO.

## Scenario engine (`suite/scenario.py`)

The value source in scenario mode. It plays a deterministic, seeded timeline
(`scenarios/*.json`) against the server: it seeds every point, keeps them fresh
with a heartbeat, and turns each timeline event into real ICCP traffic (value
injection, operator commands, ramps, comms loss). It writes a ground-truth label
timeline (benign or malicious, with a technique tag) that the dataset and detection
tools build on. It drives one ICCP agent over the same line protocol as the bridge,
so it needs no new protocol code. Standard library only. See
{doc}`../guides/scenarios`.

## Dataset labeller (`suite/dataset.py`)

Joins a packet capture of a scenario run with that scenario's ground-truth
timeline, by timestamp, and writes a labelled dataset: one row per time window with
flow features (including a TPKT-framed TASE.2/MMS PDU count) and a benign or
malicious label with technique tags, plus a deterministic train/test split and a
manifest. It reads the capture with a small built-in pcap reader, so it is standard
library only and needs no capture or parsing packages. The orchestrator
`scripts/58_run_dataset.sh` captures a run and labels it in one step. See
{doc}`../guides/datasets`.

## DNP3 outstation simulator (`ingest/dnp3_outstation_sim.py`)

A minimal outstation for the DNP3 path. It answers reads for binary and analog
inputs and accepts CROB control, so the full pipeline runs with no hardware.
