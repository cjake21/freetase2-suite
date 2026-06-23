# Request and response examples

## Read state

```bash
curl -s http://127.0.0.1:8800/api/state | python3 -m json.tool
```

```json
{
  "server": { "host": "127.0.0.1", "port": 10502, "domain": "TestDomain" },
  "online": { "A": true, "B": true },
  "report": { "last_report_time": "20260622T120000Z", "count": 14, "cond": 2 },
  "stations": [
    { "id": "plc1", "name": "FEEDER BAY 1", "online": true,
      "points": [
        { "name": "plc1_mw", "type": "real", "value": 13.5, "quality": "VALID",
          "ts": 1700000000, "age": 2, "fresh": true, "control": null }
      ] }
  ]
}
```

## Direct command

```bash
curl -s -XPOST http://127.0.0.1:8800/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action":"command","item":"plc4_stat","value":1}'
```

```json
{ "ok": true }
```

## Select-before-operate

```bash
curl -s -XPOST http://127.0.0.1:8800/api/control -d '{"action":"select","item":"plc1_brk"}'
# -> {"ok": true}, the point is armed
curl -s -XPOST http://127.0.0.1:8800/api/control -d '{"action":"command","item":"plc1_brk","value":1}'
# -> {"ok": true}, the operate is accepted
```

If you operate without selecting first:

```json
{ "error": "point 'plc1_brk' not selected (SBO)" }
```

## Stream events

```bash
curl -sN http://127.0.0.1:8800/api/events
```

```text
data: {"server": {...}, "stations": [...]}

: keepalive
```
