#!/usr/bin/env python3
"""
score: grade a detector against a scenario's ground truth.

This closes the loop. A scenario plays a known set of attacks and writes a
ground-truth timeline (suite/scenario.py). You run your intrusion detection system
over the same traffic and it produces alerts. This tool lines the two up and tells
you, honestly, how well the detector did: which attacks it caught, which it missed,
how fast it caught them, and how many times it cried wolf on benign traffic.

That is the purple-team question every asset owner and every IDS vendor actually
cares about, and normally it is answered by hand and by vibe. Here it is answered
by the numbers, because we know exactly what the attacks were.

What it reports:
  * recall: of the known attacks, how many produced at least one alert,
  * a per-technique breakdown (mapped to MITRE ATT&CK for ICS), so you can see, for
    example, that unauthorized commands are caught but spoofed readings slip by,
  * time-to-detect for each caught attack,
  * false positives: alerts that fired when nothing malicious was happening, and a
    false-positive rate per minute.

It reads alerts from Suricata's eve.json or from a simple generic JSON-lines feed,
so it works with whatever sensor you point at the capture. It reuses the same
ground-truth interval logic as the dataset tool, so the two agree on what counts as
an attack. Standard library only.

usage:
  score.py grade <groundtruth.jsonl> <alerts.json> [--format auto|suricata|generic]
                 [--tolerance 2.0] [--out scorecard.json] [--report report.md]
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dataset  # noqa: E402  (reuse the ground-truth interval logic + technique names)


# --------------------------------------------------------------------------- #
# Reading alerts
# --------------------------------------------------------------------------- #

def parse_ts(value):
    """Turn an alert timestamp into a Unix float. Accepts a number (already Unix),
    or an ISO 8601 string like Suricata's 2026-06-24T19:38:41.504123+0000 (with or
    without fractional seconds, with a numeric offset or a trailing Z)."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        return float(s)                       # a Unix timestamp as a string
    except ValueError:
        pass
    s = s.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError("cannot parse alert timestamp %r" % value)


def _first(value):
    """Suricata metadata fields are often lists; take the first item."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def load_alerts(path, fmt="auto"):
    """Read an alert file into a list of {t, signature, sid, technique}.

    Auto-detects Suricata eve.json (lines carrying an event_type) versus a generic
    JSON-lines feed of {ts, signature, sid?, technique?}."""
    alerts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            use = fmt
            if use == "auto":
                use = "suricata" if "event_type" in obj else "generic"

            if use == "suricata":
                if obj.get("event_type") != "alert":
                    continue
                a = obj.get("alert", {})
                meta = a.get("metadata", {}) or {}
                alerts.append({
                    "t": parse_ts(obj.get("timestamp")),
                    "signature": a.get("signature", "?"),
                    "sid": a.get("signature_id"),
                    "technique": _first(meta.get("mitre_technique_id")),
                })
            else:
                ts = obj.get("ts", obj.get("timestamp", obj.get("t")))
                if ts is None:
                    continue
                alerts.append({
                    "t": parse_ts(ts),
                    "signature": obj.get("signature", obj.get("msg", "?")),
                    "sid": obj.get("sid", obj.get("signature_id")),
                    "technique": obj.get("technique"),
                })
    alerts.sort(key=lambda a: a["t"])
    return alerts


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def grade(intervals, alerts, tol=2.0):
    """Match alerts to ground-truth attack instances (the malicious intervals) and
    build the scorecard. An attack is detected if any alert falls within its window
    widened by tol. An alert is a true positive if it lands in some attack window,
    a false positive otherwise."""
    attacks = []
    for start, stop, tech in intervals:
        lo, hi = start - tol, stop + tol
        matched = [a for a in alerts if lo <= a["t"] <= hi]
        first_t = min((a["t"] for a in matched), default=None)
        alerted_techs = sorted({a["technique"] for a in matched if a.get("technique")})
        attacks.append({
            "technique": tech or "unspecified",
            "start": round(start, 3), "stop": round(stop, 3),
            "duration": round(stop - start, 3),
            "detected": bool(matched),
            "latency": round(first_t - start, 3) if first_t is not None else None,
            "alerts": len(matched),
            "signatures": sorted({a["signature"] for a in matched}),
            "technique_match": bool(tech and tech in alerted_techs),
        })

    # classify each alert as a true or false positive by time overlap
    tp = 0
    for a in alerts:
        a_hit = any((s - tol) <= a["t"] <= (e + tol) for s, e, _ in intervals)
        tp += 1 if a_hit else 0
    fp = len(alerts) - tp

    # per-technique rollup
    by_tech = {}
    for atk in attacks:
        d = by_tech.setdefault(atk["technique"], {
            "name": dataset.TECHNIQUES.get(atk["technique"], atk["technique"]),
            "attacks": 0, "detected": 0, "technique_match": 0})
        d["attacks"] += 1
        d["detected"] += 1 if atk["detected"] else 0
        d["technique_match"] += 1 if atk["technique_match"] else 0
    for d in by_tech.values():
        d["recall"] = round(d["detected"] / d["attacks"], 3) if d["attacks"] else 0.0

    # the scored span (for a false-positive rate) covers the attacks and the alerts
    times = [s for s, _, _ in intervals] + [e for _, e, _ in intervals] + \
            [a["t"] for a in alerts]
    span = (max(times) - min(times)) if times else 0.0
    detected = sum(1 for a in attacks if a["detected"])
    latencies = [a["latency"] for a in attacks if a["latency"] is not None]

    return {
        "attacks_total": len(attacks),
        "attacks_detected": detected,
        "attacks_missed": len(attacks) - detected,
        "recall": round(detected / len(attacks), 3) if attacks else 0.0,
        "alerts_total": len(alerts),
        "true_positive_alerts": tp,
        "false_positive_alerts": fp,
        "false_positives_per_min": round(fp / (span / 60.0), 3) if span > 0 else 0.0,
        "mean_time_to_detect": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "by_technique": by_tech,
        "attack_detail": attacks,
        "tolerance_seconds": tol,
        "scored_span_seconds": round(span, 3),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def print_summary(sc):
    print("[score] attacks: %d total, %d detected, %d missed  (recall %.0f%%)"
          % (sc["attacks_total"], sc["attacks_detected"], sc["attacks_missed"],
             sc["recall"] * 100))
    print("[score] alerts: %d total, %d true positive, %d false positive (%.2f/min)"
          % (sc["alerts_total"], sc["true_positive_alerts"],
             sc["false_positive_alerts"], sc["false_positives_per_min"]))
    if sc["mean_time_to_detect"] is not None:
        print("[score] mean time to detect: %.2fs" % sc["mean_time_to_detect"])
    print("[score] by technique:")
    for tid, d in sorted(sc["by_technique"].items()):
        print("  %-12s %-28s %d/%d detected (%.0f%%)"
              % (tid, d["name"], d["detected"], d["attacks"], d["recall"] * 100))


def markdown_report(sc, scenario_name):
    lines = []
    lines.append("# Detection scorecard: %s" % (scenario_name or "scenario"))
    lines.append("")
    lines.append("- Attacks: **%d total**, %d detected, %d missed (recall **%.0f%%**)"
                 % (sc["attacks_total"], sc["attacks_detected"], sc["attacks_missed"],
                    sc["recall"] * 100))
    lines.append("- Alerts: %d total, %d true positive, %d false positive (%.2f per minute)"
                 % (sc["alerts_total"], sc["true_positive_alerts"],
                    sc["false_positive_alerts"], sc["false_positives_per_min"]))
    if sc["mean_time_to_detect"] is not None:
        lines.append("- Mean time to detect: %.2fs" % sc["mean_time_to_detect"])
    lines.append("")
    lines.append("## Coverage by technique")
    lines.append("")
    lines.append("| Technique | Name | Detected | Recall |")
    lines.append("|-----------|------|----------|--------|")
    for tid, d in sorted(sc["by_technique"].items()):
        lines.append("| %s | %s | %d/%d | %.0f%% |"
                     % (tid, d["name"], d["detected"], d["attacks"], d["recall"] * 100))
    lines.append("")
    lines.append("## Each attack")
    lines.append("")
    lines.append("| Technique | Window (s) | Detected | Latency (s) | Signatures |")
    lines.append("|-----------|-----------|----------|-------------|------------|")
    for a in sc["attack_detail"]:
        sigs = ", ".join(a["signatures"]) if a["signatures"] else ""
        lines.append("| %s | %.1f to %.1f | %s | %s | %s |"
                     % (a["technique"], a["start"], a["stop"],
                        "yes" if a["detected"] else "**no**",
                        ("%.2f" % a["latency"]) if a["latency"] is not None else "-",
                        sigs))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_grade(args):
    if not os.path.isfile(args.ground_truth):
        sys.exit("[score] ground truth not found: %s" % args.ground_truth)
    if not os.path.isfile(args.alerts):
        sys.exit("[score] alerts not found: %s" % args.alerts)

    header, events = dataset.load_ground_truth(args.ground_truth)
    end_wall = max((e["wall"] for e in events), default=0) + args.post + 1
    intervals = dataset.build_malicious_intervals(events, end_wall, args.pre, args.post)
    if not intervals:
        print("[score] note: the ground truth has no malicious intervals to grade")

    alerts = load_alerts(args.alerts, args.format)
    sc = grade(intervals, alerts, args.tolerance)
    sc["scenario"] = header.get("ground_truth")
    sc["seed"] = header.get("seed")

    print_summary(sc)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(sc, f, indent=2)
        print("[score] scorecard -> %s" % args.out)
    if args.report:
        with open(args.report, "w") as f:
            f.write(markdown_report(sc, header.get("ground_truth")))
        print("[score] report -> %s" % args.report)
    return 0


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite detection scorer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grade", help="grade alerts against a ground-truth timeline")
    g.add_argument("ground_truth", help="the scenario engine's --out JSONL file")
    g.add_argument("alerts", help="Suricata eve.json or a generic JSON-lines alert feed")
    g.add_argument("--format", choices=["auto", "suricata", "generic"], default="auto")
    g.add_argument("--tolerance", type=float, default=2.0,
                   help="seconds of slack around an attack window when matching alerts")
    g.add_argument("--pre", type=float, default=0.5,
                   help="seconds before an instantaneous attack counted as malicious")
    g.add_argument("--post", type=float, default=1.5,
                   help="seconds after an instantaneous attack counted as malicious")
    g.add_argument("--out", help="write the scorecard JSON here")
    g.add_argument("--report", help="write a markdown report here")
    g.set_defaults(func=cmd_grade)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
