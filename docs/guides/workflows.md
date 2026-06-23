# Common workflows

## Stand up a node for a real testbed

1. Write `config/scada.json` (stations and points).
2. Write `ingest/tags.json` (field mapping). Keep point names matching.
3. Validate: `python3 scripts/validate_config.py config/scada.json ingest/tags.json`.
4. Run: `TAGS=ingest/tags.json ./scripts/55_run_scada.sh`.

## Run a no-hardware demo

```bash
python3 suite/console.py                # control console; start field-demo (Modbus + DNP3)
python3 suite/tase2ctl.py run field-demo # same, from the command line
./scripts/50_run_hmi.sh                  # HMI over simulated values, no gateway
```

## Run as a hardened node

```bash
./scripts/gen_certs.sh
PROFILE=hardened ./scripts/55_run_scada.sh
```

## Run as an attack target on a range

Use the default `insecure` profile and place the node on the range segment. It will
accept reads, subscriptions, and commands from any peer that can reach it. See
{doc}`../api/index` and the project SECURITY.md for the threat model.

## Capture traffic

The node produces real TASE.2 / MMS on TCP, and Modbus or DNP3 on the field side.
Capture on the relevant interface with your usual tooling. The repository includes
capture helper scripts and a sample pcap under `docs/`.

## Add a device live

Edit `ingest/tags.json` while the gateway runs. It reloads on the next poll. Adding
a whole new station also needs an entry in `config/scada.json`, which is read at
startup, so restart the stack for new stations.
