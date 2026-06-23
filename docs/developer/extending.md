# Extending the tool

## Add a field protocol

Write a driver class and register it in `DRIVERS`. See {doc}`../modules/custom` and
the worked example in {doc}`../modules/example`. DNP3 is a good reference for a
connection-based, control-capable driver.

## Add a published object type

The server builds its model in `tase2_server.c` (`buildModel`, `populateCache`).
Indication points are RealQ and StateQ structures with quality and a time tag;
control objects are Block 5 devices. To add a type, extend the spec builders and the
write handler, then update the agent's report parsing if the new type appears in a
data set.

## Add an HMI feature

The HMI is data-driven from the bridge state object. Add a field in
`hmi/bridge.py` (`_station_view`) and render it in `hmi/static/hmi.js`. No build step;
reload the page.

## Add a control mechanism

Control flows HMI to `<point>_ctl` to the gateway to the device. To add a mechanism
(for example a different SBO timeout policy or a setpoint ramp), extend the server's
control handling and the gateway's `_service_control`.

## Keep it testable

- New parsers get a fuzz case ({doc}`testing`).
- New ICCP behaviour that a third-party client would exercise gets an interop
  assertion.
- New config fields get validation in `scripts/validate_config.py`.
