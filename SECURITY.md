# Security model and profiles

This node is built for OT security testbeds: adversary emulation, IDS and DPI
testing, parser and fuzz targets, and red/blue training. That shapes the security
design in two ways. It must be **robust** when attacked, and it must be runnable
**both insecure and hardened** so you can demonstrate attacks and test defenses
with the same tool.

This is testbed software. Do not connect it to production equipment, and do not
use the lab certificates it generates outside a lab.

## Profiles

Select with `PROFILE` on `scripts/55_run_scada.sh` (and the DNP3 demo).

### insecure (default)

Plaintext ICCP and an open command path. Any peer that can reach the port can
read, subscribe, and command. This is the right target for showing what an
unprotected intertie looks like and for exercising attacks (false data injection,
unauthorized control, replay, malformed-frame handling).

```bash
./scripts/55_run_scada.sh
```

### hardened

Mutual TLS (Secure ICCP) plus a command allowlist limited to loopback (the local
ingest and bridge). External peers can associate, read, and subscribe, but cannot
command or inject. Generate lab certificates first.

```bash
./scripts/gen_certs.sh
PROFILE=hardened ./scripts/55_run_scada.sh
```

Under the hood the hardened profile runs the server with
`-T -C server.crt -K server.key -A ca.crt -L 127.0.0.1` and drives the client
agents over TLS (`TASE2_TLS=1` with the client certificate). Both ends validate
the certificate chain against the lab CA.

## Controls in the server

These work in any profile and can be combined:

- **Command allowlist (`-L ip[,ip...]`).** Gates the command and injection
  direction (device control and indication-point writes) by peer IP. Reads and
  Block 2 subscription stay open. This matches OT practice: read widely, command
  narrowly. With no `-L`, the command path is open (the insecure default).
- **Select-before-operate.** Controls configured with `"mode": "sbo"` require a
  select from the same association before an operate, within a timeout. The server
  rejects an operate that was not selected, so a stray or replayed operate fails.
- **TLS / Secure ICCP (`-T -C -K -A`).** Server certificate and, when a CA is
  given, mutual TLS with chain validation. The agents speak TLS when
  `TASE2_TLS=1`.

## Robustness

Every parser that handles peer-controlled bytes is hardened to fail cleanly on
malformed input (a defined error, never a crash, an unexpected exception type, or
an unbounded loop):

- DNP3 data link and application parsing (master side)
- the DNP3 outstation simulator (reading a master)
- Modbus response parsing
- the HMI bridge control API (malformed requests return 4xx, never a 500 crash)

`tests/test_fuzz.py` throws thousands of random, truncated, and mutated-valid
inputs at these surfaces, and floods the live control API with malformed requests,
asserting the node stays up. Run the full suite with
`python3 -m unittest discover -s tests`.

## Known limits (do not rely on these for real protection)

- The command allowlist is by source IP and gates writes, not association. Without
  TLS, source IPs can be spoofed; use the hardened profile for any real trust
  boundary.
- The bilateral table is published but its per-peer data scoping is not enforced.
- Lab certificates from `gen_certs.sh` are for testbeds only.
- See the README "OT safety" section before connecting to anything live.
