#!/usr/bin/env python3
"""Tests for the dataset labeller (stdlib unittest).

Builds a synthetic pcap and a matching ground-truth timeline entirely in memory,
so the pcap reader, the IPv4/TCP parser, the TPKT counter, the label track, and
the windowing are all exercised with no capture tooling and no privileges.
"""
import argparse
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))

import dataset as ds  # noqa: E402

SERVER_PORT = 102


# ---- tiny synthetic pcap builder ------------------------------------------ #

def tpkt(body_len):
    """A TPKT-framed payload of the given total length (>= 4)."""
    return b"\x03\x00" + struct.pack(">H", body_len) + b"\x00" * (body_len - 4)


def eth_ipv4_tcp(sport, dport, payload, flags=0x18):
    ip_total = 20 + 20 + len(payload)
    ip = bytes([0x45, 0, (ip_total >> 8) & 0xFF, ip_total & 0xFF,
                0, 0, 0, 0, 64, 6, 0, 0,
                127, 0, 0, 1, 127, 0, 0, 1])
    tcp = struct.pack(">HHIIBBHHH", sport, dport, 0, 0, 0x50, flags, 0, 0, 0)
    eth = b"\x00" * 12 + b"\x08\x00"
    return eth + ip + tcp + payload


def make_pcap(packets):
    """packets: list of (ts_float, frame_bytes). Returns classic pcap bytes
    (little-endian, microsecond, Ethernet linktype)."""
    out = struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    for ts, frame in packets:
        sec = int(ts)
        usec = int(round((ts - sec) * 1e6))
        out += struct.pack("<IIII", sec, usec, len(frame), len(frame)) + frame
    return out


class TestPcapAndParse(unittest.TestCase):
    def test_read_and_parse_roundtrip(self):
        frames = [(1000.0, eth_ipv4_tcp(40000, SERVER_PORT, tpkt(8))),
                  (1000.5, eth_ipv4_tcp(SERVER_PORT, 40000, tpkt(12)))]
        with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as f:
            f.write(make_pcap(frames))
            path = f.name
        try:
            pkts = list(ds.read_pcap(path))
            self.assertEqual(len(pkts), 2)
            ts, linktype, frame = pkts[0]
            self.assertAlmostEqual(ts, 1000.0, places=4)
            self.assertEqual(linktype, 1)
            p = ds.parse_ipv4_tcp(linktype, frame)
            self.assertEqual((p["sport"], p["dport"]), (40000, SERVER_PORT))
        finally:
            os.remove(path)

    def test_tpkt_counting(self):
        payload = tpkt(8) + tpkt(12)            # two PDUs back to back
        pdus, pbytes = ds.count_tpkt_pdus(payload)
        self.assertEqual(pdus, 2)
        self.assertEqual(pbytes, 20)

    def test_non_ipv4_ignored(self):
        self.assertIsNone(ds.parse_ipv4_tcp(1, b"\x00" * 12 + b"\x86\xdd" + b"x" * 40))


class TestLabelTrack(unittest.TestCase):
    def events(self):
        return [
            {"wall": 1003.0, "do": "inject", "label": "malicious",
             "point": "plc1_mw", "technique": "T0856"},
            {"wall": 1006.0, "do": "set", "label": "benign", "point": "plc1_mw"},
            {"wall": 1008.0, "do": "operate", "label": "malicious",
             "point": "plc1_brk", "technique": "T0855"},
        ]

    def test_intervals(self):
        iv = ds.build_malicious_intervals(self.events(), end_wall=1010.0,
                                          pre=0.5, post=1.5)
        self.assertIn((1003.0, 1006.0, "T0856"), iv)
        self.assertIn((1007.5, 1009.5, "T0855"), iv)

    def test_window_labels(self):
        iv = ds.build_malicious_intervals(self.events(), 1010.0)
        self.assertEqual(ds.label_window(1004.0, 1005.0, iv), ("malicious", ["T0856"]))
        self.assertEqual(ds.label_window(1000.0, 1001.0, iv), ("benign", []))
        self.assertEqual(ds.label_window(1008.0, 1009.0, iv)[0], "malicious")


class TestSplit(unittest.TestCase):
    def rows(self, n=10):
        return [{"window": i, "label": "benign", "techniques": ""} for i in range(n)]

    def test_chrono_split_partitions(self):
        rows = self.rows(10)
        tr, te = ds.split_rows(rows, 0.7, "chrono")
        self.assertEqual(len(tr), 7)
        self.assertEqual(len(te), 3)
        self.assertEqual(len(tr) + len(te), len(rows))

    def test_interleave_is_disjoint_and_complete(self):
        rows = self.rows(10)
        tr, te = ds.split_rows(rows, 0.7, "interleave")
        idx_tr = {r["window"] for r in tr}
        idx_te = {r["window"] for r in te}
        self.assertEqual(idx_tr & idx_te, set())
        self.assertEqual(idx_tr | idx_te, set(range(10)))


class TestEndToEndLabel(unittest.TestCase):
    """Full cmd_label over a synthetic capture + ground truth."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # 0.5s spacing from 1000.0 to 1010.0, alternating direction
        frames = []
        t = 1000.0
        toggle = True
        while t <= 1010.0:
            if toggle:
                frames.append((t, eth_ipv4_tcp(40000, SERVER_PORT, tpkt(10))))
            else:
                frames.append((t, eth_ipv4_tcp(SERVER_PORT, 40000, tpkt(14))))
            toggle = not toggle
            t += 0.5
        self.pcap = os.path.join(self.dir, "cap.pcap")
        with open(self.pcap, "wb") as f:
            f.write(make_pcap(frames))
        self.gt = os.path.join(self.dir, "gt.jsonl")
        with open(self.gt, "w") as f:
            f.write(json.dumps({"ground_truth": "t", "seed": 1}) + "\n")
            for ev in [
                {"t": 3, "wall": 1003.0, "do": "inject", "label": "malicious",
                 "point": "plc1_mw", "technique": "T0856"},
                {"t": 6, "wall": 1006.0, "do": "set", "label": "benign",
                 "point": "plc1_mw"},
                {"t": 8, "wall": 1008.0, "do": "operate", "label": "malicious",
                 "point": "plc1_brk", "technique": "T0855"},
            ]:
                f.write(json.dumps(ev) + "\n")
        self.out = os.path.join(self.dir, "out")

    def _args(self, **over):
        a = argparse.Namespace(pcap=self.pcap, ground_truth=self.gt, out=self.out,
                               server_port=SERVER_PORT, window=1.0, pre=0.5, post=1.5,
                               split=0.7, split_mode="interleave", packets=True)
        for k, v in over.items():
            setattr(a, k, v)
        return a

    def test_label_outputs_and_balance(self):
        rc = ds.cmd_label(self._args())
        self.assertEqual(rc, 0)
        for name in ("dataset.csv", "dataset.jsonl", "manifest.json",
                     "packets.jsonl", "splits/train.csv", "splits/test.csv"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

        with open(os.path.join(self.out, "manifest.json")) as f:
            man = json.load(f)
        # injection [1003,1006) plus operate window around 1008 should mark
        # several windows malicious, and the rest benign
        self.assertGreaterEqual(man["malicious_windows"], 3)
        self.assertGreater(man["benign_windows"], 0)
        self.assertIn("T0856", man["techniques"])
        self.assertIn("T0855", man["techniques"])

        with open(os.path.join(self.out, "dataset.jsonl")) as f:
            rows = [json.loads(line) for line in f]
        early = next(r for r in rows if r["rel_start"] == 0.0)
        self.assertEqual(early["label"], "benign")
        mid = next(r for r in rows if r["rel_start"] == 4.0)   # ~1004s, injected
        self.assertEqual(mid["label"], "malicious")
        self.assertIn("T0856", mid["techniques"])
        self.assertGreater(mid["mms_pdus"], 0)                 # protocol-aware feature

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
