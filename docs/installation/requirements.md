# Requirements

## Host

- Linux (the build and run scripts assume a POSIX shell).
- A C toolchain: `gcc`, `make`, `cmake`.
- `swig` and `python3-dev` (for the optional Python bindings used by the
  interoperability test).
- `libmbedtls-dev` (the server links mbedtls for TLS / Secure ICCP).
- `python3` 3.7 or newer.
- `git` and `curl` (the build clones libIEC61850 and downloads mbedtls on the
  first run).

On Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake swig python3-dev libmbedtls-dev git curl
```

## Field devices (optional)

To ingest real data you need one or more devices that speak:

- **Modbus TCP** (function codes 3 and 4 for reads, 5, 6, or 16 for writes), or
- **DNP3** over TCP (binary input group 1, analog input group 30, control group 12
  CROB).

For trying the tool you need no hardware. The stub driver and the bundled DNP3
outstation simulator provide data and accept control in memory.

## Pinned dependency versions

For reproducible builds the build script pins:

| Dependency | Version | Override |
|------------|---------|----------|
| libIEC61850 | `v1.6.1` | `LIB61850_REF` |
| mbedtls | `3.6.0` | `MBEDTLS_VER` |
