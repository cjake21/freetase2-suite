# Configuration

## Point model: `config/scada.json`

```json
{
  "domain": "TestDomain",
  "stations": [
    {
      "id": "plc1",
      "name": "FEEDER BAY 1",
      "points": [
        { "name": "plc1_mw",  "type": "real",  "label": "TIE-LINE FLOW", "unit": "MW" },
        { "name": "plc1_brk", "type": "state", "label": "BREAKER",
          "states": { "0": "OPEN", "1": "CLOSED" },
          "control": { "kind": "discrete", "mode": "sbo" } }
      ]
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `domain` | ICC domain name the server exposes |
| `name` | ICCP point name; the join key to the tag database |
| `type` | `real` (float point) or `state` (integer point) |
| `label`, `unit` | display only |
| `states` | integer to text map for a state point |
| `control.kind` | `discrete` (state) or `setpoint` (real) |
| `control.mode` | `direct` (default) or `sbo` (select-before-operate) |

## Tag database: `ingest/tags.json`

Declare devices once, then a tag per point.

```json
{
  "devices": {
    "plc1": { "driver": "modbus", "host": "10.30.0.11", "port": 502, "unit": 1 }
  },
  "tags": [
    { "point": "plc1_mw",  "type": "float", "device": "plc1",
      "kind": "holding", "register": 0, "decode": "float32", "word_order": "big" },
    { "point": "plc1_brk", "type": "int", "device": "plc1",
      "kind": "input", "register": 10, "decode": "uint16",
      "control": { "kind": "coil", "register": 0 } }
  ]
}
```

See {doc}`../modules/configuration` for the full driver field reference (Modbus and
DNP3).

## Validate before running

```bash
python3 scripts/validate_config.py config/scada.json ingest/tags.json
```

The launch scripts run this first, so a typo is reported clearly.

## Attack environments: `config/environments.json`

Attack scenarios reference their points by role rather than by a fixed name, so one
scenario plays on more than one grid. An environment binds those roles to a real
point model and a power-flow grid. Two ship:

| Environment | Point model | Grid | Use it for |
|-------------|-------------|------|-----------|
| `simple` | `config/scada.json` | `config/grid.json` | The four-bus lab: small and legible |
| `realistic` | `config/scada_utility.json` | `config/grid_utility.json` | The regional `grid-demo` grid: utility-scale, a real cascade |

Each environment entry has a `config` (point model), a `grid` (power-flow model), a
`roles` map (role name to point name), and a `stations` map (station role to
station). Select one with the dropdown on an Attack Scenario in the console, or with
`--env` on the command line:

```bash
python3 suite/tase2ctl.py run ukraine2015-attack --env realistic
```

To add an environment, give every role and station defined by the scenarios an entry
under a new key. See {doc}`scenarios` for how roles resolve and {doc}`attacks` for
the two grids side by side.

## Security profile

`PROFILE` selects the security posture on the launch scripts:

| Profile | Transport | Command path |
|---------|-----------|--------------|
| `insecure` (default) | plaintext | open to any peer |
| `hardened` | mutual TLS | command allowlist (loopback) |

```bash
./scripts/gen_certs.sh
PROFILE=hardened ./scripts/55_run_scada.sh
```

```{warning}
The `insecure` profile is for closed ranges and attack demonstrations only. Use
`hardened` for any real trust boundary, and segment the network.
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SCADA_CONFIG` | `config/scada.json` | point model path |
| `ENVIRONMENT` | `simple` | attack environment (`simple` or `realistic`); sets the scenario's points and grid |
| `TAGS` | demo tags | tag database path |
| `TASE2_PORT` | `102` | server TCP port |
| `HTTP_PORT` | `8800` | HMI port |
| `HTTP_HOST` | `127.0.0.1` | HMI bind address |
| `POLL_SEC` | `1` | poll period |
| `INJECT_HOLD` | `30` | seconds the server pins a written value |
| `PROFILE` | `insecure` | security profile |
