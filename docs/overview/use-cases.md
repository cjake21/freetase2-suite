# Core use cases

## OT security research and training

- A realistic ICCP / DNP3 / Modbus target for intrusion detection and deep packet
  inspection testing.
- A parser and fuzz target. The node stays up under malformed traffic, so it is
  safe to point tooling at it.
- Adversary emulation. The published objects are standard MMS named variables and
  Block 5 device controls, so frameworks that read, write, and operate ICCP objects
  work against it. See {doc}`../modules/index` and {doc}`../api/index`.
- Red and blue exercises using the two security profiles: an open target to attack
  and a hardened target to defend.

## Power cyber-physical testbeds

- Wire the node to real PLCs or RTUs and publish their data northbound over ICCP to
  a control center or another node.
- Closed-loop control studies: an operator command travels to the field device and
  the result is read back, end to end.
- Co-simulation and hardware-in-the-loop rigs that need an interoperable ICCP node.

## Protocol interoperability bring-up

- A free node to test a third-party ICCP client or master against, with an
  interoperability test included that drives the server from an independent MMS
  stack.

## Education

- A complete, readable example of how field protocols, an ICCP publisher, reporting,
  control, and a SCADA HMI fit together, with no hardware required to run it.
