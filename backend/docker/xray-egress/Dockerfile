# VPN egress for the chat over Xray: an Xray client whose HTTP proxy the API's Anthropic client uses
# (LLM_PROXY_URL=http://xray-egress:8888). It reads the client config of an Xray server, an Amnezia
# "vpn://" key or an Xray JSON config: VLESS, VMess, Trojan or Shadowsocks over RAW, XHTTP, gRPC, ...
# with REALITY or TLS. Started by the "xray" profile of docker-compose.yml. Unlike vpn-egress (AmneziaWG)
# it needs no TUN device and no capabilities: Xray is the proxy itself, and the container's routes stay.
ARG XRAY_VERSION=v26.10.21
# SHA-256 of the release archives (the .dgst files on the release page): a changed archive fails the build.
ARG XRAY_SHA256_AMD64=0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978
ARG XRAY_SHA256_ARM64=d1304946c486d98e6b886e9b931eaaaebb23835b30e217638037b7f2721d4e9c

# The official Xray-core release binary for the target platform, checked against its SHA-256.
FROM debian:trixie-slim AS xray
ARG XRAY_VERSION
ARG XRAY_SHA256_AMD64
ARG XRAY_SHA256_ARM64
ARG TARGETARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH:-amd64}" in \
        amd64) asset=Xray-linux-64.zip sum="$XRAY_SHA256_AMD64" ;; \
        arm64) asset=Xray-linux-arm64-v8a.zip sum="$XRAY_SHA256_ARM64" ;; \
        *) echo "no Xray build pinned for $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${asset}" \
    && echo "$sum  /tmp/xray.zip" | sha256sum -c - \
    && unzip -p /tmp/xray.zip xray >/xray \
    && chmod 0755 /xray

FROM debian:trixie-slim
# jq builds the Xray config, pigz unpacks an Amnezia key (zlib), curl runs the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq pigz curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=xray /xray /usr/local/bin/xray
COPY entrypoint.sh /usr/local/bin/xray-egress
# A checkout or an editor on Windows can add CRLF, which breaks the script.
RUN sed -i 's/\r$//' /usr/local/bin/xray-egress && chmod 0755 /usr/local/bin/xray-egress
EXPOSE 8888
ENTRYPOINT ["/usr/local/bin/xray-egress"]
