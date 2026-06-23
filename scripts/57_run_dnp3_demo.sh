#!/usr/bin/env bash
set -Eeuo pipefail

# DNP3 plug-and-play demo, no hardware required. It starts the bundled DNP3
# outstation simulator, then the full SCADA stack pointed at it over DNP3:
#
#   dnp3 outstation sim -> tase2_ingest (DNP3 master) -> tase2_server -> bridge -> HMI
#
# The breaker point is select-before-operate: a command from the HMI selects then
# operates a CROB on the outstation, the outstation flips the binary input, and the
# read-back appears in the HMI. Open http://127.0.0.1:8800. No sudo needed.
#
# This is the DNP3 equivalent of scripts/55_run_scada.sh (which uses stubs).

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_HOST="127.0.0.1"
TASE2_PORT="${TASE2_PORT:-10502}"
HTTP_PORT="${HTTP_PORT:-8800}"
DNP3_PORT="${DNP3_PORT:-20000}"
POLL_SEC="${POLL_SEC:-1}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.dnp3-demo.json}"
TAGS="${TAGS:-$PROJECT/ingest/tags.dnp3-demo.json}"

SRV="$PROJECT/src/tase2_server"
[[ -x "$SRV" && -x "$PROJECT/src/tase2_hmi_agent" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['domain'])" "$CONFIG")"

python3 "$PROJECT/scripts/validate_config.py" "$CONFIG" "$TAGS"
POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -f "$POINTS"; }
trap cleanup EXIT INT TERM

echo "[dnp3-demo] starting DNP3 outstation simulator on :$DNP3_PORT"
python3 "$PROJECT/ingest/dnp3_outstation_sim.py" --port "$DNP3_PORT" & PIDS+=("$!")
sleep 1

echo "[dnp3-demo] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT (no sim)"
"$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t 10 -o 30 -n -P "$POINTS" & PIDS+=("$!")
sleep 1

echo "[dnp3-demo] starting ingestion gateway (DNP3 master)"
TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" TASE2_DOMAIN="$DOMAIN" \
  python3 "$PROJECT/ingest/tase2_ingest.py" --tags "$TAGS" \
    --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" --domain "$DOMAIN" \
    --poll-sec "$POLL_SEC" & PIDS+=("$!")

echo "[dnp3-demo] starting HMI bridge on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" --config "$CONFIG" \
    --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" --http-port "$HTTP_PORT" & PIDS+=("$!")

echo "[dnp3-demo] open http://127.0.0.1:$HTTP_PORT  -  Ctrl+C to stop"
wait
