# Data model

## Points

Every published point is a structure in the server cache:

| Point type | Structure | Use |
|------------|-----------|-----|
| `real` | `{ Value: float, Flags: bitstring(8), TimeStamp: int }` | analog telemetry |
| `state` | `{ Value: int, Flags: bitstring(8), TimeStamp: int }` | status indication |

- **Value** is the engineering value (`raw * scale + offset` from the field).
- **Flags** is the TASE.2 quality byte. The gateway sets validity VALID on a good
  read and NOTVALID when the device read fails.
- **TimeStamp** is Unix seconds, the gateway acquisition time.

## Quality byte

```text
bit 7  6  5  4  3  2  1  0
       |  | source |valid|
       |  |        |
       |  +-- current source (bits 4-5): 0 telemetered, 1 entered, 2 calculated, 3 estimated
       +----- normal value (bit 6)
   bit 7 ---- time stamp quality
   bits 2-3 - validity: 0 VALID, 1 SUSPECT, 2 HELD, 3 NOTVALID  (values 0,4,8,12)
```

The HMI maps validity to GOOD, SUSPECT, HELD, or NOTVALID, and STALE when the link
goes silent.

## Control objects

A controllable point `X` gets a Block 5 device control object `X_ctl`:

```text
X_ctl = { Command, Tag, Status, SBO }
```

- **Command** is an integer for a discrete control or a float for a setpoint.
- **Tag** is an operator label.
- **Status** is set when the control is operated.
- **SBO** is the select register for select-before-operate devices.

Operating writes `Command`. For SBO, the client writes `SBO = 1` to select first.
The monitoring point `X` and the control object `X_ctl` are separate, so a command
and the field read-back never overwrite each other.

## Transfer sets and reports

A client defines a data set (a named variable list), binds it to a
`DSTransferSetNN`, and enables it. The server then sends Block 2 InformationReports
carrying each member's value, quality, and time tag, on change and on the integrity
interval.

## Configuration files

| File | Defines |
|------|---------|
| `config/scada.json` | stations, points, types, labels, units, control kind and mode |
| `ingest/tags*.json` | per-point field mapping: driver, address, decode, scaling, control target |

They meet at the point `name`. See {doc}`../guides/configuration`.
