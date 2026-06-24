#!/usr/bin/env python3
"""Bilateral-table enforcement gate: prove the server scopes a peer's access.

Drives the server with the independent pyiec61850 MMS stack (a different code path
from this project's own client) under a bilateral table that scopes the loopback
peer to the plc1 points, and checks that reads, and controls outside the table are
denied while in-scope access and the handshake objects work. This is the test that
turns "the bilateral table is published" into "the bilateral table is enforced".

Skipped automatically if the server or pyiec61850 are not built. Report-member
withholding (an out-of-scope point returned NOT-VALID in a Block 2 report) uses the
same predicate as the read gate and is covered by the live smoke checks, since the
binding does not deliver unsolicited reports.
"""
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SERVER = os.path.join(ROOT, "src", "tase2_server")
PYBIND = os.path.join(ROOT, "deps", "libiec61850", "build", "pyiec61850")
CONFIG = os.path.join(ROOT, "config", "scada.json")
GEN = os.path.join(ROOT, "scripts", "gen_server_points.py")
DOMAIN = "TestDomain"

_have = os.path.isfile(SERVER) and os.path.isfile(os.path.join(PYBIND, "pyiec61850.py"))
if _have:
    sys.path.insert(0, PYBIND)
    try:
        import pyiec61850 as mms
    except Exception:
        _have = False


def _start_server(port, blt_text):
    pts = subprocess.check_output([sys.executable, GEN, CONFIG])
    pfile = os.path.join(HERE, ".blt_points_%d.conf" % port)
    bfile = os.path.join(HERE, ".blt_%d.conf" % port)
    with open(pfile, "wb") as f:
        f.write(pts)
    with open(bfile, "w") as f:
        f.write(blt_text)
    srv = subprocess.Popen(
        [SERVER, "-i", "127.0.0.1", "-p", str(port), "-d", DOMAIN,
         "-o", "60", "-n", "-P", pfile, "-B", bfile],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline:
        c = mms.MmsConnection_create()
        if mms.MmsConnection_connect(c, mms.toMmsErrorP(), "127.0.0.1", port):
            mms.MmsConnection_destroy(c)
            break
        mms.MmsConnection_destroy(c)
        time.sleep(0.3)
    return srv, pfile, bfile


@unittest.skipUnless(_have, "server binary or pyiec61850 not built (run scripts/10_build.sh)")
class TestBilateralTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # loopback peer may read+control plc1 points only
        cls.srv_rc, cls.p1, cls.b1 = _start_server(11602, "127.0.0.1 rc plc1_*\n")
        # loopback peer may only read plc1 points (no control)
        cls.srv_ro, cls.p2, cls.b2 = _start_server(11603, "127.0.0.1 r plc1_*\n")

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.srv_rc, cls.srv_ro):
            srv.terminate()
            try:
                srv.wait(timeout=3)
            except subprocess.TimeoutExpired:
                srv.kill()
        for f in (cls.p1, cls.b1, cls.p2, cls.b2):
            if os.path.isfile(f):
                os.remove(f)

    def _con(self, port):
        con = mms.MmsConnection_create()
        self.assertTrue(mms.MmsConnection_connect(con, mms.toMmsErrorP(), "127.0.0.1", port),
                        "scoped peer should still associate")
        return con

    @staticmethod
    def _denied(v):
        """A denied read comes back either as a null value or, through pyiec61850,
        as an MmsValue carrying a data-access-error rather than the real data."""
        return v is None or mms.MmsValue_getType(v) == mms.MMS_DATA_ACCESS_ERROR

    def test_in_scope_read_allowed(self):
        con = self._con(11602)
        try:
            v = mms.MmsConnection_readVariable(con, mms.toMmsErrorP(), DOMAIN, "plc1_mw")
            self.assertFalse(self._denied(v), "in-scope point should be readable")
            self.assertEqual(mms.MmsValue_getTypeString(v), "structure")
        finally:
            mms.MmsConnection_destroy(con)

    def test_out_of_scope_read_denied(self):
        con = self._con(11602)
        try:
            v = mms.MmsConnection_readVariable(con, mms.toMmsErrorP(), DOMAIN, "plc2_mw")
            self.assertTrue(self._denied(v), "out-of-scope point must not be readable")
        finally:
            mms.MmsConnection_destroy(con)

    def test_handshake_objects_always_readable(self):
        con = self._con(11602)
        try:
            v = mms.MmsConnection_readVariable(con, mms.toMmsErrorP(), None, "TASE2_Version")
            self.assertFalse(self._denied(v),
                             "handshake metadata must stay readable so a partner can associate")
        finally:
            mms.MmsConnection_destroy(con)

    def test_in_scope_control_allowed(self):
        con = self._con(11602)
        err = mms.toMmsErrorP()
        try:
            mms.MmsConnection_writeVariable(con, err, DOMAIN, "plc1_avr_ctl$Command",
                                            mms.MmsValue_newFloat(1.5))
            v = mms.MmsConnection_readVariable(con, err, DOMAIN, "plc1_avr_ctl")
            self.assertAlmostEqual(mms.MmsValue_toFloat(mms.MmsValue_getElement(v, 0)),
                                   1.5, places=3, msg="in-scope control should take effect")
        finally:
            mms.MmsConnection_destroy(con)

    def test_control_denied_when_read_only(self):
        con = self._con(11603)
        err = mms.toMmsErrorP()
        try:
            mms.MmsConnection_writeVariable(con, err, DOMAIN, "plc1_avr_ctl$Command",
                                            mms.MmsValue_newFloat(2.0))
            v = mms.MmsConnection_readVariable(con, err, DOMAIN, "plc1_avr_ctl")
            self.assertEqual(mms.MmsValue_toFloat(mms.MmsValue_getElement(v, 0)), 0.0,
                             "a read-only peer's control must be rejected")
        finally:
            mms.MmsConnection_destroy(con)


if __name__ == "__main__":
    unittest.main(verbosity=2)
