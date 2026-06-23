# How to contribute

## Ground rules

- The Python side is standard library only. Do not add runtime pip dependencies.
- Match the surrounding code style. Keep comments at the density of the file you are
  editing.
- Every change must keep `./scripts/70_selftest.sh` green.

## Workflow

1. Branch from `main`.
2. Make the change. Add or update tests:
   - a parser change gets a fuzz case,
   - an ICCP behaviour change gets an interop assertion,
   - a config field gets validator coverage.
3. Run the self-test and the full Python suite.
4. Update `CHANGELOG.md` under Unreleased.
5. Open a pull request. CI must pass.

## Documentation conventions

- Technical and concise. No marketing copy.
- Build the docs locally before submitting doc changes
  (`make -C docs html`).

## Versioning

The project follows semantic versioning. The version lives in `VERSION` and is read
by the build and the docs. Note user-facing changes in `CHANGELOG.md`.

## Security and OT safety

This is OT-adjacent software. Read the project SECURITY.md. Do not weaken the
robustness of the parsers or the control interlocks without tests that justify it,
and never commit real certificates or keys.
