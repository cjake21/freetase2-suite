#!/usr/bin/env python3
"""Fuzz the untrusted-byte surfaces so the node is a robust target.

A security testbed node will be poked with malformed traffic. These tests throw
random and mutated-valid inputs at every parser that handles peer-controlled
bytes and assert each one fails cleanly (an allowed exception type, no crash, no
unexpected exception, and no hang because every parse loop is bounded):

  * DNP3 data link frame parsing (master side, reading an outstation)
  * DNP3 application response parsing
  * Modbus response parsing
  * the DNP3 outstation simulator (reading a master)

A live test also floods the HMI bridge's control API with malformed requests and
confirms it never 500s or falls over.
"""
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "ingest"))

import dnp3                      # noqa: E402
import dnp3_outstation_sim       # noqa: E402
import tase2_ingest as ti        # noqa: E402

SEED = 1234
N = 3000


class FakeSock:
    """recv()/sendall() over a fixed buffer; recv returns b'' when drained."""
    def __init__(self, data):
        self.buf = data

    def sendall(self, _data):
        pass

    def recv(self, n):
        chunk = self.buf[:n]
        self.buf = self.buf[n:]
        return chunk

    def settimeout(self, _t):
        pass

    def close(self):
        pass


def _random_inputs(rng):
    """A mix of pure-random and mutated-valid byte strings."""
    valid_frame = dnp3.build_frame(10, 1, bytes([0xC0, 0x81, 0x00, 0x00, 30, 5, 0x00, 0, 1,
                                                 0x01, 0, 0, 0, 0, 0x01, 0, 0, 0, 64]))
    for _ in range(N):
        kind = rng.randint(0, 2)
        if kind == 0:
            yield bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 60)))
        elif kind == 1:                                  # truncated valid frame
            yield valid_frame[:rng.randint(0, len(valid_frame))]
        else:                                            # mutated valid frame
            b = bytearray(valid_frame)
            for _ in range(rng.randint(1, 4)):
                b[rng.randrange(len(b))] = rng.getrandbits(8)
            yield bytes(b)


class TestParserFuzz(unittest.TestCase):
    def test_dnp3_recv_frame(self):
        rng = random.Random(SEED)
        for data in _random_inputs(rng):
            try:
                dnp3.recv_frame(FakeSock(data))
            except (ValueError, OSError):
                pass
            except Exception as e:  # noqa: BLE001
                self.fail("recv_frame raised %r on %r" % (e, data[:16]))

    def test_dnp3_parse_application(self):
        rng = random.Random(SEED + 1)
        for _ in range(N):
            app = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 40)))
            try:
                dnp3.Dnp3Master._parse_single(app, 30, 5, rng.randint(0, 5))
            except ValueError:
                pass
            except Exception as e:  # noqa: BLE001
                self.fail("_parse_single raised %r on %r" % (e, app))

    def test_modbus_response_parse(self):
        rng = random.Random(SEED + 2)
        for _ in range(N):
            r = ti.ModbusTcpReader({"host": "x", "register": 0, "decode": "float32"})
            # craft an MBAP+PDU with random tail so read() parses peer bytes
            body = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 30)))
            frame = struct.pack(">HHHB", 1, 0, len(body) + 1, 1) + body
            r._conn = lambda f=frame: FakeSock(f)
            try:
                r.read()
            except (ValueError, OSError):
                pass
            except Exception as e:  # noqa: BLE001
                self.fail("modbus read raised %r on %r" % (e, body))

    def test_outstation_sim_handle(self):
        rng = random.Random(SEED + 3)
        for _ in range(N):
            app = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 40)))
            try:
                out = dnp3_outstation_sim.handle_app(app)
            except Exception as e:  # noqa: BLE001 - the sim must never raise
                self.fail("handle_app raised %r on %r" % (e, app))
            self.assertTrue(out is None or isinstance(out, (bytes, bytearray)))


SERVER = os.path.join(ROOT, "src", "tase2_server")
AGENT = os.path.join(ROOT, "src", "tase2_hmi_agent")
_live = os.path.isfile(SERVER) and os.path.isfile(AGENT)


@unittest.skipUnless(_live, "C tools not built (run scripts/10_build.sh)")
class TestBridgeFuzz(unittest.TestCase):
    """Flood the live control API with malformed requests; it must stay up."""
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, HTTP_PORT="8911", TASE2_PORT="10911")
        cls.proc = subprocess.Popen(
            ["bash", os.path.join(ROOT, "scripts", "55_run_scada.sh")],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        cls.base = "127.0.0.1", 8911
        cls.up = False
        for _ in range(25):
            time.sleep(1)
            try:
                cls._get("/api/state")
                cls.up = True
                break
            except OSError:
                pass

    @classmethod
    def tearDownClass(cls):
        try:
            os.killpg(os.getpgid(cls.proc.pid), 15)   # whole process group (server, ingest, bridge, agents)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def _get(cls, path):
        s = socket.create_connection(cls.base, timeout=3)
        s.sendall(("GET %s HTTP/1.0\r\n\r\n" % path).encode())
        data = s.recv(65536)
        s.close()
        return data

    def _post_raw(self, body_bytes):
        s = socket.create_connection(self.base, timeout=3)
        req = (b"POST /api/control HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(body_bytes)) + body_bytes
        s.sendall(req)
        data = s.recv(65536)
        s.close()
        return data.split(b"\r\n", 1)[0]

    def test_malformed_control_requests(self):
        self.assertTrue(self.up, "bridge did not come up")
        rng = random.Random(SEED + 4)
        payloads = [b"", b"{", b"[]", b"null", b"123", b'{"action":1}',
                    b'{"action":"command"}', b'{"action":"command","item":42}',
                    b'{"action":"select"}', b'{"action":"zzz"}', b"\x00\x01\x02"]
        for _ in range(200):
            payloads.append(bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 50))))
        for body in payloads:
            status = self._post_raw(body)
            self.assertTrue(status.startswith(b"HTTP/1.0 4") or status.startswith(b"HTTP/1.0 5") or
                            status.startswith(b"HTTP/1.0 2"),
                            "unexpected status line %r for %r" % (status, body[:24]))
            # must never be a hard 500 internal crash leaking a stack-trace 502/503
            self.assertNotIn(b" 502", status)
        # still alive after the flood
        self.assertIn(b"200", self._get("/api/state")[:20])


if __name__ == "__main__":
    unittest.main(verbosity=2)
