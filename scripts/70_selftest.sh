#!/usr/bin/env bash
set -Eeuo pipefail

# Self-test: validate the shipped configs, run the Python test suite (unit +
# interop), and run a short headless smoke of the SCADA stack. This is what CI
# runs and what you can run locally to check a change end to end. Build first with
# ./scripts/10_build.sh.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

echo "== validate configs =="
python3 scripts/validate_config.py config/scada.json ingest/tags.scada-demo.json
python3 scripts/validate_config.py config/scada.dnp3-demo.json ingest/tags.dnp3-demo.json

echo "== python test suite (unit + interop) =="
python3 -m unittest discover -s tests

echo "== headless smoke: SCADA stack comes up and serves state =="
if [[ -x src/tase2_server && -x src/tase2_hmi_agent ]]; then
  HTTP_PORT="${HTTP_PORT:-8899}"
  TASE2_PORT="${TASE2_PORT:-10599}"
  HTTP_PORT="$HTTP_PORT" TASE2_PORT="$TASE2_PORT" bash scripts/55_run_scada.sh >/tmp/selftest_scada.log 2>&1 &
  SMOKE_PID=$!
  ok=0
  for _ in $(seq 1 20); do
    sleep 1
    if curl -fs "http://127.0.0.1:$HTTP_PORT/api/state" >/tmp/selftest_state.json 2>/dev/null; then ok=1; break; fi
  done
  kill "$SMOKE_PID" 2>/dev/null || true
  for p in $(pgrep -x tase2_server); do kill "$p" 2>/dev/null || true; done
  for pid in $(pgrep -x python3); do
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qE 'bridge.py|tase2_ingest' && kill "$pid" 2>/dev/null || true
  done
  if [[ "$ok" != 1 ]]; then echo "[ERROR] HMI did not serve state"; cat /tmp/selftest_scada.log; exit 1; fi
  python3 -c "import json;s=json.load(open('/tmp/selftest_state.json'));assert s['stations'];print('  HMI served %d stations'%len(s['stations']))"
else
  echo "  (skipped smoke: C tools not built)"
fi

echo "== self-test OK =="
