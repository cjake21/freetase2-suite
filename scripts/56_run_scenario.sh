#!/usr/bin/env bash
set -Eeuo pipefail

# Run a scenario: a deterministic, scripted timeline drives every point, with no
# ingestion gateway and the server's own simulation off. The stack is:
#
#   scenario engine -> tase2_server (real point model, no sim) -> bridge -> HMI
#
# It (1) generates the server's point list from config/scada.json, (2) starts the
# TASE.2 server publishing those points with simulation off (-n: values come only
# from the scenario), (3) starts the HMI bridge so you can watch it, and (4) plays
# the scenario, which seeds every point, keeps them fresh, and injects the timeline
# of operations, attacks, and faults. Then open http://127.0.0.1:8800.
#
# Set SCENARIO to the scenario file (default scenarios/fdi_tieline.json) and
# SCENARIO_OUT to capture the ground-truth timeline (default a temp file). No sudo.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
TASE2_PORT="${TASE2_PORT:-10502}"
HTTP_PORT="${HTTP_PORT:-8800}"
INTEGRITY="${INTEGRITY:-10}"
INJECT_HOLD="${INJECT_HOLD:-30}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.json}"
SCENARIO="${SCENARIO:-$PROJECT/scenarios/fdi_tieline.json}"
SCENARIO_OUT="${SCENARIO_OUT:-$(mktemp -t freetase2-groundtruth.XXXX.jsonl)}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
[[ -f "$SCENARIO" ]] || { echo "[ERR] scenario not found: $SCENARIO" >&2; exit 1; }
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$CONFIG")"

# 0. validate the point model and the scenario up front
python3 "$PROJECT/scripts/validate_config.py" "$CONFIG"
SCADA_CONFIG="$CONFIG" python3 "$PROJECT/suite/scenario.py" validate "$SCENARIO" --config "$CONFIG"

# 1. generate the server point list from the shared config
POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"
echo "[scenario] config $CONFIG -> $(wc -l < "$POINTS") points, domain $DOMAIN"

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -f "$POINTS"; }
trap cleanup EXIT INT TERM

# Security profile, same as the SCADA stack: insecure (default) or hardened
# (mutual TLS + loopback command allowlist). Run ./scripts/gen_certs.sh first for
# hardened.
PROFILE="${PROFILE:-insecure}"
SRV_SEC=()
if [[ "$PROFILE" == "hardened" ]]; then
  CERTS="${CERTS:-$PROJECT/certs}"
  for f in ca.crt server.crt server.key client.crt client.key; do
    [[ -f "$CERTS/$f" ]] || { echo "[ERR] missing $CERTS/$f; run ./scripts/gen_certs.sh" >&2; exit 1; }
  done
  SRV_SEC=(-T -C "$CERTS/server.crt" -K "$CERTS/server.key" -A "$CERTS/ca.crt" -L "$TASE2_HOST")
  export TASE2_TLS=1 TASE2_TLS_CERT="$CERTS/client.crt" TASE2_TLS_KEY="$CERTS/client.key" TASE2_TLS_CA="$CERTS/ca.crt"
  echo "[scenario] profile: HARDENED (mutual TLS + command allowlist $TASE2_HOST)"
else
  echo "[scenario] profile: INSECURE (plaintext, open command path) - for ranges/attack demos"
fi

# Bilateral table (per-peer data scoping). Set BLT to a table file to enforce it.
BLT="${BLT:-}"
if [[ -n "$BLT" ]]; then
  [[ -f "$BLT" ]] || { echo "[ERR] bilateral table not found: $BLT" >&2; exit 1; }
  SRV_SEC+=(-B "$BLT")
  echo "[scenario] bilateral table ENFORCED: $BLT"
fi

# 2. server: publish the configured points, no simulation, hold injected values
echo "[scenario] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT (no sim)"
"$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t "$INTEGRITY" -o "$INJECT_HOLD" -n -P "$POINTS" "${SRV_SEC[@]}" &
PIDS+=("$!")
sleep 1

# 3. HMI bridge: subscribe over ICCP, serve the station-grid HMI
echo "[scenario] starting HMI bridge on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --http-port "$HTTP_PORT" &
PIDS+=("$!")
sleep 1

# 4. scenario engine: the value source (seed, heartbeat, and play the timeline)
echo "[scenario] playing $(basename "$SCENARIO"); ground truth -> $SCENARIO_OUT"
echo "[scenario] open http://127.0.0.1:$HTTP_PORT  -  Ctrl+C to stop"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/suite/scenario.py" run "$SCENARIO" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --out "$SCENARIO_OUT"

echo "[scenario] scenario finished; ground truth saved to $SCENARIO_OUT"
echo "[scenario] the server and HMI stay up so you can review; Ctrl+C to stop"
wait
