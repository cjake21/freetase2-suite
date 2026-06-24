# The attack library

These are built-in, multi-stage attacks on the grid, written to look on the wire
the way a real intrusion would, so you can capture them, label them, and build and
test detections against traffic that resembles the real thing. Each one is grounded
in a documented incident or a recognised technique, and each step is tagged with its
MITRE ATT&CK for ICS identifier (the classic T08xx series). They run on the live
power-flow grid, so the consequences are physically consistent, and they write a
ground-truth timeline so every packet window is labelled.

## Run one, and capture it

```bash
python3 suite/tase2ctl.py run ukraine2015-attack
```

To turn it into a dataset and a detection score:

```bash
sudo SCENARIO=scenarios/ukraine2015_blackout.json ./scripts/58_run_dataset.sh
./scripts/59_score.sh datasets/<run>
```

The four headline attacks are also deployments in the control console under Attack
Scenarios: `ukraine2015-attack`, `industroyer-attack`, `stealthy-attack`, and
`recon-attack`.

## What makes the traffic realistic

Two things, both of which a detector cares about.

**Two associations, not one.** A real attack does not come from the trusted feed. So
these scenarios open a second association: the steady, legitimate telemetry stays on
the primary connection, while the reconnaissance reads, the false-data writes, the
unauthorized commands, and the floods all come from a separate attacker connection.
A capture shows two peers, one of which suddenly browses the model and starts issuing
commands. That separation is exactly the signal a good sensor keys on, and it is
turned on with `"attacker": true` in the scenario.

**The full kill chain, in order.** The attacks do not jump straight to opening a
breaker. They discover, then collect, then act, then try to stay in control, the way
real intrusions unfold. That gives you traffic for the early stages, which is where
detection is most valuable and most often missing.

To support this the scenario engine has reconnaissance and denial actions on top of
the operational ones:

- `scan` issues real MMS reads. With `discover` it also reads the Block 1 metadata
  (version, supported features, bilateral table), the browsing that marks discovery.
- `flood` hammers a control or point with rapid messages for a few seconds, a denial
  of service on the wire.
- `inject`, `operate`, and `setpoint` carry the false data, the unauthorized
  commands, and the parameter changes, routed through the attacker association.

## The scenarios

### ukraine2015-attack (`scenarios/ukraine2015_blackout.json`)

The December 2015 attack on the Ukrainian grid, the first cyberattack publicly
confirmed to cause a blackout, attributed to Sandworm using BlackEnergy3. Having
stolen operator credentials, the adversary used the utility's own SCADA to open
breakers at around thirty substations, then wiped HMIs, bricked serial-to-Ethernet
converters, and ran a telephone denial of service so operators could neither see nor
respond.

On the wire here: the attacker association reads the model (**T0888**), reads every
point to map the grid (**T0801**), opens the breakers across the bay (**T0855**,
**T0831**), re-sends open commands as operators try to recover (**T0813**), and takes
a station's view offline (**T0815**). The live grid cascades from the breaker opens.
Detection notes: the standouts are the new association doing a full read sweep, the
burst of control-object writes, and the command flood.

### industroyer-attack (`scenarios/industroyer_sweep.json`)

The December 2016 attack on a Kyiv transmission substation by Industroyer, also called
CRASHOVERRIDE (MITRE campaign C0025, software S0604), the first malware purpose-built
to speak grid protocols directly. Its modules drove IEC 60870-5-101/104, IEC 61850,
and OPC to change breaker and switch state, sweeping them and rapidly toggling state.

On the wire here: automated discovery (**T0888**), a manipulated operator view that
pins the tie-line flow to a calm value (**T0832**), a coordinated breaker sweep
(**T0855**, **T0831**), and a rapid open/close toggling flood (**T0814**). Detection
notes: the toggling flood is a strong rate signal, and the sweep is a short cluster of
control writes to several objects in quick succession.

### stealthy-attack (`scenarios/stealthy_false_data.json`)

The quiet attack, and the one signatures struggle with. There is no breaker open and
no obvious command. The adversary identifies the controllable parameters (**T0861**),
nudges a setpoint to push toward instability (**T0836**), then feeds false telemetry
so the developing problem stays hidden: the tie-line flow reads normal while it really
climbs (**T0856**, **T0832**), and the transformer oil temperature is held below its
alarm limit while it really heats (**T0878**). Detection notes: there is little to
catch by signature, because a spoofed value looks like a normal write. This is the
case for value-range and physics-aware detection, and the scorecard will show the gap
honestly.

### recon-attack (`scenarios/recon_collection.json`)

The stage before the impact, and a clean detection target on its own. An attacker with
a foothold reads the model and the bilateral table (**T0888**, **T0846**), re-reads
every point over time (**T0801**, **T0802**), and singles out the breakers
(**T0861**), without sending a single command. Nothing changes physically, which is
exactly what makes catching it valuable: a sensor that flags a peer reading objects it
has no operational need to read stops the attack before a breaker ever moves.

## Building your own

Copy one of these and edit the timeline. Keep the `"attacker": true` flag so the
malicious traffic comes from its own association, tag each step with its ATT&CK for ICS
technique so the dataset and scorecard stay meaningful, and point the scenario at a
grid so the consequences are physical. Validate it, then capture and score it like any
other run. See {doc}`scenarios` for the full action vocabulary and {doc}`datasets` and
{doc}`scoring` for the capture and grading tools.

## Sources

The incident details above are drawn from public analyses and the MITRE ATT&CK for ICS
knowledge base: the 2015 Ukraine power grid attack and the 2016 Industroyer /
CRASHOVERRIDE campaign (MITRE C0025, software S0604). The technique identifiers are the
classic ATT&CK for ICS T08xx series.
