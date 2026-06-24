# Detection content and scoring

This directory holds starter detection content for the TASE.2/ICCP traffic the
suite generates, plus a small example so you can see the scoring loop without an
IDS installed.

## The loop

1. Play a scenario and capture it, which also writes the ground truth. The dataset
   orchestrator does both: `sudo ./scripts/58_run_dataset.sh`.
2. Run your sensor over the capture to get alerts. With Suricata and these rules:

   ```bash
   suricata -r datasets/<run>/capture.pcap -S detect/tase2.rules -l /tmp/suri
   ```

3. Grade the alerts against the ground truth:

   ```bash
   python3 suite/score.py grade datasets/<run>/groundtruth.jsonl /tmp/suri/eve.json \
     --out scorecard.json --report scorecard.md
   ```

The scorer tells you, per technique, how many attacks were caught and missed, how
fast, and how many false positives fired on benign traffic. That is the number you
tune the rules against.

## Files

- `tase2.rules` is a starter Suricata ruleset. It is a baseline to tune, not a
  finished product. The rules and their honest limitations are documented inline.
- `example_alerts.jsonl` is a tiny generic alert feed (the `{ts, signature,
  technique}` form the scorer also reads) so you can try `score.py` immediately,
  with no IDS, against a ground-truth file from any scenario run.

## Alert formats the scorer reads

- **Suricata eve.json**: the scorer takes `event_type: alert` records, reading the
  timestamp, signature, signature id, and the technique from
  `alert.metadata.mitre_technique_id`.
- **Generic JSON lines**: one object per line of `{"ts": <unix or ISO8601>,
  "signature": "...", "sid": <optional>, "technique": "<optional Txxxx>"}`. Use this
  to bring alerts from any other sensor.

## A note on what is hard

Unauthorized commands are visible: they write to a named control object, so a
content rule can catch them. False-data injection is not, on a shared channel: a
spoofed reading is byte-for-byte the same kind of write the trusted gateway makes,
so signatures alone cannot separate them. That gap is real, and the scorer is built
to show it rather than hide it. Closing it needs value-range, rate, or anomaly
logic, which is the kind of thing the physics-aware backend on the roadmap makes
possible.
