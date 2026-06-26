# Scenarios: reproducible operations, attacks, and faults

A scenario is a single file that scripts a timeline of events and plays it against
the TASE.2 server. It is the third operating mode, alongside simulation and
ingestion, and it exists for one reason: repeatability. If you want to train a blue
team, regression-test an intrusion detection system, or build a labelled dataset,
you need to run the exact same sequence of normal operations and attacks again and
again and get the same result every time. A scenario gives you that.

In scenario mode the server runs with its own simulation off and there is no
ingestion gateway. The scenario engine (`suite/scenario.py`) is the only thing
driving the points. It seeds every point to a baseline, keeps them fresh so the HMI
shows stations online, and then walks the timeline turning each event into real
TASE.2/ICCP traffic on the wire. Because the engine is the source of truth, it also
writes a ground-truth file recording exactly what it did and when, with a
benign-or-malicious label and an optional technique tag. That file is what makes
the dataset and detection-scoring tools possible.

## Running a scenario

The quickest way is the named deployment:

```bash
python3 suite/tase2ctl.py run scenario-demo
```

That starts the server, the HMI bridge, and plays `scenarios/fdi_tieline.json`.
Open <http://127.0.0.1:8800> and watch it unfold: the tie-line flow jumps over its
alarm limit, a transformer alarm flickers, the breaker is commanded open, and the
transformer station drops offline and comes back.

To run a specific scenario file directly against an already-running server:

```bash
python3 suite/scenario.py run scenarios/fdi_tieline.json \
  --server-port 102 --out groundtruth.jsonl
```

And to check a scenario before you run it:

```bash
python3 suite/scenario.py validate scenarios/fdi_tieline.json
```

Validation checks every step against the point model in `config/scada.json`, so a
typo in a point name or an unknown action is caught up front with a readable
message instead of a surprise at run time.

## The scenario file

A scenario is JSON, so it stays dependency-free and consistent with the other
config files. It has a name, an optional seed for reproducibility, a baseline of
starting values, and a timeline of steps.

```json
{
  "name": "fdi_tieline",
  "seed": 1234,
  "baseline": { "plc1_mw": 12.0, "plc1_kv": 138.0, "plc1_brk": 1 },
  "timeline": [
    { "at": 0,  "do": "annotate", "note": "normal operations" },
    { "at": 5,  "do": "inject", "point": "plc1_mw", "value": 19.5,
      "technique": "T0856", "note": "spoofed tie-line flow over the hi limit" },
    { "at": 30, "do": "operate", "point": "plc1_brk", "command": 0, "sbo": true,
      "technique": "T0855", "label": "malicious", "note": "unauthorized breaker open" },
    { "at": 36, "do": "comms_loss", "station": "plc3", "label": "benign" },
    { "at": 53, "do": "end" }
  ]
}
```

Every step has an `at` time in seconds from the start of the run, and a `do` that
names the action. The timeline runs in order. Most steps name a `point` (which must
exist in the point model) and carry an optional `note`, an optional `label` of
`benign` or `malicious`, and an optional `technique` tag (MITRE ATT&CK for ICS
technique IDs work well here, for example `T0856` Spoof Reporting Message or
`T0855` Unauthorized Command Message).

## The actions

| Action | What it does |
|--------|--------------|
| `annotate` | Drops a marker in the ground truth. No traffic, just a note on the timeline. |
| `set` | A benign sustained value change. The engine holds the new value until you change it again. |
| `inject` | A false-data injection. Same as `set` but labelled malicious by default. This is how you spoof a reading. |
| `pulse` | A transient value for a number of `seconds`, then it restores the previous value automatically. Good for a flickering false alarm. |
| `ramp` | Glides a point from its current value `to` a target over `seconds`. Good for a voltage sag or a slow drift. |
| `operate` | A Block 5 discrete command (for example a breaker open or close). Set `sbo: true` to select before operating. |
| `setpoint` | A Block 5 analog setpoint command. Also supports `sbo`. |
| `comms_loss` | Stops refreshing a `station` (or a `points` list) so it ages out, goes not-valid, and the station reads offline. |
| `restore_comms` | Brings those points back online. |
| `quality` | Forces a point's quality flag (`valid`, `suspect`, `held`, `notvalid`) without changing its value. |
| `end` | Stops the run early. |

A note on timing: long actions such as `ramp` and `pulse` run for their full
duration before the next step begins, so space your `at` times to account for them.
Meanwhile the heartbeat keeps every other point fresh in the background, so the rest
of the grid stays online while one point is ramping.

## Roles and environments

A step may name its point directly (`"point": "plc1_mw"`), or it may name it by
role (`"role": "tie_flow"`). A role is resolved at run time against the chosen
environment, so one scenario runs on more than one grid without being rewritten. The
shipped attacks all use roles, which is what lets each one play on the small lab and
on the regional grid alike. The role forms are `role` (a single point), `roles` (a
list), `target_role` (a scan or flood target), and `station_role` (a station for
`comms_loss`); the literal `point`, `points`, `target`, and `station` forms still
work and are left untouched.

An environment lives in `config/environments.json`. It gives a `config` (the point
model), a `grid` (the power-flow model), a `roles` map (role to point), and a
`stations` map (station role to station), and the engine applies it before it plays
the timeline. Two are defined: `simple` (the four-bus lab) and `realistic` (the
regional `grid-demo` grid). Pick one with `--env` or, in the console, with the
environment dropdown on an Attack Scenario:

```bash
python3 suite/scenario.py validate scenarios/ukraine2015_blackout.json --env realistic
python3 suite/tase2ctl.py run ukraine2015-attack --env realistic
```

To add an environment, give every role and station an entry under a new key. To make
a scenario portable, reference its points by role rather than by name.

## Backing a scenario with physics

A scenario can name a grid model, and then the power-flow co-simulation becomes the
value source underneath the script. Add a `grid` to the scenario:

```json
{
  "name": "fdi_cascade",
  "period": 2.0,
  "grid": "config/grid.json",
  "timeline": [
    { "at": 6,  "do": "inject", "point": "plc1_mw", "value": 60.0, "technique": "T0856" },
    { "at": 14, "do": "operate", "point": "plc1_brk", "command": 0, "sbo": true,
      "technique": "T0855", "label": "malicious" },
    { "at": 34, "do": "end" }
  ]
}
```

Now the points are driven by the solved grid, not a flat baseline, and the script
rides on top of the physics. This changes what the actions mean in the best way:

- An `operate` on a breaker actually switches a line in the model, so the flow
  redistributes and an overload can cascade. The unauthorized open above produces a
  real cascading blackout, not just a state flip.
- An `inject` pins its point over the physics. In the example the attacker spoofs the
  tie-line flow to a calm 60 MW, and that point keeps reading 60 even as the real
  grid collapses around it, so the operator sees one normal value amid the chaos.
- The cascade trips the script did not write are recorded in the ground truth as
  their own events, labelled as the physical consequences of the attack.

This is the force multiplier: every scenario, dataset, and scorecard built this way
carries physically consistent behaviour for free. Run it with the ready-made
deployment:

```bash
python3 suite/tase2ctl.py run cascade-demo
```

See {doc}`physics` for the grid model itself.

## The ground-truth timeline

When you pass `--out`, the engine writes one JSON object per line recording each
event as it happens. The first line is a header with the scenario name and seed,
and each event line looks like this:

```json
{ "t": 5.0, "wall": 1782334084.7, "do": "inject", "label": "malicious",
  "point": "plc1_mw", "station": "plc1", "value": 19.5, "quality": "valid",
  "technique": "T0856", "note": "spoofed tie-line flow over the hi limit" }
```

Because the engine knows precisely what it injected and exactly when, this file is a
perfect label track. Capture the network traffic at the same time (see
{doc}`results` and the capture scripts) and you have a packet capture
paired with ground truth: every packet window is known benign or malicious, tagged
with a technique and a target point. That pairing is the foundation for generating
labelled training data and for scoring whether a detector caught what it should
have.

## Writing your own

Start from `scenarios/steady_state.json`, which is an all-benign baseline, or copy
`scenarios/fdi_tieline.json` and edit it. Keep the point names matching
`config/scada.json`, run `validate` until it is clean, then run it. Because the run
is seeded and the engine is the sole value source, two runs of the same file
produce the same timeline, which is exactly what you want for a test you can trust.
