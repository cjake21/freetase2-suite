# Detection scoring: grade your sensor against the truth

A scenario plays a known set of attacks. A dataset captures them and labels them.
The next question is the one a security team actually loses sleep over: did the
intrusion detection system catch them? The scorer (`suite/score.py`) answers that by
the numbers. It lines your sensor's alerts up against the scenario's ground truth
and tells you which attacks were caught, which were missed, how fast, and how often
the sensor cried wolf on benign traffic.

This is the purple-team loop. Normally this is judged by hand and by feel, because
nobody knows the exact ground truth. Here we do, so the grade is honest.

## Try it right now, no sensor needed

There is a small frozen example in `detect/` so you can see the output before
wiring up anything:

```bash
python3 suite/score.py grade detect/example_groundtruth.jsonl detect/example_alerts.jsonl
```

You will see a scorecard like this:

```
[score] attacks: 2 total, 1 detected, 1 missed  (recall 50%)
[score] alerts: 2 total, 1 true positive, 1 false positive (6.06/min)
[score] mean time to detect: 0.70s
[score] by technique:
  T0855        Unauthorized Command Message 1/1 detected (100%)
  T0856        Spoof Reporting Message      0/1 detected (0%)
```

That tells a clear story in one glance: the unauthorized command was caught, the
spoofed reading was not, and there was one false alarm. Add `--out scorecard.json`
for the machine-readable version and `--report scorecard.md` for a written report.

## The full loop with a real sensor

1. Build a dataset, which captures the run and writes the ground truth:

   ```bash
   sudo ./scripts/58_run_dataset.sh
   ```

2. Run your sensor over the capture. With Suricata and the starter rules, the
   helper does it for you:

   ```bash
   ./scripts/59_score.sh datasets/<run>
   ```

   That runs Suricata with `detect/tase2.rules`, then grades the alerts and writes
   `scorecard.json` and `scorecard.md` into the dataset directory.

3. With any other sensor, run it yourself and grade its alerts directly:

   ```bash
   python3 suite/score.py grade datasets/<run>/groundtruth.jsonl my_alerts.json \
     --out scorecard.json --report scorecard.md
   ```

## How an attack counts as detected

The scorer turns the ground truth into the same attack windows the dataset tool
uses, so the two always agree on what an attack is. An attack counts as detected if
at least one alert falls within its window, widened a little by `--tolerance` (two
seconds by default) to allow for a sensor that reports slightly late. An alert that
lands in some attack window is a true positive; an alert that fires when nothing
malicious is happening is a false positive. From those it computes recall per
technique, the mean time to detect, and a false-positive rate per minute.

## Alert formats

- **Suricata eve.json**: it reads the `alert` records, taking the timestamp, the
  signature, the signature id, and the technique from
  `alert.metadata.mitre_technique_id`. Pass `--format suricata` or let it
  auto-detect.
- **Generic JSON lines**: one object per line of `{"ts": <unix or ISO8601>,
  "signature": "...", "sid": <optional>, "technique": "<optional Txxxx>"}`. This is
  the bridge for any other sensor: convert its output to this shape and grade it.

## The point of the gaps

A good scorecard is not one that reads 100 percent. It is one that tells you the
truth. The example above is honest about a real limitation: an unauthorized command
writes to a named control object and is straightforward to catch, but a false-data
injection writes an ordinary value that looks just like the trusted gateway's
traffic, so a signature alone misses it. The scorer is built to surface that gap
rather than paper over it, which is exactly what makes it useful for deciding where
to spend your detection effort. See `detect/README.md` for the starter rules and
their documented limitations.
