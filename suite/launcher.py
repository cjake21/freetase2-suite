#!/usr/bin/env python3
"""
tase2-suite: the single entry point. One command (or one click) brings up the
whole tool. It checks the native build, starts the control console, and opens it in
your browser. From the console you pick a deployment, press Start, and open its
SCADA HMI; the data and detection tools run from a terminal as the console shows.

  tase2-suite                  build check, start the console, open the browser
  tase2-suite --build          build the native tools first if they are missing
  tase2-suite --no-browser     start the console without opening a browser
  tase2-suite --host 0.0.0.0   bind all interfaces (containers); pairs with --no-browser

Standard library only.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def tools_built():
    return all(os.path.isfile(os.path.join(ROOT, "src", b))
               for b in ("tase2_server", "tase2_hmi_agent"))


def build():
    print("[tase2-suite] building the native tools (libIEC61850 + the suite); this runs once")
    rc = subprocess.call(["bash", os.path.join(ROOT, "scripts", "10_build.sh")])
    if rc != 0:
        sys.exit("[tase2-suite] build failed; see the output above")


def main():
    ap = argparse.ArgumentParser(description="FreeTASE2 Suite launcher")
    ap.add_argument("--host", default=os.environ.get("CONSOLE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONSOLE_PORT", "8080")))
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ap.add_argument("--build", action="store_true", help="build the native tools if missing")
    args = ap.parse_args()

    if not tools_built():
        if args.build:
            build()
        else:
            print("[tase2-suite] the native tools are not built yet.")
            print("[tase2-suite] run:  ./tase2-suite --build    (or)  ./scripts/10_build.sh")
            sys.exit(1)

    sys.path.insert(0, HERE)
    import console  # noqa: E402  (the control console handler + supervisor)

    httpd = ThreadingHTTPServer((args.host, args.port), console.Handler)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = "http://%s:%d/" % (shown, args.port)
    print("[tase2-suite] control console on %s" % url)
    print("[tase2-suite] pick a deployment and press Start; Ctrl+C to stop")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[tase2-suite] stopping; shutting down any running deployment")
        console.SUP.stop()


if __name__ == "__main__":
    main()
