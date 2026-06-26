# Expected output

## HMI state

`GET /api/state` returns the full view the HMI renders. Abbreviated:

```json
{
  "server": { "host": "127.0.0.1", "port": 102, "domain": "TestDomain" },
  "online": { "A": true, "B": true },
  "report": { "last_report_time": "20260622T...", "count": 14, "cond": 2 },
  "stations": [
    {
      "id": "plc1", "name": "FEEDER BAY 1", "online": true,
      "points": [
        { "name": "plc1_mw", "label": "TIE-LINE FLOW", "unit": "MW", "type": "real",
          "value": 13.5, "quality": "VALID", "ts": 1700000000, "age": 2,
          "fresh": true, "control": null, "mode": null, "armed": 0 },
        { "name": "plc1_brk", "label": "BREAKER", "type": "state",
          "value": 0, "quality": "VALID", "fresh": true,
          "control": "discrete", "mode": "sbo", "armed": 0,
          "states": { "0": "OPEN", "1": "CLOSED" } }
      ]
    }
  ]
}
```

Field meanings appear in {doc}`../api/rest`.

## Command result

A successful control returns:

```json
{ "ok": true }
```

A few seconds later the point's `value` reflects the command, because it has
travelled to the device and been read back.

## Gateway log

```text
[ingest] ICCP association online; polling 12 tag(s) every 1.0s; Ctrl+C to stop
[ingest] command plc1_brk_ctl = 1 -> plc1_brk
```

## Server log

```text
[tase2] publishing 12 indication point(s) from points file; value source: external writes only (no simulation)
[tase2] command allowlist: OPEN (any peer may write/operate)
[tase2] control plc1_brk_ctl commanded (SBO)
```
