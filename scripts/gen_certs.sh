#!/usr/bin/env bash
set -Eeuo pipefail

# Generate TLS material for the hardened (Secure ICCP, mutual-TLS) profile:
# a local CA, a server certificate/key, and a client certificate/key. These are
# LAB certificates for a testbed. Do not use them in production; issue real certs
# from your own CA with proper policy and key protection.
#
#   ./scripts/gen_certs.sh            # writes to ./certs
# Then run hardened:
#   PROFILE=hardened ./scripts/55_run_scada.sh

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERTS="${CERTS:-$PROJECT/certs}"
DAYS="${DAYS:-825}"
SUBJECT_HOST="${SUBJECT_HOST:-127.0.0.1}"

mkdir -p "$CERTS"
cd "$CERTS"

echo "[certs] generating CA"
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days "$DAYS" \
  -subj "/CN=tase2-testbed-CA" >/dev/null 2>&1

gen_leaf() {
  local name="$1" cn="$2"
  openssl req -newkey rsa:2048 -nodes -keyout "$name.key" -out "$name.csr" \
    -subj "/CN=$cn" >/dev/null 2>&1
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out "$name.crt" -days "$DAYS" \
    -extfile <(printf "subjectAltName=IP:%s,DNS:localhost\n" "$SUBJECT_HOST") >/dev/null 2>&1
  rm -f "$name.csr"
  echo "[certs] generated $name.crt"
}

gen_leaf server "tase2-server"
gen_leaf client "tase2-client"
chmod 600 ./*.key
rm -f ca.srl
echo "[certs] done -> $CERTS  (ca.crt server.crt/key client.crt/key)"
