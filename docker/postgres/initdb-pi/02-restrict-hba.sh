#!/bin/sh
# Only the desktop and Hearth's own container may connect.
# docker-compose.pi.yml sets POSTGRES_HOST_AUTH_METHOD=reject, so the image's
# entrypoint has just written `host all all all reject` — every TCP connection
# refused. This puts scram-sha-256 lines ahead of it, which pg_hba.conf reads
# top down, first match winning:
# - one per address in HEARTH_CLIENT_IPS (space-separated): the desktop, for
#   `make eval` and `make backup` run there;
# - HEARTH_APP_IP: the hearth container's fixed address in the project's
#   network. One address, never the subnet: the subnet holds the gateway, and a
#   published port can deliver any LAN client from the gateway's address.
# Local socket connections, which the healthcheck uses, are untouched.
#
# Runs once, on an empty data folder. If the desktop's address changes, edit
# pg_hba.conf in PI_PGDATA_DIR and restart the container — and give the
# desktop a DHCP reservation so it does not.
#
# A failing init script does not stop the entrypoint — measured, 2026-10-14 —
# so this script is not what keeps the cluster closed. `reject` is. If this
# fails, the cluster comes up refusing everyone, and the log says why.

: "${HEARTH_CLIENT_IPS:?HEARTH_CLIENT_IPS must name the LAN address of the desktop}"

# Whitespace alone passes the check above and would allow nobody — closed, but
# with a log line claiming otherwise.
case "$HEARTH_CLIENT_IPS" in
    *[!\ ]*) ;;
    *) echo "HEARTH_CLIENT_IPS holds no address; TCP stays closed" >&2; exit 1 ;;
esac

hba="$PGDATA/pg_hba.conf"
grep -v '^host all all all ' "$hba" > "$hba.new"
for address in $HEARTH_CLIENT_IPS; do
    printf 'host all all %s/32 scram-sha-256\n' "$address" >> "$hba.new"
done
if [ -n "${HEARTH_APP_IP:-}" ]; then
    printf 'host all all %s/32 scram-sha-256\n' "$HEARTH_APP_IP" >> "$hba.new"
fi
printf 'host all all all reject\n' >> "$hba.new"
mv "$hba.new" "$hba"
echo "pg_hba.conf: TCP connections restricted to $HEARTH_CLIENT_IPS ${HEARTH_APP_IP:-}"
