#!/usr/bin/env python3
"""
Minimal Modbus TCP slave (server) simulator, standard library only.

A bench device for the Modbus side of the gateway, the counterpart to the DNP3
outstation simulator. It lets the universal multi-protocol demo show real Modbus
traffic on the wire with no hardware.

It answers read holding/input registers (function codes 3 and 4) and accepts
writes (5 write coil, 6 write single register, 16 write multiple registers). A few
telemetry registers return moving values so a poll shows live data; written
registers are stored and read back, so the control loop (operator command ->
register write -> read-back) works end to end.

It is NOT a conformant PLC. Do not point it at production equipment.

usage:
  python3 modbus_outstation_sim.py [--port 1502]
"""

import argparse
import math
import socketserver
import struct
import time

# Telemetry channels: register address -> (base register value, amplitude). These
# return a moving value (read-only). The gateway scales them to engineering units.
TELEMETRY = {
    100: (138, 22),    # ~13.8 MW after scale 0.1
    102: (1382, 10),   # ~138.2 kV after scale 0.1
    104: (92, 16),     # ~9.2 MW
    106: (1361, 9),    # ~136.1 kV
}

STORE = {}             # written holding/input registers (control read-back)
COILS = {}             # written coils
T0 = time.time()


def regval(addr):
    if addr in STORE:
        return STORE[addr] & 0xFFFF
    if addr in TELEMETRY:
        base, amp = TELEMETRY[addr]
        return int(base + amp * math.sin((time.time() - T0) / 3.0 + addr)) & 0xFFFF
    return 0


def handle_pdu(pdu):
    """Take a Modbus PDU (function code + data), return the response PDU. Malformed
    requests get a Modbus exception response, never a crash."""
    if not pdu:
        return None
    fc = pdu[0]
    try:
        if fc in (3, 4):                                   # read holding / input regs
            addr, count = struct.unpack(">HH", pdu[1:5])
            if count < 1 or count > 125:
                raise ValueError
            regs = b"".join(struct.pack(">H", regval(addr + i)) for i in range(count))
            return bytes([fc, len(regs)]) + regs
        if fc == 6:                                        # write single register
            addr, val = struct.unpack(">HH", pdu[1:5])
            STORE[addr] = val
            return pdu                                     # echo addr,val
        if fc == 16:                                       # write multiple registers
            addr, count = struct.unpack(">HH", pdu[1:5])
            bc = pdu[5]
            data = pdu[6:6 + bc]
            if len(data) < 2 * count:
                raise ValueError
            for i in range(count):
                STORE[addr + i] = struct.unpack(">H", data[2 * i:2 * i + 2])[0]
            return bytes([16]) + struct.pack(">HH", addr, count)
        if fc == 5:                                        # write single coil
            addr, val = struct.unpack(">HH", pdu[1:5])
            COILS[addr] = 1 if val == 0xFF00 else 0
            return pdu
    except (struct.error, ValueError, IndexError):
        return bytes([(fc | 0x80) & 0xFF, 0x03])           # illegal data value
    return bytes([(fc | 0x80) & 0xFF, 0x01])               # illegal function


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        try:
            while True:
                header = _recv_exact(sock, 7)              # MBAP: txn, proto, len, unit
                txn, proto, length, unit = struct.unpack(">HHHB", header)
                if length < 1 or length > 260:
                    break
                pdu = _recv_exact(sock, length - 1)
                resp = handle_pdu(pdu)
                if resp is None:
                    continue
                sock.sendall(struct.pack(">HHHB", txn, 0, len(resp) + 1, unit) + resp)
        except Exception:  # noqa: BLE001 - a bad frame drops only this connection
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="Minimal Modbus TCP slave simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1502)
    args = ap.parse_args()
    srv = Server((args.host, args.port), Handler)
    print("[modbus-sim] slave listening on %s:%d (holding/input regs, coils); Ctrl+C to stop"
          % (args.host, args.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[modbus-sim] stopping", flush=True)


if __name__ == "__main__":
    main()
