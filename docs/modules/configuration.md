# Module configuration

Driver fields go on a tag, or on a `devices` entry that the tag references with
`device`. A field set on the tag overrides the device default.

## Common tag fields

| Field | Meaning |
|-------|---------|
| `point` | ICCP point name (must match `config/scada.json`) |
| `type` | `float` (WRITEF/quality float) or `int` |
| `driver` | `stub`, `modbus`, or `dnp3` |
| `scale`, `offset` | engineering conversion: `value = raw * scale + offset` |
| `control` | present on a controllable point; protocol-specific write target |

## `modbus`

| Field | Default | Meaning |
|-------|---------|---------|
| `host`, `port`, `unit` | `502`, `1` | device address |
| `kind` | `holding` | `holding` (FC 3) or `input` (FC 4) |
| `register` | required | start register |
| `decode` | `uint16` | `uint16`, `int16`, `uint32`, `int32`, `float32` |
| `word_order` | `big` | `big` (ABCD) or `little` (CDAB) for 32-bit |
| `control.kind` | `coil` | `coil` (FC 5), `holding` (FC 6), `float32` (FC 16) |
| `control.register` | required for control | write target |

## `dnp3`

| Field | Default | Meaning |
|-------|---------|---------|
| `host`, `port` | `20000` | outstation address |
| `outstation`, `master` | `10`, `1` | DNP3 addresses |
| `group` | `30` | `1` binary input, `30` analog input |
| `variation` | `5` | analog 1/2/5/6; binary 2 |
| `index` | required | point index |
| `control.index` | read index | CROB index |
| `control.sbo` | `true` | select-before-operate, or `false` for direct |

## `stub`

| Field | Default | Meaning |
|-------|---------|---------|
| `value` | `0` | base value |
| `jitter` | `0` | adds `jitter*sin(t)` so the value moves |
| `down` | `false` | every read fails (simulate an unreachable device) |
| `control` | absent | `{}` to make a stub point controllable (loops back in memory) |
