# Why it exists

Every comparable TASE.2 / ICCP stack is commercial and closed. Researchers,
educators, and OT security teams who need a real ICCP node in a lab have had no
open, reproducible option. That is the gap this tool fills.

The design goals follow from that:

- **Open and reproducible.** No license cost. Pinned dependencies and a one
  command build, so the same node comes up the same way every time.
- **Real on the wire.** It produces genuine TASE.2 / MMS and DNP3 traffic, not a
  mock, so captures, parsers, and intrusion detection see authentic protocol.
- **Config driven.** The published point model and the field mapping are files, so
  you can shape the node to match whatever your testbed contains.
- **Both soft and hardened.** The same tool runs as an open target for
  demonstrating attacks and as a mutual-TLS node with a command allowlist for
  testing defenses.
- **Robust.** Every parser that handles peer-controlled bytes is fuzz hardened, so
  the node is a dependable target rather than something that falls over when poked.

## Relationship to the simulator

The tool grew from a closed lab simulator whose values came from an internal
synthetic loop. That simulator was good for capture and training but connected to
nothing. This node is the real-environment superset: it keeps the simulation
capability (run without ingestion to get synthetic values, or use the stub driver)
and adds the southbound ingestion, control, multi-protocol, quality, and security
layers. See {doc}`../resources/roadmap` for the direction on unifying the two into
a single tool with explicit operating modes.
