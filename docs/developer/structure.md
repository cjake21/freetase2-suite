# Project structure

```text
src/                      C tools (built against libIEC61850)
  tase2_server.c          TASE.2/ICCP server
  tase2_hmi_agent.c       persistent MMS client (driven by bridge and gateway)
  tase2_client.c          one-shot client
  tase2_probe.c           protocol probe
  Makefile

ingest/                   southbound ingestion (Python, stdlib only)
  tase2_ingest.py         the gateway: drivers, poll loop, control loop
  dnp3.py                 DNP3 master library
  dnp3_outstation_sim.py  bench DNP3 outstation simulator
  modbus_outstation_sim.py bench Modbus slave simulator
  tags*.json              tag databases (field mapping; tags.demo.json is multi-protocol)

suite/                    control plane (the unified tool)
  tase2ctl.py             CLI: list/validate/run named deployments
  console.py              web control console (launcher) + static/console.html
  profiles.json           named deployments (mode, protocol, profile, config, sims)

hmi/
  bridge.py               HMI bridge + HTTP/SSE/control API
  static/                 web HMI (index.html, hmi.js, hmi.css)

config/
  scada.json              point model + station layout (source of truth)

scripts/
  10_build.sh             build libIEC61850 (pinned) + tools
  55_run_scada.sh         full ingestion stack (any protocol; starts sims via MODBUS_SIM/DNP3_SIM)
  50_run_hmi.sh           HMI over simulated values
  gen_server_points.py    scada.json -> server point list
  gen_certs.sh            lab TLS certificates
  validate_config.py      config validator
  70_selftest.sh          validate + tests + smoke

tests/                    unit, interop, and fuzz tests
docs/                     this documentation
Dockerfile, .github/      container image and CI
VERSION, CHANGELOG.md     version and history
```

## Single source of truth

`config/scada.json` defines the published model. `scripts/gen_server_points.py`
flattens it into the simple list the C server reads with `-P`. The bridge and HMI
read `config/scada.json` directly. The tag database maps each point name to the
field. Keep names consistent and run the validator.
