# Running operations

"Operations" here means polling jobs and operator controls, the two things the node
does continuously.

## Polling

The gateway polls every tag every `POLL_SEC` seconds. Each value is written up with
quality and a time tag. There is nothing to start per poll; the gateway runs the
loop until stopped.

## Issuing a control

From the HMI: on a controllable point, use the operate buttons (discrete) or the
setpoint box (setpoint). For a select-before-operate point, press SELECT first, then
operate within the countdown.

From the API:

```bash
# direct or armed operate
curl -s -XPOST http://127.0.0.1:8800/api/control \
  -d '{"action":"command","item":"plc4_stat","value":1}'

# select-before-operate sequence
curl -s -XPOST http://127.0.0.1:8800/api/control -d '{"action":"select","item":"plc1_brk"}'
curl -s -XPOST http://127.0.0.1:8800/api/control -d '{"action":"command","item":"plc1_brk","value":1}'
```

See {doc}`../api/rest` for all actions.

## Select-before-operate behaviour

- A select arms the point for a timeout (about 28 seconds in the HMI, 30 at the
  server).
- Only the selecting connection may operate. An operate without a current selection
  is rejected by both the bridge and the server.
- A cancel or a successful operate clears the selection.

## Command allowlist (hardened profile)

In `hardened`, only allowlisted peers may command or inject. The local bridge and
gateway are on the loopback allowlist. An external ICCP peer can read and subscribe
but its writes and operates are denied.

## Stopping

Press `Ctrl+C` in the launcher. It stops the server, gateway, bridge, and agents.
