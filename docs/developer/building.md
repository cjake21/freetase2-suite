# Building from source

## Full build

```bash
./scripts/10_build.sh
```

This clones libIEC61850 at the pinned ref, downloads mbedtls, applies the required
patches, builds the library, and compiles the C tools. It is idempotent: re-running
force-checks-out the pin and re-applies the patches.

## Rebuilding the tools only

After the library exists in `deps/`:

```bash
make -C src LIB61850_HOME="$PWD/deps/libiec61850"
```

## Pinning

Override the pinned versions if you must:

```bash
LIB61850_REF=v1.6.1 MBEDTLS_VER=3.6.0 ./scripts/10_build.sh
```

## TLS

The server links mbedtls for Secure ICCP. The binary is statically self-contained;
the build needs `libmbedtls-dev` present at link time. If linking fails on
`TLSConfiguration_*`, the mbedtls source is missing under
`deps/libiec61850/third_party/mbedtls/`.

## Documentation

```bash
python3 -m venv docs/.venv
. docs/.venv/bin/activate
pip install -r docs/requirements.txt
make -C docs html       # output in docs/_build/html/index.html
```
