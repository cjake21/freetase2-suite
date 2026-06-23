#!/usr/bin/env bash
set -Eeuo pipefail

# Run the full multi-station SCADA stack on loopback, all driven by
# config/scada.json:
#
#   PLCs -> tase2_ingest -> tase2_server (real point model) -> bridge -> HMI
#
# It (1) generates the server's point list from config/scada.json, (2) starts the
# TASE.2 server publishing exactly those points with NO internal simulation
# (-n: values come only from ingestion), (3) starts the ingestion gateway that
# polls the field devices in the tag database and writes the points over ICCP,
# and (4) starts the HMI bridge, which subscribes over ICCP and renders one
# station card per PLC. Then open http://127.0.0.1:8800.
#
# By default it uses ingest/tags.scada-demo.json (all stub drivers, no PLC
# needed: most stations jitter and read ONLINE, plc3 is 'down' and reads
# OFFLINE). Point TAGS at your own modbus tag database for real devices. No sudo.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
TASE2_PORT="${TASE2_PORT:-10502}"
HTTP_PORT="${HTTP_PORT:-8800}"
INJECT_HOLD="${INJECT_HOLD:-30}"
INTEGRITY="${INTEGRITY:-10}"
POLL_SEC="${POLL_SEC:-1}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.json}"
TAGS="${TAGS:-$PROJECT/ingest/tags.scada-demo.json}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$CONFIG")"

# 0. validate the config + tags up front (clear errors instead of runtime surprises)
python3 "$PROJECT/scripts/validate_config.py" "$CONFIG" "$TAGS"

# 1. generate the server point list from the shared config
POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"
echo "[scada] config $CONFIG -> $(wc -l < "$POINTS") points, domain $DOMAIN"

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -f "$POINTS"; }
trap cleanup EXIT INT TERM

# Security profile. PROFILE=insecure (default) is the range/attack-demo target:
# plaintext, any peer may command. PROFILE=hardened is mutual-TLS (Secure ICCP)
# plus a command allowlist limited to loopback (the ingest and bridge). Generate
# certs first with ./scripts/gen_certs.sh.
PROFILE="${PROFILE:-insecure}"
SRV_SEC=()
if [[ "$PROFILE" == "hardened" ]]; then
  CERTS="${CERTS:-$PROJECT/certs}"
  for f in ca.crt server.crt server.key client.crt client.key; do
    [[ -f "$CERTS/$f" ]] || { echo "[ERR] missing $CERTS/$f; run ./scripts/gen_certs.sh" >&2; exit 1; }
  done
  SRV_SEC=(-T -C "$CERTS/server.crt" -K "$CERTS/server.key" -A "$CERTS/ca.crt" -L "$TASE2_HOST")
  export TASE2_TLS=1 TASE2_TLS_CERT="$CERTS/client.crt" TASE2_TLS_KEY="$CERTS/client.key" TASE2_TLS_CA="$CERTS/ca.crt"
  echo "[scada] profile: HARDENED (mutual TLS + command allowlist $TASE2_HOST)"
else
  echo "[scada] profile: INSECURE (plaintext, open command path) - for ranges/attack demos"
fi

# 2. server: publish the configured points, no simulation, hold injected values
echo "[scada] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT (no sim)"
"$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t "$INTEGRITY" -o "$INJECT_HOLD" -n -P "$POINTS" "${SRV_SEC[@]}" &
PIDS+=("$!")
sleep 1

# 3. ingestion gateway: field devices -> ICCP point writes
echo "[scada] starting ingestion gateway (tags: $(basename "$TAGS"))"
TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" TASE2_DOMAIN="$DOMAIN" \
  python3 "$PROJECT/ingest/tase2_ingest.py" \
    --tags "$TAGS" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --domain "$DOMAIN" --poll-sec "$POLL_SEC" &
PIDS+=("$!")

# 4. HMI bridge: subscribe over ICCP, serve the station-grid HMI
echo "[scada] starting HMI bridge on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --http-port "$HTTP_PORT" &
PIDS+=("$!")

echo "[scada] open http://127.0.0.1:$HTTP_PORT  -  Ctrl+C to stop"
wait
