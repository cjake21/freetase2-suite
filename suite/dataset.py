#!/usr/bin/env python3
"""
dataset: turn a scenario run into a labelled dataset.

A scenario gives you two things that line up in time: a packet capture of the real
TASE.2/ICCP traffic, and the ground-truth timeline the scenario engine wrote
(suite/scenario.py, the --out file), which says exactly what happened and when and
whether it was benign or an attack. This tool joins those two by their timestamps
and produces a labelled dataset.

Why this matters. Honestly labelled industrial-protocol datasets barely exist,
because to label one correctly you have to know the ground truth, and normally
nobody does. Here we are the source of truth: the scenario engine knows precisely
which packet windows carried an injection or an unauthorized command. So we can
hand a detector a packet capture where every slice of time is marked benign or
malicious, tagged with the technique and the moment it occurred. That is exactly
what you need to train a model or to score whether an intrusion detection system
caught what it should have.

What it produces (in the output directory):
  * dataset.jsonl / dataset.csv : one row per time window, with simple flow
    features and a benign/malicious label plus any technique tags.
  * packets.jsonl (optional)    : one row per packet, with its window label.
  * splits/train.* and test.*   : a deterministic train/test split.
  * manifest.json               : what was built, the parameters, and the balance.

The capture is read with a small built-in pcap reader, so this stays standard
library only and depends on no capture or parsing packages. Point it at any pcap
of the run (the orchestrator scripts/58_run_dataset.sh records one for you).

usage:
  dataset.py label <capture.pcap> <groundtruth.jsonl> --out <dir> [options]

Python 3.7+, standard library only.
"""

import argparse
import csv
import json
import math
import os
import struct
import sys

# The TASE.2 server's default port; used to decide packet direction
# (client-to-server vs server-to-client).
DEFAULT_SERVER_PORT = 102

# Human-readable names for the MITRE ATT&CK for ICS technique tags the scenarios
# use, so the manifest and scorecard read nicely. These are the classic ATT&CK for
# ICS identifiers (the T08xx series), the set most ICS detection work references.
# Unknown tags pass through unchanged.
TECHNIQUES = {
    "T0801": "Monitor Process State",
    "T0802": "Automated Collection",
    "T0804": "Block Reporting Message",
    "T0813": "Denial of Control",
    "T0814": "Denial of Service",
    "T0815": "Denial of View",
    "T0816": "Device Restart/Shutdown",
    "T0831": "Manipulation of Control",
    "T0832": "Manipulation of View",
    "T0836": "Modify Parameter",
    "T0837": "Loss of Protection",
    "T0846": "Remote System Discovery",
    "T0855": "Unauthorized Command Message",
    "T0856": "Spoof Reporting Message",
    "T0861": "Point & Tag Identification",
    "T0878": "Alarm Suppression",
    "T0880": "Loss of Safety",
    "T0888": "Remote System Information Discovery",
}


# --------------------------------------------------------------------------- #
# pcap reader (classic libpcap format, both endianness and us/ns precision)
# --------------------------------------------------------------------------- #

def read_pcap(path):
    """Yield (timestamp_float, linktype, frame_bytes) for each packet."""
    with open(path, "rb") as f:
        magic = f.read(4)
        if len(magic) < 4:
            return
        if magic == b"\xd4\xc3\xb2\xa1":
            endian, nano = "<", False
        elif magic == b"\xa1\xb2\xc3\xd4":
            endian, nano = ">", False
        elif magic == b"\x4d\x3c\xb2\xa1":
            endian, nano = "<", True
        elif magic == b"\xa1\xb2\x3c\x4d":
            endian, nano = ">", True
        else:
            raise ValueError("not a classic pcap file (bad magic %r)" % magic)
        # rest of the 24-byte global header; linktype is the last field
        rest = f.read(20)
        if len(rest) < 20:
            return
        linktype = struct.unpack(endian + "I", rest[16:20])[0]
        denom = 1e9 if nano else 1e6
        rechdr = struct.Struct(endian + "IIII")
        while True:
            hdr = f.read(16)
            if len(hdr) < 16:
                return
            ts_sec, ts_frac, incl_len, _orig = rechdr.unpack(hdr)
            data = f.read(incl_len)
            if len(data) < incl_len:
                return
            yield ts_sec + ts_frac / denom, linktype, data


# link-layer header lengths we can strip to reach the IP packet
_LINKTYPE = {
    1: ("eth", 14),     # Ethernet (Linux lo capture reports this)
    0: ("null", 4),     # BSD loopback (DLT_NULL), 4-byte address family
    108: ("loop", 4),   # DLT_LOOP, 4-byte address family (big-endian)
    101: ("raw", 0),    # raw IP, no link header
    12: ("raw", 0),     # raw IP (alt)
    113: ("sll", 16),   # Linux cooked capture ("-i any")
}


def parse_ipv4_tcp(linktype, frame):
    """Return a dict for an IPv4/TCP packet, or None for anything else.

    Keeps just what the features need: ports, direction-relevant addresses,
    TCP flags, and the TCP payload (so we can count TASE.2/MMS PDUs)."""
    kind_off = _LINKTYPE.get(linktype)
    if kind_off is None:
        return None
    kind, off = kind_off
    if kind == "eth":
        if len(frame) < 14 or frame[12:14] != b"\x08\x00":   # IPv4 ethertype
            return None
    elif kind in ("null", "loop"):
        # 4-byte address family; IPv4 is family 2 (host order for NULL,
        # big-endian for LOOP). Accept either by checking both ends.
        if len(frame) < 4:
            return None
        fam = frame[:4]
        if 2 not in (fam[0], fam[3]):
            return None
    ip = frame[off:]
    if len(ip) < 20 or (ip[0] >> 4) != 4:
        return None
    ihl = (ip[0] & 0x0F) * 4
    if ip[9] != 6 or len(ip) < ihl + 20:        # protocol 6 = TCP
        return None
    src = ".".join(str(b) for b in ip[12:16])
    dst = ".".join(str(b) for b in ip[16:20])
    tcp = ip[ihl:]
    src_port, dst_port = struct.unpack(">HH", tcp[0:4])
    data_off = (tcp[12] >> 4) * 4
    flags = tcp[13]
    payload = tcp[data_off:] if len(tcp) >= data_off else b""
    return {"src": src, "dst": dst, "sport": src_port, "dport": dst_port,
            "flags": flags, "payload": payload}


def count_tpkt_pdus(payload):
    """Count TASE.2/MMS PDUs in a TCP payload by their TPKT framing
    (0x03 0x00 then a 2-byte total length). A cheap, protocol-aware feature
    that needs no MMS decoding. Returns (pdu_count, pdu_bytes)."""
    n = len(payload)
    i = 0
    pdus = 0
    pbytes = 0
    while i + 4 <= n:
        if payload[i] == 0x03 and payload[i + 1] == 0x00:
            length = (payload[i + 2] << 8) | payload[i + 3]
            if 4 <= length <= n - i:
                pdus += 1
                pbytes += length
                i += length
                continue
        i += 1
    return pdus, pbytes


# --------------------------------------------------------------------------- #
# Ground-truth label track
# --------------------------------------------------------------------------- #

def load_ground_truth(path):
    """Read the scenario engine's --out file: a header line then one event per
    line. Returns (header, events) where events have absolute 'wall' times."""
    header, events = {}, []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "ground_truth" in obj and "do" not in obj:
                header = obj
            elif "wall" in obj:
                events.append(obj)
    events.sort(key=lambda e: e["wall"])
    return header, events


def build_malicious_intervals(events, end_wall, pre=0.5, post=1.5):
    """Turn the ground-truth events into absolute-time intervals during which
    malicious activity was on the wire.

    Two kinds of events. A sustained value change (inject/set) holds until the
    next change on the same point, so an injection is malicious from when it was
    set until it is restored. An instantaneous action (operate/setpoint/quality)
    is a brief burst, so it gets a small window around its time. Benign events
    never produce a malicious interval; they only end a malicious one."""
    intervals = []                      # (start, stop, technique)
    open_inject = {}                    # point -> (start_wall, technique)

    def close(point, stop):
        if point in open_inject:
            start, tech = open_inject.pop(point)
            intervals.append((start, stop, tech))

    for ev in events:
        do = ev.get("do")
        wall = ev["wall"]
        label = ev.get("label", "benign")
        point = ev.get("point")
        tech = ev.get("technique")
        if do in ("inject", "set"):
            # any new value on this point ends the previous sustained state
            close(point, wall)
            if label == "malicious":
                open_inject[point] = (wall, tech)
        elif do in ("operate", "setpoint", "quality", "scan", "flood") \
                and label == "malicious":
            # a brief burst (reads, a command, a flood tick) is malicious around
            # its time; closely spaced flood ticks overlap into one window
            intervals.append((wall - pre, wall + post, tech))
        # comms_loss / restore_comms / annotate / end are benign markers here
    for point in list(open_inject):
        close(point, end_wall)
    intervals.sort()
    return intervals


def label_window(w0, w1, intervals):
    """A window is malicious if it overlaps any malicious interval. Returns
    (label, sorted list of techniques in that window). Scans every interval so a
    window that spans more than one technique collects them all."""
    techs = set()
    mal = False
    for start, stop, tech in intervals:
        if start < w1 and stop > w0:        # overlap
            mal = True
            if tech:
                techs.add(tech)
    return ("malicious" if mal else "benign"), sorted(techs)


# --------------------------------------------------------------------------- #
# Windowing + features
# --------------------------------------------------------------------------- #

def build_windows(packets, server_port, window, intervals):
    """Bin packets into fixed time windows and compute per-window features and
    labels. packets is a list of (ts, parsed) for IPv4/TCP packets only."""
    if not packets:
        return [], None, None
    t0 = packets[0][0]
    t1 = packets[-1][0]
    nwin = max(1, int(math.ceil((t1 - t0) / window)) if t1 > t0 else 1)

    rows = []
    for k in range(nwin):
        w0 = t0 + k * window
        w1 = w0 + window
        rows.append({
            "window": k, "rel_start": round(k * window, 4),
            "packets": 0, "bytes": 0,
            "pkts_c2s": 0, "pkts_s2c": 0, "bytes_c2s": 0, "bytes_s2c": 0,
            "max_len": 0, "mms_pdus": 0, "mms_bytes": 0,
            "syn": 0, "fin": 0, "rst": 0,
            "_w0": w0, "_w1": w1,
        })

    for ts, p in packets:
        k = min(nwin - 1, int((ts - t0) / window))
        r = rows[k]
        plen = len(p["payload"])
        size = plen
        r["packets"] += 1
        r["bytes"] += size
        if p["dport"] == server_port:
            r["pkts_c2s"] += 1
            r["bytes_c2s"] += size
        else:
            r["pkts_s2c"] += 1
            r["bytes_s2c"] += size
        if plen > r["max_len"]:
            r["max_len"] = plen
        pdus, pbytes = count_tpkt_pdus(p["payload"])
        r["mms_pdus"] += pdus
        r["mms_bytes"] += pbytes
        if p["flags"] & 0x02:
            r["syn"] += 1
        if p["flags"] & 0x01:
            r["fin"] += 1
        if p["flags"] & 0x04:
            r["rst"] += 1

    for r in rows:
        label, techs = label_window(r.pop("_w0"), r.pop("_w1"), intervals)
        r["label"] = label
        r["techniques"] = ",".join(techs)
    return rows, t0, t1


def split_rows(rows, frac, mode):
    """Deterministic train/test split. 'interleave' (default) sends every Nth
    window to test so both labels appear in both sets, which is the safe default
    for a short run. 'chrono' puts the first frac of time in train and the rest
    in test, which is the honest choice when you have enough data."""
    train, test = [], []
    if mode == "chrono":
        cut = int(round(len(rows) * frac))
        train, test = rows[:cut], rows[cut:]
    else:
        # interleave: keep roughly (1-frac) in test on a fixed stride
        test_every = max(2, int(round(1.0 / max(1e-6, 1.0 - frac))))
        for i, r in enumerate(rows):
            (test if (i % test_every == 0) else train).append(r)
    return train, test


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

CSV_FIELDS = ["window", "rel_start", "packets", "bytes", "pkts_c2s", "pkts_s2c",
              "bytes_c2s", "bytes_s2c", "max_len", "mms_pdus", "mms_bytes",
              "syn", "fin", "rst", "label", "techniques"]


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def write_dataset(outdir, rows, packets_rows, header, params, t0, t1,
                  intervals, frac, mode):
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(os.path.join(outdir, "splits"), exist_ok=True)

    write_jsonl(os.path.join(outdir, "dataset.jsonl"), rows)
    write_csv(os.path.join(outdir, "dataset.csv"), rows)
    if packets_rows is not None:
        write_jsonl(os.path.join(outdir, "packets.jsonl"), packets_rows)

    train, test = split_rows(rows, frac, mode)
    write_csv(os.path.join(outdir, "splits", "train.csv"), train)
    write_csv(os.path.join(outdir, "splits", "test.csv"), test)
    write_jsonl(os.path.join(outdir, "splits", "train.jsonl"), train)
    write_jsonl(os.path.join(outdir, "splits", "test.jsonl"), test)

    mal = sum(1 for r in rows if r["label"] == "malicious")
    techs = sorted({t for r in rows for t in r["techniques"].split(",") if t})
    manifest = {
        "source": params,
        "scenario": header.get("ground_truth"),
        "seed": header.get("seed"),
        "windows": len(rows),
        "window_seconds": params["window"],
        "capture_span_seconds": round((t1 - t0), 3) if t0 is not None else 0,
        "malicious_windows": mal,
        "benign_windows": len(rows) - mal,
        "malicious_intervals": len(intervals),
        "techniques": {t: TECHNIQUES.get(t, t) for t in techs},
        "split": {"mode": mode, "fraction_train": frac,
                  "train_windows": len(train), "test_windows": len(test)},
    }
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_label(args):
    if not os.path.isfile(args.pcap):
        sys.exit("[dataset] capture not found: %s" % args.pcap)
    if not os.path.isfile(args.ground_truth):
        sys.exit("[dataset] ground truth not found: %s" % args.ground_truth)

    header, events = load_ground_truth(args.ground_truth)

    packets = []
    for ts, linktype, frame in read_pcap(args.pcap):
        p = parse_ipv4_tcp(linktype, frame)
        if p is None:
            continue
        if p["sport"] != args.server_port and p["dport"] != args.server_port:
            continue                         # only the TASE.2 conversation
        packets.append((ts, p))
    packets.sort(key=lambda x: x[0])
    if not packets:
        sys.exit("[dataset] no TASE.2 packets (port %d) found in %s"
                 % (args.server_port, args.pcap))

    end_wall = packets[-1][0]
    intervals = build_malicious_intervals(events, end_wall, args.pre, args.post)
    rows, t0, t1 = build_windows(packets, args.server_port, args.window, intervals)

    packets_rows = None
    if args.packets:
        packets_rows = []
        for ts, p in packets:
            label, techs = label_window(ts, ts, intervals)
            packets_rows.append({
                "t": round(ts - t0, 4), "len": len(p["payload"]),
                "dir": "c2s" if p["dport"] == args.server_port else "s2c",
                "label": label, "techniques": ",".join(techs)})

    params = {"pcap": os.path.abspath(args.pcap),
              "ground_truth": os.path.abspath(args.ground_truth),
              "server_port": args.server_port, "window": args.window,
              "pre": args.pre, "post": args.post}
    manifest = write_dataset(args.out, rows, packets_rows, header, params, t0, t1,
                             intervals, args.split, args.split_mode)

    print("[dataset] %d packets -> %d windows (%d malicious, %d benign)"
          % (len(packets), manifest["windows"], manifest["malicious_windows"],
             manifest["benign_windows"]))
    if manifest["techniques"]:
        print("[dataset] techniques: " + ", ".join(
            "%s (%s)" % (k, v) for k, v in manifest["techniques"].items()))
    print("[dataset] wrote dataset.csv, dataset.jsonl, splits/, manifest.json to %s"
          % args.out)
    return 0


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite dataset labeller")
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("label", help="join a capture with a ground-truth timeline")
    l.add_argument("pcap")
    l.add_argument("ground_truth", help="the scenario engine's --out JSONL file")
    l.add_argument("--out", required=True, help="output directory")
    l.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT)
    l.add_argument("--window", type=float, default=1.0, help="window size (seconds)")
    l.add_argument("--pre", type=float, default=0.5,
                   help="seconds before an instantaneous attack to mark malicious")
    l.add_argument("--post", type=float, default=1.5,
                   help="seconds after an instantaneous attack to mark malicious")
    l.add_argument("--split", type=float, default=0.7, help="train fraction")
    l.add_argument("--split-mode", choices=["interleave", "chrono"],
                   default="interleave")
    l.add_argument("--packets", action="store_true",
                   help="also write a per-packet labelled file")
    l.set_defaults(func=cmd_label)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
