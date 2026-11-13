#!/bin/sh
# VPN egress sidecar: brings the AmneziaWG tunnel up by hand, makes it this container's default route
# and runs tinyproxy, the HTTP CONNECT proxy the API's Anthropic client uses (LLM_PROXY_URL).
# No awg-quick: each step is a plain `ip` or `awg` command, so the container needs only NET_ADMIN and
# /dev/net/tun (no sysctls, no iptables), and the host's own routes never change.
set -eu

IFACE="${AWG_INTERFACE:-awg0}"
CONF="${AWG_CONFIG:-/run/secrets/awg0.conf}"

log() { echo "vpn-egress: $*" >&2; }
die() { log "$*"; exit 1; }

[ -r "$CONF" ] || die "no AmneziaWG config at $CONF (see 'VPN egress' in backend/README.md)"

# First value of a config key (case-insensitive), without CR and surrounding spaces.
value() {
    tr -d '\r' <"$CONF" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//Ip" | head -n 1 | sed 's/[[:space:]]*$//'
}
# IPv4 entries of a comma-separated key: "10.8.1.2/32, fd00::2/128" -> "10.8.1.2/32".
ipv4_list() {
    value "$1" | tr ',' '\n' | tr -d ' \t' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$' || true
}

ADDRESSES="$(ipv4_list Address)"
DNS_SERVERS="$(ipv4_list DNS)"
MTU="$(value MTU)"
ENDPOINT="$(value Endpoint)"
[ -n "$ADDRESSES" ] || die "no IPv4 Address in [Interface] of $CONF"
[ -n "$ENDPOINT" ] || die "no Endpoint in [Peer] of $CONF"

# 1. Where the VPS is and how this container reaches it now, before the default route changes.
HOST="${ENDPOINT%:*}"
case "$HOST" in
    *:* | \[*) die "IPv6 endpoints are not supported: $ENDPOINT" ;;
    *[!0-9.]*) SERVER="$(getent ahostsv4 "$HOST" | awk 'NR == 1 { print $1 }')" ;;
    *) SERVER="$HOST" ;;
esac
[ -n "$SERVER" ] || die "cannot resolve the VPN server $HOST"
GATEWAY="$(ip -4 route show default | awk 'NR == 1 { print $3 }')"
[ -n "$GATEWAY" ] || die "no default route to reach the VPN server"

# 2. The tunnel interface. amneziawg-go creates it in userspace (no kernel module on the host) and forks
# into the background. `awg setconf` loads the keys, the peer and the AmneziaWG obfuscation parameters
# (Jc, Jmin, Jmax, S1-S4, H1-H4, I1-I5); the wg-quick-only keys are stripped and applied with `ip` below.
ip link del "$IFACE" 2>/dev/null || true
amneziawg-go "$IFACE"
tr -d '\r' <"$CONF" |
    grep -viE '^[[:space:]]*(Address|DNS|MTU|Table|PreUp|PostUp|PreDown|PostDown|SaveConfig)[[:space:]]*=' |
    awg setconf "$IFACE" /dev/stdin
for address in $ADDRESSES; do
    ip address add "$address" dev "$IFACE"
done
ip link set dev "$IFACE" mtu "${MTU:-1280}" up

# 3. Routing. Encrypted packets to the VPS keep the old way out. Everything else this container sends
# goes into the tunnel, while the compose network keeps its own, more specific route on eth0, so the API
# can still reach the proxy. If the tunnel goes down, traffic stops instead of leaking around it.
ip route replace "$SERVER/32" via "$GATEWAY"
ip route replace default dev "$IFACE"
if ip -6 route show default 2>/dev/null | grep -q .; then
    ip -6 route replace unreachable default # no IPv6 path around the tunnel either
fi
# Name lookups use the config's DNS servers, which are reached through the tunnel as well.
if [ -n "$DNS_SERVERS" ]; then
    # shellcheck disable=SC2086 # one "nameserver" line per address
    printf 'nameserver %s\n' $DNS_SERVERS >/etc/resolv.conf
fi
log "tunnel $IFACE is up: VPN server $SERVER, default route through $IFACE"

# 4. The proxy the API connects to: HTTPS only, to the hosts in /etc/tinyproxy/allowed-hosts.
exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf
