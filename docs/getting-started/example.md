# Example project

A minimal one-station node with one analog point and one controllable breaker, fed
by stubs so it runs with no hardware.

## `config/scada.example.json`

```json
{
  "domain": "TestDomain",
  "stations": [
    {
      "id": "bay1",
      "name": "FEEDER BAY 1",
      "points": [
        { "name": "bay1_mw",  "type": "real",  "label": "FEEDER FLOW", "unit": "MW" },
        { "name": "bay1_brk", "type": "state", "label": "BREAKER",
          "states": { "0": "OPEN", "1": "CLOSED" },
          "control": { "kind": "discrete", "mode": "sbo" } }
      ]
    }
  ]
}
```

## `ingest/tags.example.json`

```json
{
  "tags": [
    { "point": "bay1_mw",  "type": "float", "driver": "stub", "value": 13.8, "jitter": 1.5 },
    { "point": "bay1_brk", "type": "int",   "driver": "stub", "value": 0, "control": {} }
  ]
}
```

## Run it

```bash
SCADA_CONFIG=config/scada.example.json TAGS=ingest/tags.example.json \
  ./scripts/55_run_scada.sh
```

Open the HMI, SELECT then operate the breaker, and watch the read-back change. To
feed `bay1_mw` from a real Modbus PLC, replace its tag:

```json
{ "point": "bay1_mw", "type": "float", "driver": "modbus",
  "host": "10.30.0.11", "port": 502, "unit": 1,
  "kind": "holding", "register": 0, "decode": "float32" }
```
