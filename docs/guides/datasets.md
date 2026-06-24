# Labelled datasets: capture a scenario, label every packet

A scenario produces two things that line up in time. One is a packet capture of
the real TASE.2/ICCP traffic. The other is the ground-truth timeline the scenario
engine writes, which records exactly what happened and when and whether it was
benign or an attack. The dataset tool (`suite/dataset.py`) joins those two by their
timestamps and hands you a capture where every slice of time is labelled.

Why this is worth doing. Honestly labelled industrial-protocol datasets are rare,
because to label one correctly you have to know the ground truth, and normally
nobody does. Here we are the ground truth: the scenario engine knows precisely
which moments carried an injection or an unauthorized command, so the labels are
exact rather than guessed. That is the kind of data you need to train a detector or
to measure whether an intrusion detection system caught what it should have.

## Building a dataset in one command

```bash
sudo ./scripts/58_run_dataset.sh
```

That starts the server and the HMI bridge, captures the loopback traffic on the
TASE.2 port, plays `scenarios/fdi_tieline.json`, stops the capture, and labels it.
It needs `sudo` only because capturing loopback traffic does (the same as the other
capture scripts). The result lands in `datasets/<scenario>-<timestamp>/`.

Pick a different scenario or window size with environment variables:

```bash
SCENARIO=scenarios/steady_state.json WINDOW=0.5 sudo ./scripts/58_run_dataset.sh
```

## Labelling a capture you already have

The labeller is a standalone step, so if you captured the traffic yourself (with
tcpdump, Wireshark, or the namespace capture scripts) you can label it directly:

```bash
python3 suite/dataset.py label capture.pcap groundtruth.jsonl \
  --out datasets/run1 --server-port 10502 --window 1.0
```

It reads the capture with a small built-in pcap reader, so it depends on no capture
or packet-parsing packages. It is standard library only, like the rest of the
Python side.

## What you get

The output directory holds:

| File | What is in it |
|------|---------------|
| `dataset.csv`, `dataset.jsonl` | One row per time window, with flow features and a label. |
| `packets.jsonl` | One row per packet with its window label (when you pass `--packets`). |
| `splits/train.*`, `splits/test.*` | A deterministic train/test split. |
| `manifest.json` | What was built, the parameters, the label balance, and the techniques covered. |
| `capture.pcap`, `groundtruth.jsonl` | The raw inputs, kept alongside so the dataset is self-contained. |

Each window row carries simple, model-ready features computed from the capture:
packet and byte counts, the split by direction (client-to-server versus
server-to-client), the largest payload, TCP flag counts, and a protocol-aware count
of TASE.2/MMS PDUs (found cheaply from their TPKT framing, with no MMS decoding).
The label is `benign` or `malicious`, and `techniques` lists any MITRE ATT&CK for
ICS tags that were active in that window.

```json
{ "window": 4, "rel_start": 4.0, "packets": 11, "bytes": 1320,
  "pkts_c2s": 6, "pkts_s2c": 5, "max_len": 180, "mms_pdus": 9,
  "label": "malicious", "techniques": "T0856" }
```

## How the labelling works

The tool turns the ground-truth events into time intervals during which malicious
activity was on the wire, then labels each window by whether it overlaps one.

There are two kinds of events. A sustained value change (an `inject`) holds until
the next change on that point, so an injection is malicious from the moment it was
set until it is restored. An instantaneous action (an `operate`, `setpoint`, or
`quality` change) is a brief burst, so it gets a small window around its time. You
can tune that window with `--pre` and `--post`. Benign events never create a
malicious interval, they only end one.

## Choosing a split

The default split is `interleave`: every Nth window goes to the test set, so both
labels show up in both sets. That is the safe choice for a single short run, where
the attacks are clustered in time and a straight chronological cut could put every
attack on one side. When you have a longer run or many scenarios stitched together,
`--split-mode chrono` (train on the earlier part, test on the later part) is the
more honest evaluation. Either way the split is deterministic, so the same inputs
always produce the same train and test sets.

## From one run to a corpus

A single scenario gives you a small, perfectly labelled sample. To build a real
corpus, run several scenarios (a calm `steady_state` baseline, an injection run, a
command-abuse run, and so on), label each, and concatenate the window rows. Because
every run is seeded and deterministic, the corpus is reproducible: regenerate it
any time from the scenario files, and feed it to a detector or score a sensor
against it with the detection-scoring tools that build on these labels.
