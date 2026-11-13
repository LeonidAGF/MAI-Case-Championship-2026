# VPN egress for the chat: a userspace AmneziaWG client and an HTTP CONNECT proxy (tinyproxy).
# The API sends only its Anthropic requests here (LLM_PROXY_URL=http://vpn-egress:8888). User traffic
# and every other outbound request stay direct. Started by the "vpn" profile of docker-compose.yml.
ARG AWG_GO_VERSION=v3.1.20260828
ARG AWG_TOOLS_VERSION=v3.1.20260812

# amneziawg-go: the AmneziaWG tunnel in userspace, so the host needs no kernel module.
FROM golang:1.25-bookworm AS awg-go
ARG AWG_GO_VERSION
ENV CGO_ENABLED=0
RUN git clone --depth 1 --branch "${AWG_GO_VERSION}" https://github.com/amnezia-vpn/amneziawg-go /src \
    && make -C /src \
    && install -D -m 0755 /src/amneziawg-go /out/amneziawg-go

# awg: loads keys, the peer and the obfuscation parameters into the tunnel through its UAPI socket.
FROM debian:trixie-slim AS awg-tools
ARG AWG_TOOLS_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git gcc make libc6-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${AWG_TOOLS_VERSION}" https://github.com/amnezia-vpn/amneziawg-tools /src \
    && make -C /src/src \
    && install -D -m 0755 /src/src/wg /out/awg

FROM debian:trixie-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 tinyproxy curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=awg-go /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools /out/awg /usr/local/bin/awg
COPY tinyproxy.conf allowed-hosts /etc/tinyproxy/
COPY entrypoint.sh /usr/local/bin/vpn-egress
# A checkout or an editor on Windows can add CRLF, which breaks the script and the filter rules.
RUN sed -i 's/\r$//' /usr/local/bin/vpn-egress /etc/tinyproxy/tinyproxy.conf /etc/tinyproxy/allowed-hosts \
    && chmod 0755 /usr/local/bin/vpn-egress
EXPOSE 8888
ENTRYPOINT ["/usr/local/bin/vpn-egress"]
