# FAQ

**Is this a production ICCP gateway?**
No. It is a testbed node. It interoperates and is robust, but it does not implement
the full IEC 60870-6-802 type catalogue or carrier-grade redundancy. See
{doc}`../overview/architecture` and {doc}`roadmap`.

**Do I need hardware to try it?**
No. The stub driver and the bundled DNP3 outstation simulator run the full pipeline,
including control, in memory.

**Does it cost anything?**
No. It is open and built on libIEC61850 (GPL). Comparable TASE.2 stacks are
commercial.

**What field protocols are supported?**
Modbus TCP and DNP3 over TCP today. Others plug in as driver modules. See
{doc}`../modules/index`.

**Can a real ICCP client talk to it?**
Yes. The interop test drives the server from an independent MMS stack. Point your
client at the server port (default 102) and the configured domain.

**How do I send a command to a device?**
From the HMI or `POST /api/control`. For select-before-operate points, select first.
The command travels over ICCP to the gateway and down to the device. See
{doc}`../guides/operations`.

**Why does a control take a few seconds to show?**
The loop is command read, write down, read back, report. Lower `POLL_SEC` for a
snappier response.

**Is the HMI API authenticated?**
No. Bind it to loopback or a trusted segment. See {doc}`../api/auth`.

**How do I make it secure?**
Run the `hardened` profile (mutual TLS plus command allowlist) and segment the
network. See {doc}`../guides/configuration`.

**Can it run thousands of points?**
It is sized for testbeds (hundreds of points across a few stations). Large-scale
sizing is on the roadmap.
