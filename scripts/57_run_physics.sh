#!/usr/bin/env bash
set -Eeuo pipefail

# Run the power-flow co-simulation: a real grid model drives the points, and
# operator or attacker breaker commands cause flows to redistribute and, if a line
# is pushed past its limit, to cascade. The stack is:
#
#   physics co-sim -> tase2_server (real point model, no sim) -> bridge -> HMI
#
# It (1) generates the server's point list from config/scada.json, (2) starts the
# TASE.2 server with simulation off (-n: values come only from the co-simulation),
# (3) starts the HMI bridge, and (4) runs the co-simulation, which solves the grid
# in config/grid.json each tick, publishes line flows and bus quantities to the
# points, and reads breaker controls so a breaker open feeds back into the model.
# Then open http://127.0.0.1:8800 and try opening plc1_brk (the main tie) to watch
# the cascade. Set GRID to use a different grid model. No sudo.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
TASE2_PORT="${TASE2_PORT:-10502}"
HTTP_PORT="${HTTP_PORT:-8800}"
INTEGRITY="${INTEGRITY:-10}"
INJECT_HOLD="${INJECT_HOLD:-30}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.json}"
GRID="${GRID:-$PROJECT/config/grid.json}"
PERIOD="${PERIOD:-2.0}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
[[ -f "$GRID" ]] || { echo "[ERR] grid model not found: $GRID" >&2; exit 1; }
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$CONFIG")"

# 0. validate the point model and the grid model up front
python3 "$PROJECT/scripts/validate_config.py" "$CONFIG"
SCADA_CONFIG="$CONFIG" python3 "$PROJECT/suite/physics.py" validate --grid "$GRID" --config "$CONFIG"

# 1. generate the server point list from the shared config
POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"
echo "[physics] config $CONFIG -> $(wc -l < "$POINTS") points, domain $DOMAIN"

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -f "$POINTS"; }
trap cleanup EXIT INT TERM

# Security profile, same as the other stacks.
PROFILE="${PROFILE:-insecure}"
SRV_SEC=()
if [[ "$PROFILE" == "hardened" ]]; then
  CERTS="${CERTS:-$PROJECT/certs}"
  for f in ca.crt server.crt server.key client.crt client.key; do
    [[ -f "$CERTS/$f" ]] || { echo "[ERR] missing $CERTS/$f; run ./scripts/gen_certs.sh" >&2; exit 1; }
  done
  SRV_SEC=(-T -C "$CERTS/server.crt" -K "$CERTS/server.key" -A "$CERTS/ca.crt" -L "$TASE2_HOST")
  export TASE2_TLS=1 TASE2_TLS_CERT="$CERTS/client.crt" TASE2_TLS_KEY="$CERTS/client.key" TASE2_TLS_CA="$CERTS/ca.crt"
  echo "[physics] profile: HARDENED (mutual TLS + command allowlist $TASE2_HOST)"
else
  echo "[physics] profile: INSECURE (plaintext, open command path) - for ranges/attack demos"
fi

# Bilateral table (per-peer data scoping). Set BLT to a table file to enforce it.
BLT="${BLT:-}"
if [[ -n "$BLT" ]]; then
  [[ -f "$BLT" ]] || { echo "[ERR] bilateral table not found: $BLT" >&2; exit 1; }
  SRV_SEC+=(-B "$BLT")
  echo "[physics] bilateral table ENFORCED: $BLT"
fi

# 2. server: publish the configured points, no simulation, hold injected values
echo "[physics] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT (no sim)"
"$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t "$INTEGRITY" -o "$INJECT_HOLD" -n -P "$POINTS" "${SRV_SEC[@]}" &
PIDS+=("$!")
sleep 1

# 3. HMI bridge: subscribe over ICCP, serve the station-grid HMI
echo "[physics] starting HMI bridge on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --http-port "$HTTP_PORT" &
PIDS+=("$!")
sleep 1

# 4. co-simulation: the value source (solve the grid, publish, react to breakers)
echo "[physics] starting power-flow co-simulation (grid: $(basename "$GRID"))"
echo "[physics] open http://127.0.0.1:$HTTP_PORT and open plc1_brk to watch a cascade  -  Ctrl+C to stop"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/suite/physics.py" run --grid "$GRID" --config "$CONFIG" \
    --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" --domain "$DOMAIN" --period "$PERIOD"
