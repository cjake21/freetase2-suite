# Container image for the FreeTASE2 Suite: the whole tool in a box.
#
# It builds libIEC61850 (pinned) and the native tools, then runs the control
# console by default, so the container is the application:
#
#   docker build -t freetase2-suite .
#   docker run --rm -p 8080:8080 -p 8800:8800 freetase2-suite
#
# Open http://127.0.0.1:8080 for the control console, pick a deployment, press
# Start, then open its SCADA HMI (published on 8800). The console binds all
# interfaces inside the container (--host 0.0.0.0) so the published port is
# reachable; the TASE.2 server and field side stay on loopback inside the
# container. Mount your own config/tags for a real testbed, and review the OT
# safety guidance in the README before connecting live equipment.

FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake swig git curl ca-certificates \
        python3 python3-dev libmbedtls-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/freetase2-suite
COPY . .

# Build libIEC61850 (pinned) + mbedtls + the tools. One time, cached in the layer.
RUN ./scripts/10_build.sh

ENV HTTP_HOST=0.0.0.0
EXPOSE 8080
EXPOSE 8800
EXPOSE 102

# The container is the app: the control console, reachable on the published port.
CMD ["python3", "suite/launcher.py", "--no-browser", "--host", "0.0.0.0"]
