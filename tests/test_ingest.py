#!/usr/bin/env python3
"""Regression tests for the ingestion gateway internals (stdlib unittest).

Run: python3 -m unittest discover -s tests   (or: python3 tests/test_ingest.py)

Covers the deterministic protocol/config logic and a live DNP3 master <-> bundled
outstation round trip. The TASE.2/ICCP path is exercised by the shell smoke tests
and scripts/55|57, which need the built C tools.
"""
import os
import struct
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ING = os.path.join(HERE, "..", "ingest")
sys.path.insert(0, ING)

import dnp3                      # noqa: E402
import dnp3_outstation_sim       # noqa: E402
import tase2_ingest as ti        # noqa: E402


class FakeSock:
    """A minimal recv()-only socket backed by a byte buffer."""
    def __init__(self, data):
        self.buf = data

    def recv(self, n):
        chunk = self.buf[:n]
        self.buf = self.buf[n:]
        return chunk


class TestDnp3Framing(unittest.TestCase):
    def test_crc_vector(self):
        # CRC-16/DNP check value for "123456789"
        self.assertEqual(dnp3.crc16_dnp(b"123456789"), 0xEA82)

    def test_frame_roundtrip(self):
        payload = bytes([0xC0, 0xC1, 0x01, 30, 5, 0x00, 0, 7])
        frame = dnp3.build_frame(10, 1, payload)
        self.assertEqual(frame[0:2], b"\x05\x64")
        self.assertEqual(dnp3.recv_frame(FakeSock(frame)), payload)

    def test_frame_multiblock(self):
        # > 16 bytes forces multiple CRC-protected data blocks
        payload = bytes(range(40))
        frame = dnp3.build_frame(10, 1, payload)
        self.assertEqual(dnp3.recv_frame(FakeSock(frame)), payload)

    def test_header_crc_detected(self):
        frame = bytearray(dnp3.build_frame(10, 1, bytes([0xC0, 0xC1])))
        frame[3] ^= 0xFF  # corrupt control byte
        with self.assertRaises(ValueError):
            dnp3.recv_frame(FakeSock(bytes(frame)))


class TestModbusDecode(unittest.TestCase):
    def mk(self, decode, word_order="big"):
        return ti.ModbusTcpReader({"host": "x", "register": 0,
                                   "decode": decode, "word_order": word_order})

    def test_uint16_int16(self):
        self.assertEqual(self.mk("uint16")._decode([0x1234]), 0x1234)
        self.assertEqual(self.mk("int16")._decode([0xFFFE]), -2)

    def test_float32_word_order(self):
        hi, lo = struct.unpack(">HH", struct.pack(">f", 123.5))
        self.assertAlmostEqual(self.mk("float32")._decode([hi, lo]), 123.5, places=3)
        self.assertAlmostEqual(self.mk("float32", "little")._decode([lo, hi]), 123.5, places=3)

    def test_int32(self):
        hi, lo = struct.unpack(">HH", struct.pack(">i", -2))
        self.assertEqual(self.mk("int32")._decode([hi, lo]), -2)

    def test_count_derives_from_decode(self):
        self.assertEqual(self.mk("float32").count, 2)
        self.assertEqual(self.mk("uint16").count, 1)

    def test_bad_decode_rejected(self):
        with self.assertRaises(ValueError):
            self.mk("bogus")


class TestConfig(unittest.TestCase):
    def test_resolve_device_merge(self):
        devices = {"plc1": {"driver": "modbus", "host": "10.0.0.1", "unit": 3}}
        tag = {"point": "p", "device": "plc1", "register": 5}
        merged = ti.resolve_tag(tag, devices)
        self.assertEqual(merged["host"], "10.0.0.1")
        self.assertEqual(merged["unit"], 3)
        self.assertEqual(merged["register"], 5)

    def test_unknown_device_rejected(self):
        with self.assertRaises(ValueError):
            ti.build_runtime_tags([{"point": "p", "device": "nope"}], {})

    def test_control_attached(self):
        tags = ti.build_runtime_tags(
            [{"point": "p", "type": "int", "driver": "stub", "value": 0, "control": {}}], {})
        self.assertIsNotNone(tags[0]["control"])
        self.assertEqual(tags[0]["control"]["object"], "p_ctl")
        for t in tags:
            t["reader"].close()


class TestDnp3LiveRoundTrip(unittest.TestCase):
    """Start the bundled outstation simulator and drive it with the master."""
    @classmethod
    def setUpClass(cls):
        cls.srv = dnp3_outstation_sim.Server(("127.0.0.1", 0), dnp3_outstation_sim.Handler)
        cls.srv.master_addr = 1
        cls.srv.outstation_addr = 10
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_read_and_crob(self):
        m = dnp3.Dnp3Master("127.0.0.1", self.port, 1, 10)
        try:
            self.assertIsInstance(m.read_point(30, 5, 0), float)   # analog float
            self.assertEqual(m.read_point(1, 2, 0), 0)             # binary, default 0
            m.operate_crob(0, on=True, sbo=True)                   # select + operate
            self.assertEqual(m.read_point(1, 2, 0), 1)
            m.operate_crob(0, on=False, sbo=False)                 # direct operate
            self.assertEqual(m.read_point(1, 2, 0), 0)
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
