#!/usr/bin/env python3
"""Tests for the inter-control-center relay (the federation tie).

Config parsing runs anywhere. The live test stands up two servers (two control
centers), runs a tie between them, writes a value into the source, and checks it
crosses the intertie and appears in the destination. It uses the independent
pyiec61850 stack to write and read, and the relay drives the real agent, so the
mirror is exercised end to end. Skipped automatically if the tools are not built.
"""
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))
import relay  # noqa: E402

SERVER = os.path.join(ROOT, "src", "tase2_server")
AGENT = os.path.join(ROOT, "src", "tase2_hmi_agent")
PYBIND = os.path.join(ROOT, "deps", "libiec61850", "build", "pyiec61850")
GEN = os.path.join(ROOT, "scripts", "gen_server_points.py")

_have = all(os.path.isfile(p) for p in (SERVER, AGENT)) and \
    os.path.isfile(os.path.join(PYBIND, "pyiec61850.py"))
if _have:
    sys.path.insert(0, PYBIND)
    try:
        import pyiec61850 as mms
    except Exception:
        _have = False


class TestRelayConfig(unittest.TestCase):
    def test_point_types(self):
        types, domain = relay.point_types(os.path.join(ROOT, "config", "scada.json"))
        self.assertEqual(domain, "TestDomain")
        self.assertTrue(types["plc1_mw"])        # real -> float
        self.assertFalse(types["plc1_brk"])      # state -> int

    def test_partner_config(self):
        types, domain = relay.point_types(os.path.join(ROOT, "config", "scada_b.json"))
        self.assertEqual(domain, "PartnerICC")
        self.assertIn("tieA_mw", types)


def _start_server(port, config, domain):
    pts = subprocess.check_output([sys.executable, GEN, config])
    pfile = os.path.join(HERE, ".relay_pts_%d.conf" % port)
    with open(pfile, "wb") as f:
        f.write(pts)
    srv = subprocess.Popen(
        [SERVER, "-i", "127.0.0.1", "-p", str(port), "-d", domain,
         "-t", "2", "-o", "60", "-n", "-P", pfile],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10                  # wait until it accepts a connection
    while time.time() < deadline:
        c = mms.MmsConnection_create()
        if mms.MmsConnection_connect(c, mms.toMmsErrorP(), "127.0.0.1", port):
            mms.MmsConnection_destroy(c)
            break
        mms.MmsConnection_destroy(c)
        time.sleep(0.3)
    return srv, pfile


@unittest.skipUnless(_have, "server/agent/pyiec61850 not built (run scripts/10_build.sh)")
class TestRelayLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pa, cls.pb = 11702, 11703
        cls.srvA, cls.fa = _start_server(cls.pa, os.path.join(ROOT, "config", "scada.json"), "TestDomain")
        cls.srvB, cls.fb = _start_server(cls.pb, os.path.join(ROOT, "config", "scada_b.json"), "PartnerICC")
        centers = {"A": {"host": "127.0.0.1", "port": cls.pa, "config": "config/scada.json"},
                   "B": {"host": "127.0.0.1", "port": cls.pb, "config": "config/scada_b.json"}}
        link = {"from": "A", "to": "B", "points": {"plc1_mw": "tieA_mw"}}
        cls.tie = relay.Tie(link, centers)
        cls.tie.start()
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.tie.stop()
        for srv in (cls.srvA, cls.srvB):
            srv.terminate()
            try:
                srv.wait(timeout=3)
            except subprocess.TimeoutExpired:
                srv.kill()
        for f in (cls.fa, cls.fb):
            if os.path.isfile(f):
                os.remove(f)

    def test_value_crosses_the_tie(self):
        # write into CC-A
        conA = mms.MmsConnection_create()
        self.assertTrue(mms.MmsConnection_connect(conA, mms.toMmsErrorP(), "127.0.0.1", self.pa))
        mms.MmsConnection_writeVariable(conA, mms.toMmsErrorP(), "TestDomain",
                                        "plc1_mw$Value", mms.MmsValue_newFloat(42.0))
        mms.MmsConnection_destroy(conA)

        # the relay should mirror it into CC-B within an integrity cycle or two
        conB = mms.MmsConnection_create()
        self.assertTrue(mms.MmsConnection_connect(conB, mms.toMmsErrorP(), "127.0.0.1", self.pb))
        got = None
        for _ in range(20):
            time.sleep(0.5)
            v = mms.MmsConnection_readVariable(conB, mms.toMmsErrorP(), "PartnerICC", "tieA_mw")
            if v and mms.MmsValue_getTypeString(v) == "structure":
                got = mms.MmsValue_toFloat(mms.MmsValue_getElement(v, 0))
                if abs(got - 42.0) < 0.01:
                    break
        mms.MmsConnection_destroy(conB)
        self.assertIsNotNone(got, "destination never received a value")
        self.assertAlmostEqual(got, 42.0, places=2, msg="value did not cross the tie intact")


if __name__ == "__main__":
    unittest.main(verbosity=2)
