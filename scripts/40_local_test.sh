#!/usr/bin/env bash
set -Eeuo pipefail

# Quick smoke test on loopback: start the server on port 102 (binding the
# privileged port needs sudo), run the probe and the full client driver, then
# stop the server. Proves Block 1/2/5.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-102}"
DOMAIN="${TASE2_DOMAIN:-TestDomain}"

SRV="$PROJECT/src/tase2_server"
CLI="$PROJECT/src/tase2_client"
PRB="$PROJECT/src/tase2_probe"
for b in "$SRV" "$CLI" "$PRB"; do
  [[ -x "$b" ]] || { echo "[ERR] build first: ./scripts/10_build.sh" >&2; exit 1; }
done

sudo "$SRV" -i 127.0.0.1 -p "$PORT" -d "$DOMAIN" -t 5 &
SRV_PID=$!
trap 'sudo pkill -x tase2_server 2>/dev/null || true' EXIT
sleep 1

echo "######## PROBE (read-only object dump) ########"
"$PRB" 127.0.0.1 "$PORT" "$DOMAIN" || true
echo
echo "######## CLIENT DRIVER (Block 1/2/5) ########"
"$CLI" 127.0.0.1 "$PORT" "$DOMAIN" 12 || true
