#!/usr/bin/env python3
"""
tase2_ingest: the southbound ingestion gateway.

This is the piece that makes the TASE.2 publisher carry real field data instead
of the server's synthetic simulateValues() loop. It polls field devices
(PLCs/RTUs) over their own protocol, then writes those values into the TASE.2
server's points over ICCP, reusing the exact write path that the SCADA HMI bridge
already uses.

Data flow (Option A from the README):

  PLCs --(Modbus/DNP3/61850)--> tase2_ingest --(ICCP write)--> tase2_server --(ICCP report)--> HMI

Direction of the southbound link (important): this gateway is a Modbus TCP
*master/poller*. It opens the connections OUTBOUND to each PLC and reads its
registers on a timer. You therefore do NOT configure the PLCs to "send" anywhere;
you point the gateway AT the PLCs. Each PLC must run a Modbus TCP server (slave)
on its port (default 502) and expose the registers you list. The gateway host
just needs a network route to the PLC subnet. (DNP3 unsolicited / IEC 60870-5-104
style push from the device would be a different reader; the Modbus reader here is
poll-only.)

Adding PLCs: list each PLC once under "devices" in the tag database and reference
it by name from each tag (see ingest/tags.4plc.example.json). The gateway watches
the tag file and reloads it live, so adding a fifth PLC is an edit-and-save, not a
restart. There is no network auto-discovery: devices and their register maps are
always declared explicitly, which is the safe/standard practice on an OT segment.

How the ICCP write happens: instead of re-implementing an ICCP client, this
gateway drives the existing src/tase2_hmi_agent subprocess and sends it the same
WRITEF/WRITEI line commands the bridge sends. The server's injection-hold (-o)
keeps each written value pinned, so we re-assert on a heartbeat and the value
stays put between polls.

What is real here and what is a stub:

  * The Modbus TCP reader is real and dependency-free (plain sockets, function
    codes 3 and 4). Point it at a real PLC and it reads.
  * The "stub" driver returns a fixed value. It exists only to test the gateway
    plumbing end to end without a PLC on the bench. It is NOT the server's
    simulation, and it is clearly labelled so nobody mistakes it for real data.

Add more field protocols by writing another reader class and registering it in
DRIVERS. DNP3 and IEC 61850 are the obvious next two.

Stdlib only. Python 3.7+.
"""

import argparse
import json
import math
import os
import struct
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_BIN = os.path.join(HERE, "..", "src", "tase2_hmi_agent")

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dnp3  # noqa: E402  (local module, sys.path adjusted above)

# TASE.2 quality byte (IEC 60870-6-802). Validity in bits 2-3, current source in
# bits 4-5. We publish telemetered field data, valid on a good read and not-valid
# when the device read fails.
Q_VALID = 0          # validity VALID | source TELEMETERED
Q_HELD = 8           # validity HELD
Q_SUSPECT = 4        # validity SUSPECT
Q_NOTVALID = 12      # validity NOTVALID


def log(msg):
    """Operational log line. Always flush: when this runs under systemd or with
    stdout redirected to a file, the default block buffering would otherwise hide
    or lose these lines (including the final shutdown notice)."""
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Field-device readers (southbound)
# --------------------------------------------------------------------------- #

# Shared in-memory state for stub devices: a stub control write lands here and the
# matching stub read returns it, so the full command loop (HMI -> ICCP -> ingest ->
# "PLC" -> read-back -> ICCP -> HMI) works on the bench with no real hardware.
STUB_STATE = {}


class StubReader:
    """A development stand-in for a field device.

    Use this to prove the gateway can read a tag, scale it, and land it on the
    server's point before you have a real PLC wired up. This is plumbing only.
    It is not the server's simulateValues() and it is not real field data.

    Options (for demos):
      value  : the base value to return (default 0)
      jitter : if set, add jitter*sin(t) each read so the value visibly moves,
               which keeps the point 'fresh' for the HMI's comms indicator
      down   : if true, every read raises - simulating an unreachable device, so
               its station goes STALE/OFFLINE in the HMI

    If a command has been written to this point (via a stub control), the stub
    returns the commanded value, so the read-back reflects the operator's command.
    """

    def __init__(self, tag):
        self._name = tag.get("point")
        self._value = tag.get("value", 0)
        self._jitter = float(tag.get("jitter", 0))
        self._down = bool(tag.get("down", False))
        self._t = 0

    def read(self):
        if self._down:
            raise IOError("stub: simulated device offline")
        if self._name in STUB_STATE:        # a command landed: reflect it back
            return STUB_STATE[self._name]
        self._t += 1
        if self._jitter:
            return self._value + self._jitter * math.sin(self._t / 3.0)
        return self._value

    def close(self):
        pass


class ModbusTcpReader:
    """A minimal Modbus TCP client over plain sockets.

    Supports reading holding registers (function code 3) and input registers
    (function code 4). One device connection is shared across all tags that name
    the same host/port/unit, so we do not open a socket per tag.
    """

    # one cached connection per (host, port, unit)
    _pool = {}
    _pool_lock = threading.Lock()

    # how many 16-bit registers each decode consumes
    _WORDS = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2}

    def __init__(self, tag):
        self.host = tag["host"]
        self.port = int(tag.get("port", 502))
        self.unit = int(tag.get("unit", 1))
        self.register = int(tag["register"])
        self.kind = tag.get("kind", "holding")  # holding | input
        # uint16 | int16 | uint32 | int32 | float32
        self.decode = tag.get("decode", "uint16")
        if self.decode not in self._WORDS:
            raise ValueError("unknown modbus decode %r" % self.decode)
        # Word order for 32-bit values across the two registers. Real PLCs differ:
        # "big" = high word first (ABCD, the Modbus-standard order), "little" =
        # low word first (CDAB, common on many devices). Defaults to big.
        self.word_order = tag.get("word_order", "big")
        # count defaults to exactly what the decode needs; honour an explicit
        # larger count but never read fewer registers than the decode requires.
        need = self._WORDS[self.decode]
        self.count = max(int(tag.get("count", need)), need)
        self._key = (self.host, self.port, self.unit)
        self._txn = 0

    def _conn(self):
        with ModbusTcpReader._pool_lock:
            sock = ModbusTcpReader._pool.get(self._key)
            if sock is None:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.settimeout(3)
                ModbusTcpReader._pool[self._key] = sock
            return sock

    def _drop_conn(self):
        with ModbusTcpReader._pool_lock:
            sock = ModbusTcpReader._pool.pop(self._key, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _rpc(self, pdu):
        """Send one Modbus PDU and return the response data (the bytes after the
        function code). Handles MBAP framing and exception responses."""
        self._txn = (self._txn + 1) & 0xFFFF
        mbap = struct.pack(">HHHB", self._txn, 0, len(pdu) + 1, self.unit)
        try:
            sock = self._conn()
            sock.sendall(mbap + pdu)
            header = self._recv_exact(sock, 7)               # MBAP
            _, _, length, _unit = struct.unpack(">HHHB", header)
            resp = self._recv_exact(sock, length - 1)        # PDU: func code + data
        except (OSError, ValueError):
            # one reconnect attempt, then let the caller mark the tag bad
            self._drop_conn()
            raise
        if not resp:
            raise ValueError("empty modbus response")
        if resp[0] & 0x80:
            raise ValueError("modbus exception 0x%02x" % (resp[1] if len(resp) > 1 else 0))
        return resp[1:]

    def read(self):
        fc = 3 if self.kind == "holding" else 4
        data = self._rpc(struct.pack(">BHH", fc, self.register, self.count))
        # data is peer-controlled: bounds-check before decoding so a malformed
        # response raises ValueError rather than IndexError/struct.error
        if len(data) < 1:
            raise ValueError("short modbus response")
        byte_count = data[0]
        if byte_count < 2 * self.count or len(data) < 1 + byte_count:
            raise ValueError("truncated modbus payload (%d bytes)" % byte_count)
        try:
            regs = struct.unpack(">%dH" % (byte_count // 2), data[1:1 + byte_count])
        except struct.error:
            raise ValueError("bad modbus payload")
        return self._decode(regs)

    # ---- southbound writes (control path) ---- #

    def write_control(self, control, value):
        """Write a command value down to the device (the control path).

        control.kind picks the function:
          coil    -> FC 5  write single coil (discrete on/off)
          holding -> FC 6  write single register (integer)
          float32 -> FC 16 write two registers (float, honours word_order)
        """
        kind = control.get("kind", "coil")
        reg = int(control["register"])
        if kind == "coil":
            self._rpc(struct.pack(">BHH", 5, reg, 0xFF00 if int(round(value)) else 0x0000))
        elif kind in ("float32", "setpoint"):
            hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
            if control.get("word_order", "big") != "big":
                hi, lo = lo, hi
            self._rpc(struct.pack(">BHHB", 16, reg, 2, 4) + struct.pack(">HH", hi, lo))
        else:  # holding / register
            self._rpc(struct.pack(">BHH", 6, reg, int(round(value)) & 0xFFFF))

    def _decode(self, regs):
        if self.decode == "int16":
            v = regs[0]
            return v - 0x10000 if v >= 0x8000 else v
        if self.decode == "uint16":
            return regs[0]
        # 32-bit decodes span two registers; apply the configured word order.
        hi, lo = (regs[0], regs[1]) if self.word_order == "big" else (regs[1], regs[0])
        raw = struct.pack(">HH", hi, lo)
        if self.decode == "float32":
            return struct.unpack(">f", raw)[0]
        if self.decode == "int32":
            return struct.unpack(">i", raw)[0]
        return struct.unpack(">I", raw)[0]  # uint32

    @staticmethod
    def _recv_exact(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed")
            buf += chunk
        return buf

    def close(self):
        self._drop_conn()


class Dnp3Reader:
    """A DNP3 (IEEE 1815) master reader/control target over TCP.

    Reads one static point (binary input group 1, or analog input group 30) from
    an outstation, and for controllable points operates a CROB (group 12). One
    master association is shared per (host, port, outstation).

    Tag fields: host, port (default 20000), outstation (default 10), master
    (default 1), group (default 30), variation (default 5 = analog float), index.
    For control add control.index (CROB index, defaults to the read index) and
    control.sbo (default true = select-before-operate, false = direct operate).
    """

    _pool = {}
    _lock = threading.Lock()

    def __init__(self, tag):
        self.host = tag["host"]
        self.port = int(tag.get("port", 20000))
        self.outstation = int(tag.get("outstation", 10))
        self.master = int(tag.get("master", 1))
        self.group = int(tag.get("group", 30))
        self.variation = int(tag.get("variation", 5))
        self.index = int(tag["index"])
        self._key = (self.host, self.port, self.outstation)

    def _master(self):
        with Dnp3Reader._lock:
            m = Dnp3Reader._pool.get(self._key)
            if m is None:
                m = dnp3.Dnp3Master(self.host, self.port, self.master, self.outstation)
                Dnp3Reader._pool[self._key] = m
            return m

    def _drop(self):
        with Dnp3Reader._lock:
            m = Dnp3Reader._pool.pop(self._key, None)
        if m is not None:
            m.close()

    def read(self):
        try:
            return self._master().read_point(self.group, self.variation, self.index)
        except (OSError, ValueError, struct.error, IndexError):
            # any I/O or malformed-response error: drop the association so the next
            # poll reconnects cleanly rather than getting stuck on a bad socket
            self._drop()
            raise

    def write_control(self, control, value):
        idx = int(control.get("index", self.index))
        sbo = bool(control.get("sbo", True))
        self._master().operate_crob(idx, on=bool(int(round(value))), sbo=sbo)

    def close(self):
        self._drop()


# Register new field protocols here.
DRIVERS = {
    "stub": StubReader,
    "modbus": ModbusTcpReader,
    "dnp3": Dnp3Reader,
    # "iec61850": Iec61850Reader # TODO
}


class StubControlTarget:
    """Control target for a stub point: a command 'written to the PLC' is stored
    in STUB_STATE so the matching StubReader returns it, closing the loop on the
    bench with no hardware."""

    def __init__(self, point):
        self.point = point

    def write_control(self, control, value):
        STUB_STATE[self.point] = value


# --------------------------------------------------------------------------- #
# Northbound writer (the existing ICCP write path)
# --------------------------------------------------------------------------- #

class IccpWriter:
    """Drives one src/tase2_hmi_agent subprocess and writes points over ICCP."""

    def __init__(self, host, port, domain):
        if not os.path.isfile(AGENT_BIN):
            sys.exit("[ingest] build first: (cd src && make tase2_hmi_agent)")
        self.proc = subprocess.Popen(
            [AGENT_BIN, host, str(port), domain],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.online = False
        self._lock = threading.Lock()
        self._reads = {}                      # item -> last read value
        self._read_cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ev") == "online":
                self.online = True
            elif ev.get("ev") == "error":
                log("[ingest] ICCP agent: %s" % ev.get("msg", "error"))
            elif ev.get("ev") == "read":
                with self._read_cv:
                    self._reads[ev.get("item")] = ev.get("value")
                    self._read_cv.notify_all()

    def wait_online(self, timeout):
        """Block until the agent reports its ICCP association is up, or timeout.
        Returns True if online. The agent emits {"ev":"online"} after it connects
        and {"ev":"error"} if the connect fails, so this also surfaces a dead
        server instead of letting the first writes vanish into a broken pipe."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.online:
                return True
            if self.proc.poll() is not None:
                return False  # agent exited (e.g. connect failed)
            time.sleep(0.1)
        return self.online

    def _send(self, cmd):
        with self._lock:
            if self.proc.poll() is not None:
                raise IOError("ICCP agent exited (rc=%s)" % self.proc.returncode)
            try:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError) as e:
                raise IOError("ICCP write pipe closed: %s" % e)

    def write(self, point, value, is_float):
        self._send(("WRITEF %s %r" % (point, float(value))) if is_float
                   else ("WRITEI %s %d" % (point, int(value))))

    def write_q(self, point, value, is_float, quality, ts):
        """Write a point with its TASE.2 quality byte and Unix-seconds time tag.
        This carries real field quality (valid vs not-valid) and acquisition time
        end to end, instead of the HMI inferring it from value freshness."""
        v = repr(float(value)) if is_float else str(int(value))
        self._send("WRITEQ %s %d %s %d %d" % (point, 1 if is_float else 0, v,
                                              int(quality), int(ts)))

    def request_read(self, item):
        """Ask the agent to read a point/control object (fire and forget). The
        response lands asynchronously in self._reads via the reader thread."""
        self._send("READ " + item)

    def last_read(self, item):
        """Most recent value seen for item (element 0, e.g. a control Command), or
        None if none yet. Paired with request_read this avoids blocking the poll
        loop waiting on a response while the agent is busy with writes."""
        with self._read_cv:
            return self._reads.get(item)

    def read(self, item, timeout=1.0):
        """Synchronous read (used by tooling/tests). The gateway's poll loop uses
        request_read/last_read instead to stay non-blocking under write load."""
        with self._read_cv:
            self._reads.pop(item, None)
        self._send("READ " + item)
        with self._read_cv:
            self._read_cv.wait_for(lambda: item in self._reads, timeout=timeout)
            return self._reads.get(item)

    def stop(self):
        with self._lock:
            if self.proc.poll() is None and self.proc.stdin:
                try:
                    self.proc.stdin.write("QUIT\n")
                    self.proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


# --------------------------------------------------------------------------- #
# Tag-database loading (device-centric, with optional hot reload)
# --------------------------------------------------------------------------- #

def resolve_tag(tag, devices):
    """Merge a named device's connection fields into a tag.

    A tag names its device once with "device": "<name>" and that device's
    connection fields (driver/host/port/unit) are filled in from the top-level
    "devices" map. This is what keeps a 4-PLC config readable: each PLC's address
    is declared once as a device, and every tag that lives on it just references
    the name. A tag may still carry its own connection fields inline (the legacy
    flat format), and any field set directly on the tag overrides the device."""
    ref = tag.get("device")
    if ref is None:
        return tag
    if ref not in devices:
        raise ValueError("tag %r references unknown device %r"
                         % (tag.get("point"), ref))
    merged = dict(devices[ref])
    merged.update(tag)  # explicit tag fields win over device defaults
    return merged


def build_runtime_tags(tag_dicts, devices):
    """Turn raw config tags into runtime tags (with an open reader each).

    Raises ValueError on any bad entry so the caller can decide whether that is
    fatal (initial load) or should be ignored in favour of the running config
    (hot reload)."""
    runtime = []
    try:
        for t in tag_dicts:
            spec = resolve_tag(t, devices)
            driver_name = spec.get("driver")
            if driver_name not in DRIVERS:
                raise ValueError("unknown driver %r for point %r"
                                 % (driver_name, spec.get("point")))
            reader = DRIVERS[driver_name](spec)
            is_float = spec.get("type", "float") == "float"
            tag = {
                "point": spec["point"],
                "is_float": is_float,
                "scale": float(spec.get("scale", 1.0)),
                "offset": float(spec.get("offset", 0.0)),
                "reader": reader,
                "name": "%s<-%s" % (spec["point"], driver_name),
                "bad": False,
                "control": None,
            }
            # Southbound control: if this tag is commandable, the gateway reads the
            # server's control object (<point>_ctl by default, set by an HMI
            # operate) and writes any new command down to the PLC.
            ctl = spec.get("control")
            if ctl is not None:
                target = StubControlTarget(spec["point"]) if driver_name == "stub" else reader
                tag["control"] = {
                    "object": ctl.get("object", spec["point"] + "_ctl"),
                    "spec": ctl,
                    "target": target,
                    "last": None,
                    "primed": False,
                }
            runtime.append(tag)
    except Exception:
        for tag in runtime:  # don't leak sockets opened before the bad entry
            tag["reader"].close()
        raise
    return runtime


def load_config(path):
    """Read the tag database file and return (devices, tag_dicts).

    Accepts the device-centric form ({"devices": {...}, "tags": [...]}), the flat
    form ({"tags": [...]}) where each tag carries its own connection fields, and a
    bare top-level list of tags."""
    with open(path) as f:
        cfg = json.load(f)
    if isinstance(cfg, list):
        return {}, cfg
    return cfg.get("devices", {}), cfg.get("tags", [])


# --------------------------------------------------------------------------- #
# The gateway
# --------------------------------------------------------------------------- #

class Gateway:
    def __init__(self, tags_path, writer, poll_sec):
        self.tags_path = tags_path
        self.writer = writer
        self.poll_sec = poll_sec
        self._mtime = self._stat()
        devices, tag_dicts = load_config(tags_path)
        try:
            self.tags = build_runtime_tags(tag_dicts, devices)
        except (ValueError, KeyError, OSError) as e:
            sys.exit("[ingest] bad tag database %s: %s" % (tags_path, e))
        self._running = True

    def _stat(self):
        try:
            return os.path.getmtime(self.tags_path)
        except OSError:
            return None

    def reload_if_changed(self):
        """If the tag file changed on disk, rebuild devices+tags live.

        This is how adding more PLCs takes effect without a restart: edit the tag
        database (add a device and its tags, or new tags on an existing device),
        save, and the next poll cycle picks it up. A broken edit is logged and the
        currently-running configuration is kept, so a typo never takes the
        gateway down."""
        mtime = self._stat()
        if mtime is None or mtime == self._mtime:
            return
        try:
            devices, tag_dicts = load_config(self.tags_path)
            new_tags = build_runtime_tags(tag_dicts, devices)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
            log("[ingest] reload skipped, keeping running config: %s" % e)
            self._mtime = mtime  # don't retry the same broken file every cycle
            return
        for tag in self.tags:  # release readers/sockets from the old config
            tag["reader"].close()
        self.tags = new_tags
        self._mtime = mtime
        log("[ingest] reloaded tag database: now %d tag(s) across %d device(s)"
            % (len(self.tags), len(devices)))

    def poll_once(self):
        for tag in self.tags:
            try:
                raw = tag["reader"].read()
                value = raw * tag["scale"] + tag["offset"]
                ts = int(time.time())
                self.writer.write_q(tag["point"], value, tag["is_float"], Q_VALID, ts)
                tag["last_value"] = value
                tag["last_ts"] = ts
                if tag["bad"]:
                    log("[ingest] %s recovered" % tag["name"])
                    tag["bad"] = False
            except Exception as e:  # noqa: BLE001 - one bad tag must not stop the rest
                # Mark the point NOT-VALID (its quality propagates to the HMI),
                # holding the last good value (or 0 if never read) and time tag, so
                # a dead device shows as bad quality instead of a stale value.
                self.writer.write_q(tag["point"], tag.get("last_value", 0) or 0,
                                    tag["is_float"], Q_NOTVALID,
                                    tag.get("last_ts", int(time.time())))
                if not tag["bad"]:
                    log("[ingest] %s read failed: %s" % (tag["name"], e))
                    tag["bad"] = True
            self._service_control(tag)

    def _service_control(self, tag):
        """Pull this tag's command from the server and, if it changed, write it
        down to the PLC. The first observed command is taken as the baseline and
        not pushed, so startup does not force an unintended command."""
        ctl = tag.get("control")
        if ctl is None:
            return
        try:
            # Use the value captured from the previous poll's response, then ask
            # for a fresh one (non-blocking). A one-poll lag on commands is fine.
            cmd = self.writer.last_read(ctl["object"])
            self.writer.request_read(ctl["object"])
            if cmd is None:
                return
            if not ctl["primed"]:
                ctl["last"] = cmd
                ctl["primed"] = True
                return
            if cmd != ctl["last"]:
                # Operator commands are in engineering units; convert back to raw
                # device units (inverse of value = raw*scale + offset) before
                # writing down, so a scaled setpoint round-trips on read-back.
                scale = tag["scale"] or 1.0
                raw = (cmd - tag["offset"]) / scale
                ctl["target"].write_control(ctl["spec"], raw)
                log("[ingest] command %s = %s -> %s (raw %s)" % (ctl["object"], cmd, tag["point"], raw))
                ctl["last"] = cmd
        except Exception as e:  # noqa: BLE001
            log("[ingest] control %s failed: %s" % (ctl["object"], e))

    def run(self):
        if not self.writer.wait_online(timeout=10):
            log("[ingest] ICCP association did not come online; aborting")
            self.writer.stop()
            return
        log("[ingest] ICCP association online; polling %d tag(s) every %.1fs; Ctrl+C to stop"
            % (len(self.tags), self.poll_sec))
        try:
            while self._running:
                # A dead agent is unrecoverable here (nothing restarts it), so
                # stop rather than spin marking every tag bad forever.
                if self.writer.proc.poll() is not None:
                    log("[ingest] ICCP agent exited; stopping")
                    break
                self.reload_if_changed()
                self.poll_once()
                time.sleep(self.poll_sec)
        except KeyboardInterrupt:
            log("\n[ingest] shutting down")
        finally:
            for tag in self.tags:
                tag["reader"].close()
            self.writer.stop()


def main():
    ap = argparse.ArgumentParser(description="TASE.2 southbound ingestion gateway")
    ap.add_argument("--tags", default=os.path.join(HERE, "tags.example.json"),
                    help="tag database (JSON) mapping ICCP points to field devices")
    ap.add_argument("--server-host", default=os.environ.get("TASE2_HOST", "127.0.0.1"))
    ap.add_argument("--server-port", type=int,
                    default=int(os.environ.get("TASE2_PORT", "102")))
    ap.add_argument("--domain", default=os.environ.get("TASE2_DOMAIN", "TestDomain"))
    ap.add_argument("--poll-sec", type=float, default=2.0,
                    help="how often to poll every tag")
    args = ap.parse_args()

    if not os.path.isfile(args.tags):
        sys.exit("[ingest] tag database not found: %s" % args.tags)

    writer = IccpWriter(args.server_host, args.server_port, args.domain)
    log("[ingest] writing to TASE.2 server %s:%d domain %s"
        % (args.server_host, args.server_port, args.domain))
    Gateway(args.tags, writer, args.poll_sec).run()


if __name__ == "__main__":
    main()
