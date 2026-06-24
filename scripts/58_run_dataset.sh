#!/usr/bin/env bash
set -Eeuo pipefail

# Build a labelled dataset from a scenario run.
#
# It captures the TASE.2/ICCP traffic while a scenario plays, then joins the
# capture with the scenario's ground-truth timeline so every window of time is
# marked benign or malicious with a technique tag:
#
#   tcpdump (lo) ----.
#                    +--> dataset.py label --> datasets/<run>/  (csv, jsonl, splits)
#   scenario engine -'      (capture + ground truth)
#
# Steps: (1) start the TASE.2 server with simulation off, (2) start the HMI
# bridge so Block 2 report traffic is on the wire too, (3) start a capture on
# loopback for the server port, (4) play the scenario writing its ground truth,
# (5) stop the capture, (6) run the labeller.
#
# Capturing loopback needs privilege, like the other capture scripts, so tcpdump
# runs under sudo by default. Override with CAP_CMD if your tcpdump has the right
# capabilities (CAP_CMD=tcpdump) or to point at a different tool.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
TASE2_PORT="${TASE2_PORT:-10502}"
HTTP_PORT="${HTTP_PORT:-8800}"
INTEGRITY="${INTEGRITY:-10}"
INJECT_HOLD="${INJECT_HOLD:-30}"
CONFIG="${SCADA_CONFIG:-$PROJECT/config/scada.json}"
SCENARIO="${SCENARIO:-$PROJECT/scenarios/fdi_tieline.json}"
WINDOW="${WINDOW:-1.0}"
CAP_CMD="${CAP_CMD:-sudo tcpdump}"

SCN_NAME="$(basename "$SCENARIO" .json)"
STAMP="$(date +%Y%m%d-%H%M%S)"
DATASET_DIR="${DATASET_DIR:-$PROJECT/datasets/${SCN_NAME}-${STAMP}}"
PCAP="$DATASET_DIR/capture.pcap"
GROUND="$DATASET_DIR/groundtruth.jsonl"
mkdir -p "$DATASET_DIR"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done
[[ -f "$SCENARIO" ]] || { echo "[ERR] scenario not found: $SCENARIO" >&2; exit 1; }
DOMAIN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('domain','TestDomain'))" "$CONFIG")"

# validate the point model and the scenario up front
python3 "$PROJECT/scripts/validate_config.py" "$CONFIG"
SCADA_CONFIG="$CONFIG" python3 "$PROJECT/suite/scenario.py" validate "$SCENARIO" --config "$CONFIG"

POINTS="$(mktemp)"
python3 "$PROJECT/scripts/gen_server_points.py" "$CONFIG" > "$POINTS"

PIDS=()
CAP_PID=""
cleanup() {
  [[ -n "$CAP_PID" ]] && { $CAP_CMD -h >/dev/null 2>&1 || true; kill "$CAP_PID" 2>/dev/null || true; }
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  rm -f "$POINTS"
}
trap cleanup EXIT INT TERM

# 1. server: configured points, no simulation
echo "[dataset] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT (no sim)"
"$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t "$INTEGRITY" -o "$INJECT_HOLD" -n -P "$POINTS" &
PIDS+=("$!"); sleep 1

# 2. HMI bridge: subscribes, so Block 2 reports are also on the wire
echo "[dataset] starting HMI bridge on http://127.0.0.1:$HTTP_PORT"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/hmi/bridge.py" --config "$CONFIG" \
    --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" --http-port "$HTTP_PORT" &
PIDS+=("$!"); sleep 1

# 3. capture on loopback for the server port
echo "[dataset] capturing on lo (tcp port $TASE2_PORT) -> $PCAP"
$CAP_CMD -i lo -nn -s 0 -w "$PCAP" "tcp port $TASE2_PORT" &
CAP_PID="$!"; sleep 1

# 4. play the scenario, writing its ground truth
echo "[dataset] playing $SCN_NAME; ground truth -> $GROUND"
SCADA_CONFIG="$CONFIG" TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" \
  python3 "$PROJECT/suite/scenario.py" run "$SCENARIO" \
    --config "$CONFIG" --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" \
    --out "$GROUND"

# 5. stop the capture (flush its buffers)
sleep 1
$CAP_CMD -h >/dev/null 2>&1 || true
kill "$CAP_PID" 2>/dev/null || true
CAP_PID=""
sleep 1

# 6. label it
echo "[dataset] labelling capture against ground truth"
python3 "$PROJECT/suite/dataset.py" label "$PCAP" "$GROUND" \
  --out "$DATASET_DIR" --server-port "$TASE2_PORT" --window "$WINDOW" --packets

echo "[dataset] done -> $DATASET_DIR"
