# Built-in modules

Drivers live in `ingest/tase2_ingest.py` and are registered in the `DRIVERS` map.
Three ship today.

## `stub`

A development stand-in. Returns a fixed value, optionally with `jitter` so it moves,
or `down: true` to simulate an unreachable device. For controllable points the
command loops back in memory, so the full control path works with no hardware. Used
for demos and tests. It is not real data.

## `modbus`

A Modbus TCP master over plain sockets, dependency free.

- Reads holding registers (function code 3) and input registers (function code 4).
- Decodes `uint16`, `int16`, `uint32`, `int32`, `float32`, with selectable
  `word_order` (`big` or `little`) for the 32-bit types.
- Writes commands as a coil (function code 5), a single register (6), or a float
  across two registers (16).
- One connection is shared per `host:port:unit`.

## `dnp3`

A DNP3 (IEEE 1815) master over TCP, dependency free (`ingest/dnp3.py`).

- Reads binary inputs (group 1) and analog inputs (group 30, variations 1, 2, 5, 6).
- Operates a control relay output block (group 12 CROB) by select-before-operate or
  direct operate.
- One association is shared per `host:port:outstation`.
- A bench outstation simulator is provided (`ingest/dnp3_outstation_sim.py`).

## Robustness

All drivers that parse peer-controlled bytes are hardened to fail cleanly on
malformed input. The fuzz suite exercises them. See {doc}`../developer/testing`.
