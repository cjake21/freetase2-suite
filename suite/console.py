#!/usr/bin/env python3
"""
FreeTASE2 Suite control console.

A small management GUI that wraps the whole tool. It lists the named deployments
from suite/profiles.json, starts and stops them, shows which one is running, and
links through to that deployment's SCADA HMI. This is the control plane the final
packaged GUI builds on; the SCADA HMI itself remains the operational view.

  python3 suite/console.py            # serve on http://127.0.0.1:8080

Standard library only. One deployment runs at a time (they share ports by default).
"""

import datetime
import json
import os
import signal
import subprocess
import tempfile
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tase2ctl  # noqa: E402

STATIC = os.path.join(HERE, "static")
DOCS_HTML = os.path.join(tase2ctl.ROOT, "docs", "_build", "html")

DOC_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css",
    ".js": "application/javascript", ".json": "application/json",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".gif": "image/gif", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".eot": "application/vnd.ms-fontobject",
    ".ico": "image/x-icon", ".txt": "text/plain",
}


def _port_open(host, port):
    """True if something is accepting TCP connections on host:port (a cheap
    readiness probe for the HMI bridge, which binds a few seconds after start)."""
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.3):
            return True
    except OSError:
        return False


class Supervisor:
    """Owns the single running deployment subprocess (a launcher process group)."""

    def __init__(self):
        self.proc = None
        self.name = None
        self._logf = None
        self.runs = {}            # deployment name -> last start time (ISO)
        self.logpath = os.path.join(tempfile.gettempdir(), "freetase2-suite-deploy.log")
        self._lock = threading.Lock()

    def tail(self, n=200):
        try:
            with open(self.logpath, "rb") as f:
                data = f.read()
        except OSError:
            return []
        return data.decode("utf-8", "replace").splitlines()[-n:]

    def status(self):
        with self._lock:
            running = self.proc is not None and self.proc.poll() is None
            if not running:
                self.proc = None
                self.name = None
            dep = tase2ctl.load_profiles().get(self.name or "", {})
            http_port = dep.get("http_port", 8800) if running else None
            return {
                "running": running,
                "name": self.name,
                "http_port": http_port,
                # The HMI bridge binds its port a few seconds after the deployment
                # starts (it spins up two ICCP agents first), so report whether it
                # is actually serving yet. The header uses this to avoid offering a
                # SCADA HMI link that points at a port nothing is listening on.
                "hmi_ready": running and _port_open("127.0.0.1", http_port),
                "mode": dep.get("mode") if running else None,
                "security": dep.get("security") if running else None,
                "protocol": dep.get("protocol") if running else None,
                "last_run": self.runs.get(self.name) if running else None,
                "docs_available": os.path.isdir(DOCS_HTML),
            }

    def start(self, name):
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError("a deployment is already running; stop it first")
            argv, env = tase2ctl.build_launch(name)
            self._logf = open(self.logpath, "wb")
            self.proc = subprocess.Popen(
                argv, env=env, cwd=tase2ctl.ROOT,
                stdout=self._logf, stderr=subprocess.STDOUT,
                start_new_session=True)
            self.name = name
            self.runs[name] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def stop(self):
        with self._lock:
            if self.proc is None:
                return
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            if self._logf:
                self._logf.close()
                self._logf = None
            self.proc = None
            self.name = None


SUP = Supervisor()


def precheck(name):
    """Pre-launch health checks and warnings for a deployment, so the operator
    knows what is unsafe or missing before pressing Start."""
    deps = tase2ctl.load_profiles()
    d = deps.get(name)
    if d is None:
        return {"checks": [], "warnings": ["unknown deployment"]}
    R = tase2ctl.ROOT
    checks = []

    def add(label, ok, detail):
        checks.append({"label": label, "ok": bool(ok), "detail": detail})

    bins = all(os.path.isfile(os.path.join(R, "src", b))
               for b in ("tase2_server", "tase2_hmi_agent"))
    add("tools built", bins, "src/tase2_server" if bins else "run scripts/10_build.sh")
    cfg = os.path.join(R, d["config"])
    add("point model present", os.path.isfile(cfg), d["config"])
    tags = None
    if d.get("tags"):
        tags = os.path.join(R, d["tags"])
        add("tag database present", os.path.isfile(tags), d["tags"])
    if d.get("scenario"):
        scn = os.path.join(R, d["scenario"])
        add("scenario present", os.path.isfile(scn), d["scenario"])
    argv = ["python3", os.path.join(R, "scripts", "validate_config.py"), cfg]
    if tags:
        argv.append(tags)
    try:
        rc = subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        rc = 1
    add("configuration valid", rc == 0, "validate_config.py")
    if d.get("security") == "hardened":
        certs = all(os.path.isfile(os.path.join(R, "certs", f)) for f in
                    ("ca.crt", "server.crt", "server.key", "client.crt", "client.key"))
        add("TLS certificates present", certs, "certs/  (run scripts/gen_certs.sh)")
    add("documentation built", os.path.isdir(DOCS_HTML), "./scripts/65_build_docs.sh")

    warnings = []
    if d.get("security") == "insecure":
        warnings.append("INSECURE PROFILE: plaintext ICCP and an open command path. Use only on a closed range.")
    if d.get("mode") == "ingestion":
        warnings.append("INGESTION MODE reaches real devices per the tag database. Verify network segmentation before launch.")
    if d.get("dnp3_sim"):
        warnings.append("DNP3 simulator will bind TCP 20000 locally for the demo outstation.")
    return {"checks": checks, "warnings": warnings}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file("console.html", "text/html; charset=utf-8")
        if path == "/api/deployments":
            return self._json(200, tase2ctl.load_profiles())
        if path == "/api/status":
            return self._json(200, SUP.status())
        if path == "/api/logs":
            return self._json(200, {"lines": SUP.tail(300)})
        if path == "/api/runs":
            return self._json(200, SUP.runs)
        if path == "/api/precheck":
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            return self._json(200, precheck((q.get("name") or [""])[0]))
        if path == "/docs" or path.startswith("/docs/"):
            return self._serve_docs(path)
        if path.startswith("/static/"):
            return self._file(os.path.basename(path), "application/octet-stream")
        self._json(404, {"error": "not found"})

    def _serve_docs(self, path):
        """Serve the built documentation (docs/_build/html) so it is reachable
        from the console GUI. Build it with `make -C docs html`."""
        if not os.path.isdir(DOCS_HTML):
            return self._json(404, {"error": "docs not built; run: make -C docs html"})
        rel = path[len("/docs"):].lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(DOCS_HTML, rel))
        if not target.startswith(DOCS_HTML):           # no path traversal
            return self._json(404, {"error": "not found"})
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            return self._json(404, {"error": "not found"})
        ctype = DOC_TYPES.get(os.path.splitext(target)[1], "application/octet-stream")
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if path == "/api/start":
                SUP.start(body["name"])
            elif path == "/api/stop":
                SUP.stop()
            else:
                return self._json(404, {"error": "not found"})
        except (KeyError, ValueError, RuntimeError) as e:
            return self._json(400, {"error": str(e)})
        self._json(200, SUP.status())

    def _file(self, name, ctype):
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            return self._json(404, {"error": "not found"})
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite control console")
    ap.add_argument("--host", default=os.environ.get("CONSOLE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONSOLE_PORT", "8080")))
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[console] FreeTASE2 Suite console on http://%s:%d" % (args.host, args.port))
    print("[console] start a deployment, then open its SCADA HMI; Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[console] stopping; shutting down any running deployment")
        SUP.stop()


if __name__ == "__main__":
    main()
