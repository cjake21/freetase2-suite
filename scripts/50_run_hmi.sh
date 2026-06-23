#!/usr/bin/env bash
set -Eeuo pipefail

# Run the SCADA HMI on loopback over SIMULATED values (no ingestion gateway),
# then open http://127.0.0.1:8800. No sudo needed. This is the quick way to see
# the station-grid HMI without wiring any PLCs: the server publishes the point
# model from config/scada.json and drives it with its internal simulation, so
# every station reads ONLINE with moving values.
#
# For the full real path (field devices -> ingest -> server -> HMI) use
# scripts/55_run_scada.sh instead, which adds the ingestion gateway and runs the
# server with simulation off (-n) so values come only from the field.
#
# The bridge drives real ICCP clients against the server, so everything in the
# HMI is genuine TASE.2/MMS traffic. To capture it, point a capture at the
# loopback TCP port below, or use the namespace scripts (20/30/32).

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_PORT="${TASE2_PORT:-10502}"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
HTTP_PORT="${HTTP_PORT:-8800}"
INJECT_HOLD="${INJECT_HOLD:-30}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.json}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$CONFIG")"

# Generate the server's point list from the shared config.
POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"

# Start the server only if nothing is already serving the chosen port. It
# publishes the configured points and (no -n) drives them with its simulation.
SRV_PID=""
if ! ss -ltn "( sport = :$TASE2_PORT )" 2>/dev/null | grep -q ":$TASE2_PORT"; then
  echo "[hmi] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT ($(wc -l < "$POINTS") points, simulated)"
  "$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t 10 -o "$INJECT_HOLD" -P "$POINTS" &
  SRV_PID=$!
  sleep 1
else
  echo "[hmi] reusing TASE.2 server already on :$TASE2_PORT"
fi

cleanup() { [[ -n "$SRV_PID" ]] && kill "$SRV_PID" 2>/dev/null || true; rm -f "$POINTS"; }
trap cleanup EXIT INT TERM

SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --http-port "$HTTP_PORT"
