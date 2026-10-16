#!/bin/sh
# Who on the LAN may reach Hearth's published ports on the Pi.
#
# ufw cannot do this. Docker publishes a port by DNAT in the nat table and
# forwards the packet to the container through the FORWARD chain, and ufw's
# INPUT rules never see it. Docker leaves one chain for its owner,
# DOCKER-USER, which every forwarded packet passes before Docker's own rules.
# This rewrites it, whole, every time it runs:
#
# - replies to connections already allowed go through;
# - new connections arriving on the LAN interface are allowed to 443 and 80
#   from the home subnet, and to Postgres from the desktop alone;
# - every other new connection arriving on the LAN interface is dropped.
#
# Traffic the containers start themselves — Hearth to LM Studio, SearXNG to
# its engines — leaves by the LAN interface rather than arriving on it, and is
# not touched. Ports are matched as published (conntrack's original
# destination), not as the container port they are rewritten to.
#
# pg_hba.conf is the second, independent lock on Postgres. Run as root, before
# Docker starts (hearth-firewall.service). Settings: /etc/default/hearth-firewall.

set -eu

: "${HEARTH_CLIENT_IPS:?must name the LAN address of the desktop, as in the .env of the project}"
: "${LAN_SUBNET:?the home network, e.g. 192.168.1.0/24}"
LAN_IF="${LAN_IF:-eth0}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

# Whitespace alone passes the check above and would admit nobody to Postgres —
# closed, but silently. Say so instead.
case "$HEARTH_CLIENT_IPS" in
    *[!\ ]*) ;;
    *) echo "HEARTH_CLIENT_IPS holds no address" >&2; exit 1 ;;
esac

chain() {
    # Docker creates DOCKER-USER when it starts, and keeps one that exists.
    "$1" -N DOCKER-USER 2>/dev/null || true
    "$1" -F DOCKER-USER
    "$1" -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
}

new_from_lan() {
    # A new TCP connection arriving on the LAN interface for a published port.
    echo "-i $LAN_IF -p tcp -m conntrack --ctstate NEW --ctdir ORIGINAL --ctorigdstport $1"
}

chain iptables
for port in 443 80; do
    # shellcheck disable=SC2046 # the rule's words are meant to split
    iptables -A DOCKER-USER $(new_from_lan "$port") -s "$LAN_SUBNET" -j RETURN
done
for address in $HEARTH_CLIENT_IPS; do
    # shellcheck disable=SC2046
    iptables -A DOCKER-USER $(new_from_lan "$POSTGRES_PORT") -s "$address" -j RETURN
done
iptables -A DOCKER-USER -i "$LAN_IF" -m conntrack --ctstate NEW -j DROP
iptables -A DOCKER-USER -j RETURN

# The compose file publishes on IPv4 only. Should anything ever publish on
# IPv6, it is closed to the LAN until this script says otherwise.
if command -v ip6tables >/dev/null 2>&1; then
    chain ip6tables
    ip6tables -A DOCKER-USER -i "$LAN_IF" -m conntrack --ctstate NEW -j DROP
    ip6tables -A DOCKER-USER -j RETURN
fi

echo "DOCKER-USER: 443 and 80 from $LAN_SUBNET, $POSTGRES_PORT from $HEARTH_CLIENT_IPS, other new connections on $LAN_IF dropped"
