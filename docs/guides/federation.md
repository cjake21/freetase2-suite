# Federation and enforced bilateral tables

TASE.2 is an inter-control-center protocol: its whole reason for existing is to let
one utility's control center share grid data with another's across a tie line. What
each pair of centers agrees to share is written down in a bilateral table. Center A
might let Center B see the tie-line flow and the bus voltage, but not the rest of
its network, and let B operate one shared breaker but nothing else. The bilateral
table is that agreement, made explicit.

Every TASE.2 server publishes a bilateral table identifier so a partner knows which
agreement is in force. The harder part, the part most testbeds skip, is actually
enforcing it: making sure a partner can only see and command what its agreement
allows. This server does that.

## Turning it on

Point the server at a bilateral table file with `-B`, or use the federation
deployment, which wires it up for you:

```bash
python3 suite/tase2ctl.py run field-federated
```

That runs the normal ingestion stack with `config/blt.conf` enforced. On the bench
everything runs on loopback, where the local components have full access, so the
demo behaves normally. The enforcement bites for partner control centers, which are
scoped by the table.

## Writing a bilateral table

A table is a plain text file. Each line is one peer, matched by source IP, with the
rights it holds and the objects those rights apply to:

```text
# peer_ip    rights   objects
127.0.0.1    rcw      *
10.0.0.10    r        plc1_*
10.0.0.11    rc       plc2_*
10.0.0.12    r        rtu1_*
```

The rights are `r` to read and subscribe, `c` to control (operate), and `w` to
write or inject values. The objects are a comma-separated list of point names,
`prefix*` patterns, or `*` for everything. A rule that lists a point also covers its
control object, so `plc1_brk` covers `plc1_brk_ctl` too.

In the example above, the local suite (loopback) has full access, partner A may read
the Feeder Bay 1 points but command nothing, partner B may read Feeder Bay 2 and
operate its breaker, and the DNP3 partner sees only the RTU points. Anyone not in
the table is denied every data object.

## What enforcement actually does

With a table loaded, the server checks three things on every association:

- **Reads.** A peer reading a point outside its rule is refused. Only the handshake
  objects (the version, the supported features, the bilateral table id, and the
  transfer-set objects) stay readable for everyone, so a partner can still associate
  and discover the model before its data access is scoped.
- **Controls and injections.** An operate needs the `c` right on that object, and a
  value write or injection needs `w`. Without the right, the write is rejected, the
  same way the command allowlist rejects it, but now per object rather than all or
  nothing.
- **Reports.** Block 2 reports are scoped per peer. If a peer subscribes to a data
  set that includes a point it may not read, that member comes back withheld, zeroed
  and marked not-valid, so the report structure is intact but the data is not
  leaked.

A peer with no rule at all is denied every data object. This is default-deny, which
is the safe posture for a trust boundary.

## Honesty about the model

Peers are identified by source IP, the same as the command allowlist. That is fine
for a segmented lab and for demonstrating the model, but an IP can be spoofed on an
open network, so for a real trust boundary pair the bilateral table with the
hardened profile (mutual TLS), where each peer is also cryptographically
authenticated. Enforcement by IP plus authentication by certificate is the
combination that actually holds.

## Many control centers: the live tie

A single server with a bilateral table models one center sharing scoped data with
several partners. A real federation goes further: several control centers, each its
own server with its own points, exchanging agreed data across the ties between them.
The suite does this with a relay.

```bash
python3 suite/tase2ctl.py run federation-demo
```

That brings up two control centers. CC-A runs its own server with live data. CC-B
runs its own server with no local source. The relay (`suite/relay.py`) subscribes to
CC-A, receives its reports, and writes the agreed tie points into CC-B over real
ICCP. Open <http://127.0.0.1:8800> and you are looking at CC-B's screen: its
intertie view shows CC-A's tie-line flow, voltage, and breaker, data that CC-B never
measured but received across the tie, the same way one utility sees another's across
a real interconnect.

The federation is described in `config/federation.json`:

```json
{
  "centers": {
    "A": { "host": "127.0.0.1", "port": 10502, "config": "config/scada.json" },
    "B": { "host": "127.0.0.1", "port": 10602, "config": "config/scada_b.json" }
  },
  "ties": [
    { "from": "A", "to": "B",
      "points": { "plc1_mw": "tieA_mw", "plc1_kv": "tieA_kv", "plc1_brk": "tieA_brk" } }
  ]
}
```

Each center is a server (its own port, domain, and point model). Each tie names a
source center, a destination center, and the points to carry, mapping each source
point to the destination point that receives it. Add more centers and more ties,
including ties the other direction, to model a wider interconnect.

This is where the bilateral table and the relay meet. The relay connects to the
source center as an ordinary peer, so if that center enforces a bilateral table, the
relay only receives what the table allows. Put a table on CC-A (set `BLT` when you
launch, or `-B` on its server) and the tie carries exactly the agreed subset and no
more. That combination, a real tie plus enforced scoping, is what lets you emulate a
regional interconnect and study cross-utility attacks such as a compromised partner
poisoning data across the boundary, with the scoping real rather than cosmetic.
