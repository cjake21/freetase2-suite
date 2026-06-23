#!/usr/bin/env python3
"""
Minimal DNP3 (IEEE 1815) master over TCP, standard library only.

Enough of DNP3 to poll an outstation and operate a control relay, which is what
the ingestion gateway needs for a power testbed:

  * data link layer: 0x05 0x64 frames with CRC-16/DNP per block
  * transport layer: single-fragment FIR/FIN
  * application layer: READ (FC 1), SELECT (FC 3), OPERATE (FC 4),
    DIRECT_OPERATE (FC 5)
  * objects: binary input (group 1), analog input (group 30 variations 1/2/5/6),
    and control relay output block / CROB (group 12 variation 1)

This is intentionally a focused subset (no unsolicited handling, no time sync, no
fragmentation reassembly beyond multi-block frames). It is built for clarity and
for talking to a real outstation on a bench, not for full conformance.

References: IEEE 1815-2012 (DNP3). CRC is CRC-16/DNP (poly 0x3D65 reflected,
init 0x0000, xorout 0xFFFF).
"""

import socket
import struct

# Application function codes
FC_READ = 0x01
FC_SELECT = 0x03
FC_OPERATE = 0x04
FC_DIRECT_OPERATE = 0x05
FC_RESPONSE = 0x81

# CROB (group 12 var 1) operation types, low nibble of the control code
CROB_PULSE_ON = 0x01
CROB_LATCH_ON = 0x03
CROB_LATCH_OFF = 0x04


# --------------------------------------------------------------------------- #
# CRC-16/DNP
# --------------------------------------------------------------------------- #

def crc16_dnp(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA6BC   # 0x3D65 bit-reversed
            else:
                crc >>= 1
    return (~crc) & 0xFFFF


def _crc_tail(block):
    return struct.pack("<H", crc16_dnp(block))


# --------------------------------------------------------------------------- #
# Data link framing
# --------------------------------------------------------------------------- #

def build_frame(dest, src, payload, ctrl=0xC4):
    """Wrap transport+application payload in a DNP3 data link frame. ctrl 0xC4 =
    DIR=1, PRM=1, unconfirmed user data (master -> outstation)."""
    length = 5 + len(payload)               # ctrl + dest(2) + src(2) + payload
    if length > 255:
        raise ValueError("DNP3 frame too long (%d)" % length)
    header = bytes([0x05, 0x64, length, ctrl]) + struct.pack("<HH", dest, src)
    out = header + _crc_tail(header)
    for i in range(0, len(payload), 16):
        block = payload[i:i + 16]
        out += block + _crc_tail(block)
    return out


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def recv_frame(sock):
    """Read one data link frame and return its reassembled payload (transport +
    application data), validating CRCs."""
    header = _recv_exact(sock, 10)          # 0x05 0x64 len ctrl dest(2) src(2) crc(2)
    if header[0] != 0x05 or header[1] != 0x64:
        raise ValueError("bad DNP3 start octets")
    if crc16_dnp(header[:8]) != struct.unpack("<H", header[8:10])[0]:
        raise ValueError("DNP3 header CRC error")
    length = header[2]
    if length < 5:                          # control+dest+src is the minimum
        raise ValueError("bad DNP3 length %d" % length)
    user_len = length - 5                   # bytes after src, i.e. the payload
    payload = b""
    remaining = user_len
    while remaining > 0:
        n = min(16, remaining)
        block = _recv_exact(sock, n)
        block_crc = _recv_exact(sock, 2)
        if crc16_dnp(block) != struct.unpack("<H", block_crc)[0]:
            raise ValueError("DNP3 data CRC error")
        payload += block
        remaining -= n
    return payload


# --------------------------------------------------------------------------- #
# DNP3 master
# --------------------------------------------------------------------------- #

class Dnp3Master:
    """One TCP association to a DNP3 outstation."""

    def __init__(self, host, port=20000, master_addr=1, outstation_addr=10, timeout=3):
        self.host = host
        self.port = int(port)
        self.master_addr = int(master_addr)
        self.outstation_addr = int(outstation_addr)
        self.timeout = timeout
        self._sock = None
        self._tseq = 0       # transport sequence
        self._aseq = 0       # application sequence

    def _conn(self):
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
        return self._sock

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _transact(self, app):
        """Send an application request and return the application payload of the
        response (application control + FC + IIN + objects)."""
        self._aseq = (self._aseq + 1) & 0x0F
        self._tseq = (self._tseq + 1) & 0x3F
        app_control = 0xC0 | self._aseq          # FIR=1, FIN=1, SEQ
        transport = 0xC0 | self._tseq            # FIR=1, FIN=1, SEQ
        tpdu = bytes([transport, app_control]) + app
        sock = self._conn()
        try:
            sock.sendall(build_frame(self.outstation_addr, self.master_addr, tpdu))
            payload = recv_frame(sock)
        except (OSError, ValueError):
            self.close()
            raise
        # payload = transport byte + application data
        return payload[1:]

    # ---- reads ---- #

    def read_point(self, group, variation, index):
        """READ a single static point by group/variation/index. Returns the
        decoded value (float/int for analog, 0/1 for binary)."""
        # qualifier 0x00 = 1-octet start/stop index range
        obj = bytes([group, variation, 0x00, index, index])
        resp = self._transact(bytes([FC_READ]) + obj)
        return self._parse_single(resp, group, variation, index)

    @staticmethod
    def _parse_single(app, group, variation, want_index):
        """Parse an outstation READ response for one wanted point. The response is
        attacker/peer controlled, so every access is bounds-checked and every
        failure mode raises ValueError (never IndexError or struct.error) and the
        loop is bounded so a hostile frame cannot hang or crash the gateway."""
        # app: FC(1=0x81 response) + IIN(2) + objects
        if len(app) < 3:
            raise ValueError("short DNP3 response")
        p = 3                                    # skip response FC + IIN(2)
        objects = 0
        while p + 3 <= len(app):
            objects += 1
            if objects > 1000:
                raise ValueError("too many object headers")
            g, v, qual = app[p], app[p + 1], app[p + 2]
            p += 3
            if qual == 0x00:                     # 1-octet start/stop
                if p + 2 > len(app):
                    raise ValueError("truncated range")
                start, stop = app[p], app[p + 1]
                p += 2
            elif qual == 0x01:                   # 2-octet start/stop
                if p + 4 > len(app):
                    raise ValueError("truncated range")
                start, stop = struct.unpack("<HH", app[p:p + 4])
                p += 4
            else:
                raise ValueError("unsupported qualifier 0x%02x" % qual)
            count = stop - start + 1
            if count <= 0 or count > 65536:
                raise ValueError("bad object count %d" % count)
            size = Dnp3Master._obj_size(g, v)
            for i in range(count):
                if p + size > len(app):
                    raise ValueError("truncated object data")
                data = app[p:p + size]
                p += size
                if g == group and (start + i) == want_index:
                    return Dnp3Master._decode(g, v, data)
        raise ValueError("point g%d v%d idx%d not in response" % (group, variation, want_index))

    @staticmethod
    def _obj_size(group, variation):
        if group == 1:
            return 1                             # binary input w/ flags: 1 byte
        if group == 30:
            sizes = {1: 5, 2: 3, 5: 5, 6: 9}
            if variation not in sizes:
                raise ValueError("unsupported analog variation %d" % variation)
            return sizes[variation]
        raise ValueError("unsupported object g%d v%d" % (group, variation))

    @staticmethod
    def _decode(group, variation, data):
        try:
            if group == 1:                       # binary input, state in bit 7
                if not data:
                    raise ValueError("short binary object")
                return (data[0] >> 7) & 1
            if group == 30:
                if variation == 1:               # 32-bit int with flags
                    return struct.unpack("<i", data[1:5])[0]
                if variation == 2:               # 16-bit int with flags
                    return struct.unpack("<h", data[1:3])[0]
                if variation == 5:               # 32-bit float with flags
                    return struct.unpack("<f", data[1:5])[0]
                if variation == 6:               # 64-bit double with flags
                    return struct.unpack("<d", data[1:9])[0]
        except struct.error:
            raise ValueError("truncated object data g%d v%d" % (group, variation))
        raise ValueError("cannot decode g%d v%d" % (group, variation))

    # ---- control (CROB, group 12 var 1) ---- #

    def operate_crob(self, index, on, sbo=True, op_type=None):
        """Operate a control relay output block. With sbo=True does select then
        operate; otherwise a direct operate. on=True -> latch on, on=False ->
        latch off (unless op_type overrides the control code)."""
        if op_type is None:
            op_type = CROB_LATCH_ON if on else CROB_LATCH_OFF
        # CROB: control code, count, on-time(4), off-time(4), status(1)
        crob = struct.pack("<BBIIB", op_type, 1, 0, 0, 0)
        # qualifier 0x17 = 1-octet count of objects, each with a 1-octet index prefix
        obj = bytes([12, 1, 0x17, 1, index]) + crob
        if sbo:
            sel = self._transact(bytes([FC_SELECT]) + obj)
            self._check_crob_status(sel)
            opr = self._transact(bytes([FC_OPERATE]) + obj)
            self._check_crob_status(opr)
        else:
            opr = self._transact(bytes([FC_DIRECT_OPERATE]) + obj)
            self._check_crob_status(opr)

    @staticmethod
    def _check_crob_status(app):
        # response echoes the CROB; status is the last byte, 0 = success
        if len(app) >= 1 and app[-1] != 0:
            raise ValueError("CROB rejected, status 0x%02x" % app[-1])
