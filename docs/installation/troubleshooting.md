# Troubleshooting

## The build fails linking `TLSConfiguration_*`

libIEC61850 only compiles its TLS layer when the mbedtls source is present. The
build downloads it automatically; if you are offline, place mbedtls `3.6.0` under
`deps/libiec61850/third_party/mbedtls/mbedtls-3.6.0` and rebuild. Confirm
`libmbedtls-dev` is installed.

## `git checkout` fails on re-run of the build

The build force-checks-out the pinned ref and re-applies its patches. If you have
made manual edits inside `deps/libiec61850`, they will be discarded. Do not edit
the vendored library; change the tools in `src/` instead.

## The HMI shows no stations or all OFFLINE

- Confirm the gateway is running and connected. Its log prints `ICCP association
  online; polling N tag(s)`.
- A station reads OFFLINE when no valid, recent data is arriving for its points.
  In the stub demo, station `plc3` is OFFLINE on purpose.
- Check that point names in `ingest/tags.json` match names in `config/scada.json`.
  Run the validator: `python3 scripts/validate_config.py config/scada.json
  ingest/tags.json`.

## A control has no effect

- For a select-before-operate point you must SELECT before OPERATE. The HMI shows
  a SELECT button first.
- In the `hardened` profile, only allowlisted peers may command. The local bridge
  and gateway are on the loopback allowlist; an external peer is not.
- Control takes a few poll cycles to read back (command read, write down, read up,
  report). Lower `POLL_SEC` for a snappier loop.

## A DNP3 device does not read

- Confirm the outstation address, group, variation, and index in the tag.
- Confirm network reachability to the outstation TCP port (default 20000).
- Try the bundled simulator first: `python3 ingest/dnp3_outstation_sim.py`.

## Port already in use

Set `TASE2_PORT` and `HTTP_PORT` before launching, for example
`HTTP_PORT=8801 TASE2_PORT=10503 ./scripts/55_run_scada.sh`.
