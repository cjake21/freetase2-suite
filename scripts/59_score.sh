#!/usr/bin/env bash
set -Eeuo pipefail

# Score a sensor against a scenario's ground truth.
#
# Given a dataset directory produced by scripts/58_run_dataset.sh (it holds
# capture.pcap and groundtruth.jsonl), this runs Suricata over the capture with
# the starter rules and grades the resulting alerts:
#
#   capture.pcap --(suricata + detect/tase2.rules)--> eve.json --(score.py)--> scorecard
#
#   ./scripts/59_score.sh datasets/<run>
#
# Needs Suricata on PATH. If you use a different sensor, skip this and run
# suite/score.py directly against your sensor's alerts (Suricata eve.json or the
# generic {ts,signature,technique} JSON-lines form). See detect/README.md.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${1:?usage: 59_score.sh <dataset-dir>}"
PCAP="$DIR/capture.pcap"
GROUND="$DIR/groundtruth.jsonl"
RULES="${RULES:-$PROJECT/detect/tase2.rules}"

[[ -f "$PCAP" ]]   || { echo "[ERR] no capture.pcap in $DIR" >&2; exit 1; }
[[ -f "$GROUND" ]] || { echo "[ERR] no groundtruth.jsonl in $DIR" >&2; exit 1; }

if ! command -v suricata >/dev/null 2>&1; then
  echo "[score] Suricata is not installed. Run your own sensor over $PCAP and then:"
  echo "        python3 suite/score.py grade $GROUND <alerts> --out $DIR/scorecard.json"
  exit 1
fi

SURI_OUT="$DIR/suricata"
mkdir -p "$SURI_OUT"
echo "[score] running Suricata over $PCAP with $(basename "$RULES")"
suricata -r "$PCAP" -S "$RULES" -l "$SURI_OUT" >/dev/null 2>&1 || true

EVE="$SURI_OUT/eve.json"
[[ -f "$EVE" ]] || { echo "[ERR] Suricata produced no eve.json in $SURI_OUT" >&2; exit 1; }

echo "[score] grading alerts against ground truth"
python3 "$PROJECT/suite/score.py" grade "$GROUND" "$EVE" \
  --format suricata --out "$DIR/scorecard.json" --report "$DIR/scorecard.md"
echo "[score] scorecard -> $DIR/scorecard.json and $DIR/scorecard.md"
