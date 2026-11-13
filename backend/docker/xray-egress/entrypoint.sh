#!/bin/sh
# Xray egress sidecar: turns the client config of an Xray VPN server into an Xray config whose only
# inbound is the HTTP CONNECT proxy the API's Anthropic client uses (LLM_PROXY_URL), then runs Xray.
# HTTPS to ALLOWED_HOSTS goes into the tunnel; every other request is refused, never sent around it.
set -eu

CONF="${XRAY_CLIENT_CONFIG:-/run/secrets/xray-client}"
WORK=/tmp/xray-egress
PORT=8888
# The chat's API, and the address check of `python -m app.cli egress-check` and of the healthcheck.
ALLOWED_HOSTS="api.anthropic.com www.cloudflare.com"

log() { echo "xray-egress: $*" >&2; }
die() { log "$*"; exit 1; }

[ -r "$CONF" ] || die "cannot read the Xray client config at $CONF (see 'Xray' in backend/README.md)"
umask 077 # the files below hold the server's keys
mkdir -p "$WORK"

# 1. The client config as JSON. An Amnezia "vpn://" key is base64url of Qt's qCompress output: the
# length as 4 bytes, then the JSON as a zlib stream. A JSON file is used as it is.
case "$(tr -d ' \t\r\n' <"$CONF" | head -c 6)" in
    vpn://)
        tr -d ' \t\r\n' <"$CONF" | cut -c 7- | tr '_-' '/+' >"$WORK/key.b64"
        case $(($(wc -c <"$WORK/key.b64") % 4)) in
            2) printf '==' >>"$WORK/key.b64" ;;
            3) printf '=' >>"$WORK/key.b64" ;;
        esac
        base64 -d "$WORK/key.b64" >"$WORK/key.bin" 2>/dev/null || die "$CONF: the vpn:// key is not valid base64"
        tail -c +5 "$WORK/key.bin" | pigz -dz >"$WORK/client.json" 2>/dev/null ||
            die "$CONF: cannot unpack the vpn:// key"
        ;;
    '{'*) cp "$CONF" "$WORK/client.json" ;;
    *) die "$CONF is neither an Amnezia vpn:// key nor an Xray JSON config" ;;
esac

# 2. The tunnel: the Xray server of an Amnezia key, or the first VLESS, VMess, Trojan or Shadowsocks
# outbound of an Xray config. Its streamSettings (REALITY, XHTTP, ...) are kept as they are.
jq -ce '
    if type == "object" and has("containers") then
        [.containers[]?.xray.last_config? // empty]
        | (first // error("no Xray server in the Amnezia key (it holds other protocols only)"))
        | if type == "string" then fromjson else . end
    else . end
    | [.outbounds[]? | select(.protocol | IN("vless", "vmess", "trojan", "shadowsocks"))]
    | (first // error("no VLESS, VMess, Trojan or Shadowsocks outbound in the config"))
    | .tag = "tunnel"
' "$WORK/client.json" >"$WORK/outbound.json" || die "$CONF: no usable Xray server in it"
SUMMARY="$(jq -r '
    (.settings.vnext[0] // .settings.servers[0] // .settings) as $server
    | "\(.protocol) to \($server.address):\($server.port) over \(.streamSettings.network // "raw")"
      + " with \(.streamSettings.security // "no encryption layer")"
' "$WORK/outbound.json")"

# 3. The Xray config: the HTTP proxy for the API, the tunnel, and a blackhole for everything else.
jq -n --slurpfile tunnel "$WORK/outbound.json" --arg hosts "$ALLOWED_HOSTS" \
    --arg level "${LOG_LEVEL:-warning}" --argjson port "$PORT" '{
    log: {loglevel: $level},
    inbounds: [{tag: "api", listen: "0.0.0.0", port: $port, protocol: "http", settings: {}}],
    outbounds: [$tunnel[0], {tag: "refused", protocol: "blackhole"}],
    routing: {domainStrategy: "AsIs", rules: [
        {inboundTag: ["api"], domain: ($hosts | split(" ") | map("full:" + .)), port: "443", outboundTag: "tunnel"},
        {inboundTag: ["api"], outboundTag: "refused"}
    ]}
}' >"$WORK/xray.json"
rm -f "$WORK/key.b64" "$WORK/key.bin" "$WORK/client.json" "$WORK/outbound.json"

log "tunnel: $SUMMARY; HTTP proxy on :$PORT for $ALLOWED_HOSTS only"
exec xray run -c "$WORK/xray.json"
