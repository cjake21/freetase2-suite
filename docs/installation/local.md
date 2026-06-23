# Local install

Clone the repository and run the two setup scripts.

```bash
./scripts/00_install_deps.sh    # install host packages (optional helper)
./scripts/10_build.sh           # clone + build libIEC61850 (pinned) and the tools
```

`10_build.sh` does the following, once:

1. Clones libIEC61850 into `deps/` and checks out the pinned ref (`v1.6.1`).
2. Downloads mbedtls (`3.6.0`) for TLS support.
3. Applies two required libIEC61850 patches (VMD-scope named variables and a
   read-path fix).
4. Builds the library and compiles the four C tools into `src/`.

When it finishes you will have:

```text
src/tase2_server      src/tase2_client
src/tase2_hmi_agent   src/tase2_probe
```

## Verify

```bash
./scripts/70_selftest.sh
```

This validates the shipped configs, runs the unit and interoperability tests, and
runs a short headless smoke of the SCADA stack. Expected final line:

```text
== self-test OK ==
```

## Run

```bash
./scripts/55_run_scada.sh       # Modbus / stub demo, HMI on http://127.0.0.1:8800
./scripts/57_run_dnp3_demo.sh   # DNP3 demo with the bundled outstation simulator
```

See {doc}`../getting-started/first-run`.
