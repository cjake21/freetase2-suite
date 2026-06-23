# Exporting reports

The node is a live publisher rather than a report generator, so "exporting" means
capturing its outputs for analysis or records.

## State snapshot

Save the current state as JSON:

```bash
curl -s http://127.0.0.1:8800/api/state > state.json
```

## Time series from the event stream

The event stream pushes the full state on every change. Capture it to a file:

```bash
curl -sN http://127.0.0.1:8800/api/events > events.sse
```

Each `data:` line is a JSON snapshot. Post-process with your own script to extract
per-point time series.

## Packet captures

For protocol-level records, capture the TASE.2 / MMS traffic on the server port and
the Modbus or DNP3 traffic on the field side with your usual tooling. The repository
includes capture helper scripts and a sample TASE.2 pcap under `docs/`.

## Logs

The server, gateway, and bridge log to stdout. Redirect them to files when running
unattended, for example under a service manager, to keep a record of associations,
commands, and quality changes.
