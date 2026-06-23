# Container image for the TASE.2 / ICCP testbed node.
#
# Builds libIEC61850 (pinned) and the tools, then runs the multi-station SCADA
# stack with the stub demo by default so the image is a ready "known target":
#
#   docker build -t tase2-plc-gateway .
#   docker run --rm -p 8800:8800 tase2-plc-gateway          # stub demo, HMI on :8800
#   docker run --rm -p 8800:8800 tase2-plc-gateway \
#       bash scripts/57_run_dnp3_demo.sh                    # DNP3 demo instead
#
# The HMI binds to 0.0.0.0 inside the container (HTTP_HOST) so the published port
# is reachable; the TASE.2 server and field side stay on loopback inside the
# container. Mount your own config/tags and set TAGS/SCADA_CONFIG for a real
# testbed, and review OT safety in the README before connecting live equipment.

FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake swig git curl ca-certificates \
        python3 python3-dev libmbedtls-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/tase2-plc-gateway
COPY . .

# Build libIEC61850 (pinned) + mbedtls + the tools. One time, cached in the layer.
RUN ./scripts/10_build.sh

ENV HTTP_HOST=0.0.0.0
EXPOSE 8800
EXPOSE 10502

CMD ["bash", "scripts/55_run_scada.sh"]
