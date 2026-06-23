#!/usr/bin/env python3
"""
Minimal DNP3 outstation simulator, standard library only.

A bench target for the DNP3 master in the ingestion gateway (and for the tests).
It listens on TCP 20000, answers READ requests for binary inputs (group 1) and
analog inputs (group 30), and accepts CROB (group 12) select/operate, updating the
matching binary input so the control loop reads back the change.

It is NOT a conformant outstation. It implements just enough of DNP3 to exercise
the master end to end. Do not point it at production equipment.

usage:
  python3 dnp3_outstation_sim.py [--port 20000] [--addr 10]
Analog inputs default to a couple of moving values; binary inputs default to 0
and follow CROB operates.
"""

import argparse
import math
import socketserver
import struct
import threading
import time

import dnp3


class PointDB:
    def __init__(self):
        self.t0 = time.time()
        # binary inputs (group 1): index -> 0/1
        self.binary = {0: 0, 1: 0}
        # analog inputs (group 30): index -> float
        self.analog = {0: 0.0, 1: 0.0}

    def analog_value(self, idx):
        # a couple of moving values so a poll shows live data
        t = time.time() - self.t0
        if idx == 0:
            return 13.8 + 1.5 * math.sin(t / 3.0)
        if idx == 1:
            return 138.0 + 0.5 * math.cos(t / 5.0)
        return self.analog.get(idx, 0.0)


DB = PointDB()


def encode_point(group, var, idx):
    if group == 1:                              # binary input with flags (var 2)
        state = DB.binary.get(idx, 0)
        return bytes([0x01 | (state << 7)])     # bit0 ONLINE, bit7 STATE
    if group == 30 and var == 5:                # analog input float with flags
        return bytes([0x01]) + struct.pack("<f", DB.analog_value(idx))
    if group == 30 and var == 1:                # analog input 32-bit int with flags
        return bytes([0x01]) + struct.pack("<i", int(DB.analog_value(idx)))
    raise ValueError("unsupported object g%d v%d" % (group, var))


def handle_app(app):
    """Take the application payload of a request, return the application payload
    of the response (or None to stay silent). The request is peer/attacker
    controlled: everything is bounds-checked and a malformed request returns None
    instead of raising, so a hostile frame cannot crash the outstation."""
    if len(app) < 5:                            # app control + FC + group/var/qual
        return None
    fc = app[1]
    p = 2
    group, var, qual = app[p], app[p + 1], app[p + 2]
    p += 3

    if fc == dnp3.FC_READ:
        if qual == 0x00:
            if p + 2 > len(app):
                return None
            start, stop = app[p], app[p + 1]
        elif qual == 0x06:                      # all points
            keys = DB.binary if group == 1 else DB.analog
            start, stop = (min(keys), max(keys)) if keys else (0, 0)
        else:
            if p + 1 > len(app):
                return None
            start, stop = app[p], app[p]
        if stop < start or (stop - start) > 65535:
            return None
        try:
            obj = bytes([group, var, 0x00, start & 0xFF, stop & 0xFF])
            data = b"".join(encode_point(group, var, i) for i in range(start, stop + 1))
        except ValueError:
            return None
        return bytes([dnp3.FC_RESPONSE, 0x00, 0x00]) + obj + data

    if fc in (dnp3.FC_SELECT, dnp3.FC_OPERATE, dnp3.FC_DIRECT_OPERATE):
        # group 12 var 1, qualifier 0x17: count, index, CROB(11)
        if p + 2 + 11 > len(app):
            return None
        count = app[p]; p += 1
        idx = app[p]; p += 1
        crob = app[p:p + 11]
        op = crob[0] & 0x0F
        if fc in (dnp3.FC_OPERATE, dnp3.FC_DIRECT_OPERATE):
            if op == dnp3.CROB_LATCH_ON or op == dnp3.CROB_PULSE_ON:
                DB.binary[idx] = 1
            elif op == dnp3.CROB_LATCH_OFF:
                DB.binary[idx] = 0
        # echo CROB back with status 0 (success)
        echoed = crob[:10] + bytes([0x00])
        return bytes([dnp3.FC_RESPONSE, 0x00, 0x00, 12, 1, 0x17, count, idx]) + echoed

    return None


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        try:
            while True:
                payload = dnp3.recv_frame(sock)     # transport + app
                app = payload[1:]
                resp_app = handle_app(app)
                if resp_app is None:
                    continue
                transport = 0xC0                    # FIR=1, FIN=1, seq 0
                tpdu = bytes([transport]) + resp_app
                # outstation -> master: ctrl 0x44 (DIR=0, PRM=1, unconfirmed)
                sock.sendall(dnp3.build_frame(self.server.master_addr,
                                              self.server.outstation_addr, tpdu, ctrl=0x44))
        except Exception:  # noqa: BLE001 - a bad frame drops only this connection
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="Minimal DNP3 outstation simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20000)
    ap.add_argument("--addr", type=int, default=10, help="outstation address")
    ap.add_argument("--master", type=int, default=1, help="master address")
    args = ap.parse_args()

    srv = Server((args.host, args.port), Handler)
    srv.master_addr = args.master
    srv.outstation_addr = args.addr
    print("[dnp3-sim] outstation %d listening on %s:%d (binary g1, analog g30); Ctrl+C to stop"
          % (args.addr, args.host, args.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dnp3-sim] stopping", flush=True)


if __name__ == "__main__":
    main()
