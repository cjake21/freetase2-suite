# Caldera usage: a tester's guide to the TASE.2 plugin

This guide is for a tester who is driving the TASE.2 / ICCP Caldera plugin against
a FreeTASE2 Suite server (or any TASE.2 server you are authorized to test). It
explains every capability the plugin ships, what each one is for, when to reach for
it, what to type into every input, and what you have to learn first before a given
capability will work. It also gives a recommended order to run things in.

The plugin can do a lot, but almost all of the friction testers hit is small and
avoidable: a wrong port, a stray backslash in an object name, writing a whole
structure instead of a leaf, or an association that was briefly refused. Read the
mental model and the golden rules first. They will save you the most time.

```{contents}
:local:
:depth: 2
```

## What this plugin is

The plugin gives Caldera a set of abilities that speak MMS / TASE.2 to a server on
TCP port 102. Each ability is one command line that runs a
small payload on the Caldera agent. There are two payloads:

- `tase2_actions` is the everyday tool: recon, read, write, control, inject,
  transfer-set work, TLS checks, and captures.
- `tase2_fuzz` is a separate, gated robustness tool used only for fuzzing.

You drive everything with **facts**. A fact is a named value, for example
`tase2.server.ip = 10.20.0.10`. An ability's command line contains placeholders
like `#{tase2.server.ip}`, and Caldera fills them in from your facts before it runs
the command on the agent. When the payload finishes, it prints lines of the form
`FACT <name> <value>`, and Caldera turns those back into new facts that later
abilities can use.

## The five minute mental model

### How a run actually works

1. You set facts (the target address, an object name, a value, and so on).
2. Caldera substitutes your facts into the ability's command line.
3. The agent runs the payload, which opens a fresh TASE.2 association, does the one
   action, prints results, and closes the association.
4. The output is parsed back into facts.

Two consequences fall out of this that trip people up. First, every ability opens
its **own** association, so if the server only allows a few associations at once,
abilities can be refused when the HMI and other abilities are already connected
(see the golden rules). Second, the value you put in a fact is passed through a
shell, so the way you quote object names matters.

### The object model you are touching

A TASE.2 server publishes a **domain** (for example `TestDomain`) that contains
named objects. The objects you care about fall into a few kinds. The names below
are from the field demo; yours may differ, which is why you fingerprint first.

- **Indication points**, such as `plc1_mw` or `plc1_avr`. These are measurements
  and statuses. They are structures with leaves like `Value`, `Flags`, and
  `TimeStamp`. You read these to see the live value, and you can falsify them.
- **Control objects**, such as `plc1_avr_ctl` or `plc1_brk_ctl`. These are how a
  remote operator actuates a device. They are structures with leaves like
  `Command` (the operate value), `Tag` (the select handle), `Status` (a readback),
  and `SBO` (a select-before-operate flag). You actuate these with Block 5 Control.
- **Transfer sets**, such as `DSTransferSet01`. These are the rules that decide
  which points get reported upstream, and how often. You read and manipulate these
  to control when a value reaches the remote control center.
- **Identity and agreement objects**, such as `TASE2_Version`,
  `Supported_Features`, and `Bilateral_Table_ID`. These tell you what the server is
  and what it supports.

```{important}
An indication point and its control object are two different objects. `plc1_avr`
is the measurement (leaf `Value`). `plc1_avr_ctl` is the control (leaves `Command`,
`Tag`, `Status`, `SBO`). To read the value you use `plc1_avr`. To actuate it you
use `plc1_avr_ctl`. Introspecting the point will never show you control leaves,
because it has none.
```

### Recon first, always

You do not guess object names, leaf names, or types. You discover them. Run
**Fingerprint Endpoint** to learn the domain, the objects, and the supported
blocks, then run **Object Type Introspection** on any object you intend to write,
to read its exact leaf names and types. Every action capability below tells you
which recon capability supplies the names it needs.

## Golden rules that prevent most problems

```{warning}
Read these once. Most failures testers report are one of these eight things.
```

1. **Set the port to match the target.** The `tase2.server.port` fact defaults to
   `102`, the standard TASE.2 / ICCP port. If your target listens on a different
   port, set this fact to match it, or you get `connect failed
   (connection-rejected)`.

2. **No backslash in object names inside Caldera facts.** Write the object as
   `plc1_avr_ctl$Command`, not `plc1_avr_ctl\$Command`. The ability already wraps
   the value in single quotes, so the `$` is safe on its own. A backslash you add
   becomes a literal character and corrupts the name, giving
   `could not introspect ...`. The backslash is only needed when you type a name
   directly on a command line where it is not quoted.

3. **Block 5 Control takes the base object, Write Arbitrary takes a leaf.** For the
   Block 5 Control ability, `tase2.ctl.object` is the base name only, for example
   `plc1_avr_ctl`. The ability adds the `Tag`, `Command`, and `Status` leaves for
   you. For Write Arbitrary Object, `tase2.object.write` is the full leaf path, for
   example `plc1_avr_ctl$Command`.

4. **Do not write a whole structure.** Writing `plc1_avr_ctl` directly fails with
   `is a STRUCTURE; write a leaf component`. Target a leaf such as
   `plc1_avr_ctl$Command`.

5. **Component facts are bare names.** Set `tase2.comp.select = Tag`, not
   `tase2.comp.select = tase2.comp.select=Tag`. Put only the leaf name in the value
   box, not the whole `name=value` string.

6. **Use Block 5 Control for select-before-operate objects.** If a control object
   has a `Tag` and an `SBO` leaf, it expects a select then an operate. A single
   bare write to its `Command` leaf is not a valid operate and will be refused or
   ignored. Use Block 5 Control, which does the select then the operate. Use Write
   Arbitrary only for objects that are not select-before-operate.

7. **`connection-rejected` is usually transient.** A TASE.2 server allows only a
   limited number of associations at once. When the HMI and other abilities hold
   slots, a new ability can be refused. Re-run it, and avoid running abilities in
   parallel. If it happens constantly, raise the server's maximum connections.

8. **Get leaf names and types from introspection.** Run Object Type Introspection
   on an object before you write it. It prints each leaf and its type. Never guess
   the leaf names or the value type.

## Step one: set your target

Before any ability, set the facts that describe the target. The easiest way is to
select the `TASE.2 Target (set these first)` fact source on your operation and edit
it. These facts are shared by almost every ability.

| Fact | What it is | How to fill it |
|------|------------|----------------|
| `tase2.server.ip` | target server address | the IP of the TASE.2 server, for example `10.20.0.10` or `127.0.0.1` |
| `tase2.server.port` | TCP port | `102`, the standard TASE.2 port. Set it to match your target if it uses a different port. |
| `tase2.idspec` | association identity | `none` for a permissive server. For a gated server, a single string `key=value;key=value;...` using keys `remote_aptitle`, `remote_ae`, `local_aptitle`, `local_ae`, `psel`, `ssel`, `tsel`. |
| `tase2.domain.name` | the ICC domain | the domain the objects live in, for example `TestDomain`. You confirm this with recon. |

```{tip}
If the server gates the association on identity, get the AP-Titles and selectors
from the bilateral agreement and put them in `tase2.idspec`. If you can connect
with `none`, the server is permissive and you can leave it.
```

## The capabilities

Each capability below is described the same way: **why** you use it, **when** to
reach for it and what you must already know, **where** it sits in an engagement,
**how** to fill every input, and the **gotchas**. The "you must obtain first"
column of each input table names the capability that gives you that value.

### Group A: recon and footprinting

This is where you start. All of it is read-only and safe to run first.

#### Association Probe

- **Why:** confirm you can associate at all, and if not, learn why (identity,
  selector, bilateral-table, or TLS).
- **When:** the very first thing you run against a new target, to validate your
  address and identity facts before anything else.
- **Where:** reconnaissance. ATT&CK T0846.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.server.ip` | target IP | you set it |
| `tase2.server.port` | target port | you set it |
| `tase2.idspec` | `none` or the identity string | the bilateral agreement, if gated |

- **Output:** confirms association, and prints `tase2.server.version` and
  `tase2.server.bltid` when the reads succeed.
- **Gotcha:** if this fails with `connection-rejected`, fix the port first, then
  the identity. This ability is the cleanest way to debug those two facts.

#### Fingerprint Endpoint

- **Why:** get a complete map of the server in one shot: its identity, the domain
  list, every object with its MMS type, and which conformance blocks it supports.
- **When:** immediately after Association Probe. This is your primary recon, and it
  is what tells you which other capabilities are even possible.
- **Where:** reconnaissance. ATT&CK T0888.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.server.ip` | target IP | you set it |
| `tase2.server.port` | target port | you set it |
| `tase2.idspec` | `none` or identity | the bilateral agreement, if gated |

- **Output:** `tase2.fp.version`, `tase2.fp.features`, `tase2.fp.bltid`,
  `tase2.fp.domain`, one `tase2.fp.object` per object as `Base=TYPE`,
  `tase2.fp.has_control`, `tase2.fp.has_transfersets`, and `tase2.fp.blocks`
  (for example `block1,block2,block5`).
- **Gotcha:** the blocks list comes from the standard `Supported_Features` value,
  so it is authoritative. If `has_control` is false, do not expect Block 5 Control
  to work. If `has_transfersets` is false, the Block 2 abilities will not apply.

#### Enumerate Domains

- **Why:** learn the ICC domain name or names with no prior knowledge.
- **When:** if you do not know the domain, or you want the domain auto-populated as
  a fact for later abilities.
- **Where:** reconnaissance. ATT&CK T0846.
- **How (inputs):** `tase2.server.ip`, `tase2.server.port`, `tase2.idspec`.
- **Output:** one `tase2.domain.name` fact per domain. This is the capability that
  seeds the `tase2.domain.name` that almost every other ability reads, so running
  it once means you do not have to type the domain by hand.

#### Enumerate Bilateral Table and Objects

- **Why:** read the server's `Bilateral_Table_ID` and list the top-level objects in
  a domain.
- **When:** after you know the domain, to get the object names and the bilateral
  table id in one step.
- **Where:** reconnaissance. ATT&CK T0888.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.server.ip` / `tase2.server.port` | target | you set it |
| `tase2.domain.name` | the domain | Enumerate Domains, or Fingerprint Endpoint |

- **Output:** `tase2.server.bltid`, and one `tase2.object.name` per top-level
  object.
- **Gotcha:** Fingerprint Endpoint already gives you the objects and the bilateral
  table id, so you often do not need this separately. Use it when you want just the
  object list for one domain.

#### Object Type Introspection

- **Why:** read an object's exact leaf names and types. This is how you learn that
  `plc1_avr_ctl` has `Command` (FLOAT), `Tag` (string), `Status` (integer), and
  `SBO` (integer).
- **When:** before you write, control, inject, or fuzz any object. This is the
  capability that supplies the leaf and component names every write-type ability
  needs.
- **Where:** reconnaissance. ATT&CK T0861.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.read.domain` | the domain | Enumerate Domains or Fingerprint |
| `tase2.read.item` | the object base name, for example `plc1_avr_ctl`. Use `-` as the domain for VMD-scope objects. | Fingerprint Endpoint or Enumerate Bilateral Table |

- **Output:** one `tase2.objtype Name=TYPE` line per leaf.
- **How to read it for control work:** the value-typed leaf (FLOAT or INTEGER) is
  the operate component, the `VISIBLE_STRING` leaf is the select, the integer
  readback is the status. A FLOAT operate means you use kind `real`, an INTEGER
  operate means kind `discrete`.

```{admonition} Worked example
:class: note
Running Object Type Introspection on `plc1_avr_ctl` prints:

    plc1_avr_ctl: STRUCTURE
      Command: FLOAT
      Tag:     VISIBLE_STRING(32)
      Status:  INTEGER(8)
      SBO:     INTEGER(8)

So for Block 5 Control you set `tase2.comp.operate = Command`,
`tase2.comp.select = Tag`, `tase2.comp.status = Status`, and `tase2.ctl.kind =
real` (because `Command` is a FLOAT). The `SBO` leaf tells you this is a
select-before-operate control, so you use Block 5 Control, not Write Arbitrary.
```

#### Read Arbitrary Object

- **Why:** read any object or leaf and see its current value.
- **When:** to take a baseline before an injection, to confirm a write landed, or
  just to inspect a value.
- **Where:** collection. ATT&CK T0861.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.read.domain` | the domain (or `-` for VMD scope) | Enumerate Domains or Fingerprint |
| `tase2.read.item` | the object or leaf, for example `plc1_avr` or `plc1_avr_ctl$Command`. No backslash. | Fingerprint or Object Type Introspection |

- **Output:** `tase2.read.value`.
- **Gotcha:** reading a structure returns the whole tuple, for example
  `{150.000000,hmi,1,0}`. The values are in declaration order, the same order
  introspection lists the leaves.

### Group B: access and trust mapping

#### Associate Using Bilateral Table ID

- **Why:** present a bilateral table id and confirm whether the server accepts and
  matches it. This models a peer associating under a specific agreement.
- **When:** after recon, once you know the bilateral table id.
- **Where:** lateral movement. ATT&CK T0859.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.server.bltid` | the bilateral table id, for example `TestBilTab` | Enumerate Bilateral Table, Fingerprint, or Association Probe |

- **Output:** prints the server id, the presented id, and whether they match.

#### Probe Authorization Boundary (Read) and (Write)

- **Why:** map what an association identity is allowed to touch, and find objects
  that are reachable but not in the published list (reachable beyond the declared
  agreement). The write variant additionally proves write access without changing
  any value, by writing each object's current value back to itself.
- **When:** after you have an object list, to test the access agreement.
- **Where:** read variant is discovery (T0888), write variant is
  impair-process-control (T0855).
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.trust.identity_label` | a label for this identity, for example `bilA`, used to tag the results | you choose it |
| `tase2.trust.candidates` | a comma-separated list of object names to probe, or empty to probe the published list | Fingerprint (object names). Add names you suspect exist but are not listed to test for hidden objects. |

- **Output:** `tase2.trust.identity`, one `tase2.trust.edge` per object as
  `identity|object|read-or-write|allow-or-deny`, and `tase2.trust.boundary` for any
  object readable but not in the published list. The write variant also emits
  `tase2.trust.restore` confirmations.
- **Gotcha:** the read variant never writes. The write variant only writes a value
  back to itself, so it does not change state, but it is still an active write, so
  only run it against a target you are cleared to test.

### Group C: transport and Secure ICCP

```{note}
The two certificate-based abilities (Probe TLS Enforcement and Present Untrusted
Certificate) need a payload built with TLS support, and the cert and key files must
exist on the agent at the paths you give. The plaintext ability works on any build.
```

#### Attempt Plaintext Fallback

- **Why:** test whether the server enforces Secure ICCP. If a plaintext association
  succeeds against a server that should require TLS, that is a finding, because it
  means the link can be driven with no certificates.
- **When:** during transport recon, before you rely on TLS being mandatory.
- **Where:** lateral movement. ATT&CK T0830.
- **How (inputs):** `tase2.server.ip`, `tase2.server.port`, `tase2.idspec`.
- **Output:** `tase2.tls.plaintext_fallback` is `true` (the finding) or `false`
  (transport enforced), plus `tase2.tls.result`.

#### Probe TLS Enforcement

- **Why:** confirm that a valid client certificate associates over TLS, and record
  the result.
- **When:** transport recon, with valid cert material in hand.
- **Where:** discovery. ATT&CK T0869.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.tls.ca` | path to the CA cert on the agent | issued for the engagement |
| `tase2.tls.cert` | path to the client cert | issued for the engagement |
| `tase2.tls.key` | path to the client key | issued for the engagement |

- **Output:** `tase2.tls.result`, `tase2.tls.mutualauth`.

#### Present Untrusted Certificate

- **Why:** test whether the server rejects a client certificate it should not
  trust. If it associates anyway, mutual authentication is weak.
- **When:** transport recon, to check certificate validation strictness.
- **Where:** discovery. ATT&CK T0869.
- **How (inputs):** `tase2.tls.ca`, and `tase2.tls.bad_cert` plus
  `tase2.tls.bad_key` pointing at an untrusted or expired cert and key on the agent.
- **Output:** `tase2.tls.untrusted_cert` is `rejected` (correct) or `accepted`
  (the finding).

### Group D: reporting recon and manipulation (Block 2)

#### Read Transfer Set Config (Block 2)

- **Why:** read a transfer set's reporting configuration, the attributes that
  decide whether, when, and how a group of points is reported upstream.
- **When:** after you know which transfer set carries the point you care about, and
  before you manipulate it.
- **Where:** collection. ATT&CK T0861.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.ts.name` | the transfer set, for example `DSTransferSet01` | Fingerprint (object list) or Enumerate Bilateral Table |

- **Output:** one `tase2.ts.<attr>` fact per attribute (for example
  `tase2.ts.Interval`, `tase2.ts.DataSetName`, `tase2.ts.Status`), and
  `tase2.ts.name`. These attribute names are exactly what you feed to the manipulate
  ability next.

#### Manipulate Transfer Set Reporting (Block 2)

- **Why:** change one reporting attribute, for example shorten the `Interval`, turn
  on report-by-exception, or flip `Status` to enable a dormant set, so a value you
  injected is reported upstream quickly.
- **When:** after Read Transfer Set Config, so you know the attribute names.
- **Where:** impair-process-control. ATT&CK T0836.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.ts.name` | the transfer set | Fingerprint or Read Transfer Set Config |
| `tase2.ts.attr` | the attribute to write, for example `Interval` or `Status` | Read Transfer Set Config |
| `tase2.ts.type` | leave as `auto` to let the payload match the server's type | not needed |
| `tase2.ts.newvalue` | the new value, for example `5` | you choose it |

- **Gotcha:** the FreeTASE2 server applies the change to its reporting engine but
  does not refresh its read cache, so a read-back can still show the old value.
  Confirm the effect from the server log or from captured reports, not from a
  read-back.

### Group E: process impact

#### Inject False Telemetry (FDI)

- **Why:** write a believable false value into an indication point's value leaf, so
  the remote control center sees falsified telemetry.
- **When:** after recon, once you know the point name and (if not `Value`) its value
  leaf name.
- **Where:** impair-process-control. ATT&CK T0856.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.fdi.point` | the point base name only, for example `plc1_mw` (no `$`, no backslash). The ability appends the value leaf. | Fingerprint (point names) |
| `tase2.fdi.value` | the false value, for example `47.5` | you choose it |
| `tase2.comp.value` | the value leaf name, normally `Value`. Set `-` for a scalar point with no leaf. | Object Type Introspection on the point |

- **Output:** `tase2.fdi.point`, `tase2.fdi.value`.
- **Gotcha:** on a server that drives points from a live simulation, the simulation
  can overwrite your value before a report carries it. The FreeTASE2 server has an
  injection-hold option to hold a written value long enough to be reported.

#### Block 5 Control (Select-Before-Operate)

- **Why:** actuate a control object the proper way, as a select then an operate then
  a status read, which is exactly what a real HMI does.
- **When:** to operate any control object, especially one that enforces
  select-before-operate (it has `Tag` and `SBO` leaves).
- **Where:** impair-process-control. ATT&CK T0855.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.ctl.object` | the control object base name only, for example `plc1_avr_ctl` (no `$`, no leaf, no backslash). The ability adds the leaves. | Fingerprint (object names) |
| `tase2.ctl.kind` | `real` for a FLOAT operate, `discrete` for an INTEGER operate, or `auto` | Object Type Introspection (the operate leaf type) |
| `tase2.ctl.value` | the setpoint or command, for example `150` | you choose it |
| `tase2.comp.select` | the select leaf name, normally `Tag` | Object Type Introspection |
| `tase2.comp.operate` | the operate leaf name, normally `Command` | Object Type Introspection |
| `tase2.comp.status` | the status leaf name, normally `Status` | Object Type Introspection |

- **Output:** `tase2.ctl.object`, `tase2.ctl.value`, `tase2.ctl.status`.
- **Gotcha:** the object fact is the base name only. If you put a leaf here (for
  example `plc1_avr_ctl$Command`) it will not work, because the ability appends the
  leaves itself. Put only `plc1_avr_ctl`.

#### Write Arbitrary Object

- **Why:** a single raw MMS write to any leaf. Use it for objects that are not
  select-before-operate, for transfer-set members, parameters, or to poke one
  component on purpose.
- **When:** when a bare write is the right model. For an SBO control, use Block 5
  Control instead.
- **Where:** impair-process-control. ATT&CK T0855.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.object.write` | the full leaf path, for example `plc1_mw$Value` (no backslash) | Fingerprint plus Object Type Introspection |
| `tase2.write.type` | leave as `auto` to match the server's type | not needed |
| `tase2.object.newvalue` | the value to write | you choose it |

- **Gotcha:** this is the ability where testers most often add a stray backslash or
  try to write the whole structure. Use `plc1_mw$Value`, not `plc1_mw\$Value`, and
  never the bare `plc1_mw`.

### Group F: verification

#### Monitor Points

- **Why:** poll one or more points over a window and watch their values move, for
  example before and after an injection.
- **When:** around an injection or control action, to see the effect.
- **Where:** collection. ATT&CK T0801.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.monitor.points` | comma-separated point names, for example `plc1_mw,plc1_brk` | Fingerprint |
| `tase2.monitor.seconds` | how long to poll, for example `15` | you choose it |

- **Output:** one `tase2.monitor.<point>` fact per point with its last reading.

#### Capture Transfer Set Reports (Verification)

- **Why:** prove the false data actually reaches the remote control center, by
  enabling a transfer set and capturing the reports the server pushes.
- **When:** the closing step of a false-data flow, to confirm the value lands
  upstream.
- **Where:** collection. ATT&CK T0801.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Enumerate Domains or Fingerprint |
| `tase2.ts.name` | the transfer set to enable and listen on | Fingerprint or Read Transfer Set Config |
| `tase2.report.points` | comma-separated points to bind into the reported data set, for example `plc1_mw,plc1_brk` | Fingerprint |
| `tase2.report.seconds` | how long to listen, for example `15` | you choose it |

- **Output:** `tase2.report.values` per report and `tase2.report.count`.

### Group G: robustness fuzzing (gated)

```{warning}
These abilities are DoS-class. They run the separate `tase2_fuzz` payload and
refuse to run unless the target IP is on `tase2.fuzz.allowlist` and the
authorization flag is present in the command. Use a disposable server you own, and
never run them against production. The Check Server Liveness ability is read-only
and is not gated.
```

#### Check Server Liveness

- **Why:** a read-only watchdog. It reports `up`, `degraded`, or `down`. Run it
  after a fuzz case to record whether the server survived.
- **How (inputs):** `tase2.server.ip`, `tase2.server.port`, `tase2.idspec`.
- **Output:** `tase2.fuzz.liveness`.

#### Fuzz PDU Value Field

- **Why:** stress how the server handles bad values (out of range, wrong type,
  oversized) on a point's value leaf.
- **How (inputs):**

| Fact | What to put | You must obtain first |
|------|-------------|-----------------------|
| `tase2.domain.name` | the domain | Fingerprint |
| `tase2.fuzz.point` | the point base name, or an explicit `Base$Member` | Fingerprint |
| `tase2.comp.value` | the value leaf, normally `Value`, or `-` for a scalar point | Object Type Introspection |
| `tase2.fuzz.seed` | a number, the same seed replays the same cases | you choose it |
| `tase2.fuzz.cases` | how many cases to run, for example `12` | you choose it |
| `tase2.fuzz.allowlist` | comma-separated allowed target IPs, must include your target | you set it |

- **Output:** `tase2.fuzz.seed`, `tase2.fuzz.case`, `tase2.fuzz.effect`,
  `tase2.fuzz.liveness`, and `tase2.fuzz.crash_corpus` on a crash.

#### Fuzz SBO Sequence

- **Why:** abuse the Block 5 select-before-operate interlock with out-of-sequence
  operations (operate with no select, double select, out-of-range command).
- **How (inputs):** `tase2.domain.name`, `tase2.fuzz.object` (the control base
  name, from Fingerprint), `tase2.comp.select` and `tase2.comp.operate` (from
  Object Type Introspection), and `tase2.fuzz.allowlist`.

#### Fuzz Bilateral Table ID

- **Why:** probe how the server handles malformed bilateral table identities.
- **How (inputs):** `tase2.domain.name` and `tase2.fuzz.allowlist`.

## What you must obtain first, and where it comes from

This table is the heart of the guide. Before you can run a write-type capability,
you need certain values, and each comes from a specific recon capability.

| Value you need | Used by | Get it from |
|----------------|---------|-------------|
| Target IP and port | everything | you set them |
| Association identity (`tase2.idspec`) | everything, if the server is gated | the bilateral agreement |
| Domain name | almost everything | Enumerate Domains (auto-fills the fact), or read it from Fingerprint |
| Object base names (points, controls, transfer sets) | Inject, Block 5 Control, Write Arbitrary, fuzz, monitor, capture | Fingerprint Endpoint, or Enumerate Bilateral Table |
| Leaf names and types (`Command`, `Tag`, `Status`, `Value`) | Block 5 Control, Inject, Write Arbitrary, fuzz | Object Type Introspection on that object |
| Operate kind (`real` or `discrete`) | Block 5 Control | Object Type Introspection (FLOAT is real, INTEGER is discrete) |
| Bilateral table id | Associate Using Bilateral Table ID | Enumerate Bilateral Table, Fingerprint, or Association Probe |
| Transfer set name | Read Transfer Set Config, Manipulate, Capture | Fingerprint or Enumerate Bilateral Table |
| Transfer set attribute names | Manipulate Transfer Set Reporting | Read Transfer Set Config |
| Supported blocks and capabilities | deciding what is even possible | Fingerprint Endpoint |

```{tip}
The short version: Fingerprint Endpoint gives you objects and blocks, Object Type
Introspection gives you the leaves and types, and only then do the write-type
abilities have everything they need.
```

## Recommended chaining order

A clean engagement runs in four phases. Each phase produces what the next one
needs.

### Phase 1: recon and access mapping (read-only, safe)

1. **Association Probe** to validate the address and identity.
2. **Fingerprint Endpoint** to get the domain, objects, types, and blocks.
3. **Object Type Introspection** on any object you plan to write, to read its
   leaves and types.
4. Optionally **Enumerate Domains** to auto-fill the domain fact, **Enumerate
   Bilateral Table** for the object list and bilateral id, and **Probe
   Authorization Boundary (Read)** to map the access agreement.
5. Optionally **Attempt Plaintext Fallback** and the TLS checks for transport.

### Phase 2: baseline

6. **Read Arbitrary Object** or **Monitor Points** on the target point to record
   its normal value before you change anything.

### Phase 3: act

7. To falsify a measurement: **Inject False Telemetry**, then if needed **Read
   Transfer Set Config** and **Manipulate Transfer Set Reporting** so it reports
   upstream.
8. To actuate a control: **Block 5 Control** (for SBO controls), or **Write
   Arbitrary Object** (for non-SBO objects).

### Phase 4: verify

9. **Monitor Points** and **Capture Transfer Set Reports** to prove the value or
   action reached the remote control center.

The plugin also ships these as ready-made adversaries you can run end to end:
"TASE.2 Bilateral Table Abuse" (a recon-to-write chain) and "ICCP False Data
Injection" (the full inject, manipulate reporting, control, and verify chain). Use
them as a starting point, then set the object and component facts to match your
target from the recon you did in Phase 1.

### A concrete first run

To set the AVR setpoint on the field demo, the exact sequence is:

1. Association Probe on `127.0.0.1:102`, identity `none`. Confirms the link.
2. Fingerprint Endpoint. Shows `plc1_avr_ctl` exists and the server supports
   block5.
3. Object Type Introspection on `plc1_avr_ctl`. Shows `Command` (FLOAT), `Tag`,
   `Status`, `SBO`, so the operate kind is `real` and it is select-before-operate.
4. Read Arbitrary Object on `plc1_avr_ctl` to baseline it.
5. Block 5 Control with `tase2.ctl.object = plc1_avr_ctl`, `tase2.ctl.kind = real`,
   `tase2.ctl.value = 150`, components left at `Tag` / `Command` / `Status`.
6. Read Arbitrary Object on `plc1_avr_ctl` again to confirm `Command` is now 150.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `connect failed (connection-rejected)` right away | wrong port, or wrong identity | set `tase2.server.port` to match the target, then check `tase2.idspec` |
| `connect failed (connection-rejected)` intermittently | the server's association slots are full | re-run the ability, do not run abilities in parallel, or raise the server's max connections |
| `could not introspect ... for auto type` | a backslash in the object name, or the object does not exist | remove the backslash (use `plc1_avr_ctl$Command`, not `\$`), and confirm the name with Fingerprint |
| `is a STRUCTURE; write a leaf component` | you targeted the whole object | target a leaf, for example `plc1_avr_ctl$Command` |
| introspection shows only `Value` | you introspected the indication point, not the control object | introspect the `_ctl` object for control leaves |
| a bare write to a control is refused or has no effect | the object is select-before-operate | use Block 5 Control, which does select then operate |
| component name comes out as `tase2.comp.select=Tag` | you put the whole `name=value` in the value box | put only the bare name, `Tag` |
| a fuzz ability refuses to run | the target is not allow-listed | add the target IP to `tase2.fuzz.allowlist` |

## See also

- {doc}`attacks` for the built-in multi-stage attacks and how they read on the wire.
- {doc}`tase2-on-the-wire` for the MMS and TASE.2 framing.
- {doc}`configuration` for the server's security model and association settings.
