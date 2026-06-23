# Docker install

A container image builds the pinned libIEC61850 and the tools, then runs the SCADA
stack as a ready target.

## Build the image

```bash
docker build -t tase2-plc-gateway .
```

## Run

```bash
# the universal multi-protocol demo (Modbus + DNP3), HMI on :8800
docker run --rm -p 8800:8800 freetase2-suite \
    bash -lc "MODBUS_SIM=1 DNP3_SIM=1 scripts/55_run_scada.sh"

# or the control console on :8080 to choose and run a deployment
docker run --rm -p 8080:8080 -p 8800:8800 freetase2-suite \
    bash -lc "CONSOLE_HOST=0.0.0.0 python3 suite/console.py"
```

Open `http://127.0.0.1:8800` (HMI) or `http://127.0.0.1:8080` (console).

```{note}
Inside the container the HMI binds to `0.0.0.0` (set by `HTTP_HOST`) so the
published port is reachable. The TASE.2 server and the field side stay on loopback
inside the container.
```

## Use your own configuration

Mount your point model and tag database and point the stack at them:

```bash
docker run --rm -p 8800:8800 \
    -v "$PWD/config:/opt/tase2-plc-gateway/config:ro" \
    -v "$PWD/ingest:/opt/tase2-plc-gateway/ingest:ro" \
    -e SCADA_CONFIG=/opt/tase2-plc-gateway/config/scada.json \
    -e TAGS=/opt/tase2-plc-gateway/ingest/tags.json \
    tase2-plc-gateway
```

To reach real field devices the container needs network access to them. Review the
security model in {doc}`../guides/configuration` first.
