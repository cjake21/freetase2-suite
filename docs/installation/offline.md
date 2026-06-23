# Offline install

The only steps that need the network are the first build (cloning libIEC61850 and
downloading mbedtls) and, optionally, building the documentation. Everything else
runs offline.

## Pre-stage the dependencies

On a machine with network access, run the build once so `deps/` is populated:

```bash
./scripts/10_build.sh
```

Then move the whole repository, including `deps/`, to the offline host. On the
offline host the build is incremental and needs no network:

```bash
./scripts/10_build.sh     # re-checks the pin and recompiles; no fetch needed if deps/ exists
```

If you cannot carry `deps/`, mirror the two sources yourself and point the build at
them: clone `libiec61850` to `deps/libiec61850` at tag `v1.6.1`, and extract
mbedtls `3.6.0` into `deps/libiec61850/third_party/mbedtls/mbedtls-3.6.0`.

## Offline documentation

Build the HTML docs once where you have network, then copy `docs/_build/html`:

```bash
python3 -m venv docs/.venv
. docs/.venv/bin/activate
pip install -r docs/requirements.txt
make -C docs html
```

## Python

The gateway, bridge, and simulator use only the Python standard library, so no pip
packages are required to run the tool. Only the documentation build needs Sphinx.
