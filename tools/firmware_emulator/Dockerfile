# Vendor Venus E 3.0 / A / D Control firmware in Renode, serving the Local API
# on UDP 30000 of the container's own IP. Build from the repository root:
#   docker build -f tools/firmware_emulator/Dockerfile -t marstek-fw-emulator .
# See tools/firmware_emulator/README.md ("Docker").

# ---- Renode: pinned stable portable release, checksum-verified -------------
# https://github.com/renode/renode/releases/tag/v1.17.0 lists the SHA256.
FROM debian:bookworm-slim AS renode
ARG RENODE_VERSION=1.17.0
ARG RENODE_SHA256=4ba7c68b59e2447f188ef4b4b112fcccd2802582c460beedceb63b84e5605a5f
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# Optional build secret "build_ca": extra CA bundle for a TLS-intercepting proxy.
# Unset (or empty), curl uses the system store.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export CURL_CA_BUNDLE=/run/secrets/build_ca; fi \
    && curl -fsSL -o /tmp/renode.tar.gz \
        "https://github.com/renode/renode/releases/download/v${RENODE_VERSION}/renode-${RENODE_VERSION}.linux-portable.tar.gz" \
    && echo "${RENODE_SHA256}  /tmp/renode.tar.gz" | sha256sum -c - \
    && mkdir /opt/renode \
    && tar xzf /tmp/renode.tar.gz -C /opt/renode --strip-components=1 \
    && rm /tmp/renode.tar.gz

# ---- Runtime --------------------------------------------------------------
FROM debian:bookworm-slim
# The portable build bundles .NET 8 (invariant globalization) and needs no GTK
# with --disable-gui. The emulator scripts use only the standard library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=renode /opt/renode /opt/renode
ENV RENODE=/opt/renode/renode \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Same layout as the repository, so run_firmware.py finds the catalog and the
# committed blobs relative to itself. No firmware is downloaded at runtime.
COPY tools/firmware/catalog.json /app/tools/firmware/catalog.json
COPY tools/firmware/blobs /app/tools/firmware/blobs
COPY tools/firmware_emulator /app/tools/firmware_emulator

# EEPROM (the firmware's persistent settings) lives here; mount a volume on it.
VOLUME ["/state"]

HEALTHCHECK --interval=60s --timeout=30s --start-period=240s --start-interval=10s --retries=3 \
    CMD ["python3", "/app/tools/firmware_emulator/healthcheck.py"]

# Pass --firmware DEVICE:VERSION, --ble-mac, --wifi-mac and friends as the command.
ENTRYPOINT ["python3", "/app/tools/firmware_emulator/run_firmware.py", \
            "--ip", "auto", "--work", "/tmp/marstek-fw", "--eeprom", "/state/eeprom.bin", \
            "--ct-loopback", "--quantum", "0.01", "--log-max-mb", "20"]
CMD ["--firmware", "VNSE3-0:150"]
