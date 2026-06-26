#!/usr/bin/env bash
set -Eeuo pipefail

# Run a federation of two TASE.2 control centres with a live tie between them:
#
#   CC-A server (live) --(relay subscribes, mirrors)--> CC-B server --> CC-B HMI
#
# Control centre A runs its own server with live (simulated) data. Control centre B
# runs its own server with no local source. The relay (suite/relay.py) subscribes to
# A and writes the agreed tie points into B over real ICCP, so B's HMI shows A's
# tie-line data without measuring it locally. Then open http://127.0.0.1:8800 to see
# CC-B's intertie view. What A shares is its bilateral agreement; set BLT to a table
# on A to enforce it. Needs sudo to bind port 102.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FED="${FEDERATION:-$PROJECT/config/federation.json}"
HTTP_PORT="${HTTP_PORT:-8800}"
INTEGRITY="${INTEGRITY:-4}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
[[ -f "$FED" ]] || { echo "[ERR] federation config not found: $FED" >&2; exit 1; }

# validate the federation up front
python3 "$PROJECT/suite/relay.py" validate --federation "$FED"

cfield() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['centers'][sys.argv[2]][sys.argv[3]])" "$FED" "$1" "$2"; }
domain_of() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$1"; }

A_HOST="$(cfield A host)"; A_PORT="$(cfield A port)"; A_CONFIG="$PROJECT/$(cfield A config)"; A_DOMAIN="$(domain_of "$A_CONFIG")"
B_HOST="$(cfield B host)"; B_PORT="$(cfield B port)"; B_CONFIG="$PROJECT/$(cfield B config)"; B_DOMAIN="$(domain_of "$B_CONFIG")"

PTSA="$(mktemp)"; PTSB="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$A_CONFIG" > "$PTSA"
python3 "$PROJECT/scripts/gen_server_points.py" "$B_CONFIG" > "$PTSB"

PIDS=()
cleanup() { sudo pkill -x tase2_server 2>/dev/null || true; for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -f "$PTSA" "$PTSB"; }
trap cleanup EXIT INT TERM

# Optional bilateral table on the source center (what A is willing to share).
A_SEC=()
BLT="${BLT:-}"
if [[ -n "$BLT" ]]; then
  [[ -f "$BLT" ]] || { echo "[ERR] bilateral table not found: $BLT" >&2; exit 1; }
  A_SEC=(-B "$BLT")
  echo "[fed] CC-A bilateral table ENFORCED: $BLT"
fi

# 1. CC-A: its own server with live data (simulation on)
echo "[fed] starting CC-A server on $A_HOST:$A_PORT (domain $A_DOMAIN, live)"
sudo "$SRV" -i "$A_HOST" -p "$A_PORT" -d "$A_DOMAIN" -t "$INTEGRITY" -P "$PTSA" "${A_SEC[@]}" & PIDS+=("$!")

# 2. CC-B: its own server, no local source (values arrive over the tie)
echo "[fed] starting CC-B server on $B_HOST:$B_PORT (domain $B_DOMAIN, no sim)"
"$SRV" -i "$B_HOST" -p "$B_PORT" -d "$B_DOMAIN" -t "$INTEGRITY" -o 30 -n -P "$PTSB" & PIDS+=("$!")
sleep 1

# 3. the relay: carry the agreed points from CC-A into CC-B
echo "[fed] starting inter-control-centre relay"
python3 "$PROJECT/suite/relay.py" run --federation "$FED" & PIDS+=("$!")
sleep 1

# 4. CC-B HMI: B's view of the intertie
echo "[fed] starting CC-B HMI on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$B_CONFIG" TASE2_HOST="$B_HOST" TASE2_PORT="$B_PORT" \
  python3 "$PROJECT/hmi/bridge.py" --config "$B_CONFIG" \
    --server-host "$B_HOST" --server-port "$B_PORT" --http-port "$HTTP_PORT" & PIDS+=("$!")

echo "[fed] federation up. Open http://127.0.0.1:$HTTP_PORT to watch CC-A's tie data arrive at CC-B. Ctrl+C to stop."
wait
