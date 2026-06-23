#!/usr/bin/env bash
set -Eeuo pipefail

# Run the southbound ingestion gateway on loopback: start the TASE.2 server on a
# high port, then run tase2_ingest in front of it. The gateway polls the field
# devices named in the tag database and writes their values into the server's
# points over ICCP, so the publisher carries real field data instead of the
# server's synthetic simulateValues() loop. No sudo needed.
#
# By default it uses ingest/tags.example.json, whose first tag is a "stub" that
# returns a fixed value so you can confirm the whole path works before any PLC is
# wired up. Point TAGS at your own tags.json for real devices, e.g.
#   TAGS=ingest/tags.4plc.example.json ./scripts/60_run_ingest.sh
# which polls four PLCs declared once each under "devices". The gateway is a Modbus
# MASTER: it connects OUT to each PLC and reads it, so point it AT the PLCs (set
# each device's host/port/unit). Editing the tag file while it runs reloads it
# live, so adding more PLCs needs no restart.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASE2_PORT="${TASE2_PORT:-10502}"
TASE2_HOST="${TASE2_HOST:-127.0.0.1}"
DOMAIN="${TASE2_DOMAIN:-TestDomain}"
INJECT_HOLD="${INJECT_HOLD:-30}"
POLL_SEC="${POLL_SEC:-2}"
TAGS="${TAGS:-$PROJECT/ingest/tags.example.json}"

SRV="$PROJECT/src/tase2_server"
AGENT="$PROJECT/src/tase2_hmi_agent"
for b in "$SRV" "$AGENT"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh (then make tase2_hmi_agent)" >&2; exit 1; }
done

# Start the server only if nothing is already serving the chosen port. Note the
# injection-hold (-o): it keeps each ingested value pinned between polls.
SRV_PID=""
if ! ss -ltn "( sport = :$TASE2_PORT )" 2>/dev/null | grep -q ":$TASE2_PORT"; then
  echo "[ingest] starting TASE.2 server on $TASE2_HOST:$TASE2_PORT"
  "$SRV" -i "$TASE2_HOST" -p "$TASE2_PORT" -d "$DOMAIN" -t 30 -o "$INJECT_HOLD" &
  SRV_PID=$!
  sleep 1
else
  echo "[ingest] reusing TASE.2 server already on :$TASE2_PORT"
fi

cleanup() { [[ -n "$SRV_PID" ]] && kill "$SRV_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

TASE2_HOST="$TASE2_HOST" TASE2_PORT="$TASE2_PORT" TASE2_DOMAIN="$DOMAIN" \
  python3 "$PROJECT/ingest/tase2_ingest.py" \
    --tags "$TAGS" \
    --server-host "$TASE2_HOST" --server-port "$TASE2_PORT" --domain "$DOMAIN" \
    --poll-sec "$POLL_SEC"
