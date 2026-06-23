# Testing

## Run everything

```bash
./scripts/70_selftest.sh
```

This validates the shipped configs, runs the Python test suite, and runs a headless
smoke that brings up the SCADA stack and checks the HMI serves state. It is what CI
runs.

## Python test suite

```bash
python3 -m unittest discover -s tests
```

| File | Covers |
|------|--------|
| `tests/test_ingest.py` | DNP3 CRC and framing, Modbus decodes, config resolution, a live DNP3 master to outstation round trip |
| `tests/test_interop.py` | interop gate: drives the server with an independent MMS stack (pyiec61850) for Block 1 reads, Block 2 transfer-set config, and Block 5 select-before-operate |
| `tests/test_fuzz.py` | fuzzes every untrusted-byte parser and floods the live control API with malformed requests |

The interop and live tests skip automatically if the C tools or pyiec61850 are not
built.

## Interop gate

`test_interop.py` is the credibility test. It uses pyiec61850, a different code path
from the project's own client, to confirm a third-party stack can associate, decode
the object model, configure a transfer set, and operate a control with
select-before-operate enforcement. Treat a failure here as a real interop bug.

## Fuzzing

`test_fuzz.py` throws thousands of random, truncated, and mutated-valid inputs at
the DNP3, Modbus, and outstation parsers, and posts hundreds of malformed control
requests to a live bridge, asserting clean failures and that the node stays up. Add
a case here whenever you add a parser.

## CI

`.github/workflows/ci.yml` builds (with dependency caching) and runs the self-test
on every push and pull request.
