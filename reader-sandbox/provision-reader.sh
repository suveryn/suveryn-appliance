#!/bin/sh
# Provision the sandboxed PDF reader (suveryn-tracker#15). Idempotent; run as root.
#
#   provision-reader.sh <gateway-user> <inbox> <reader-work-dir>
#
# - user and group suveryn-reader: no login, no home, no password;
# - the gateway user joins group suveryn-reader, so it can hand uploads to the reader;
# - <inbox>: owner the gateway, group suveryn-reader, 2750 (setgid: uploads inherit the group);
#   suveryn-core writes uploads there 0640 (SUVERYN_EXTRACT_INBOX);
# - <reader-work-dir>: owner suveryn-reader, 0700 (SUVERYN_EXTRACT_WORK_DIR);
# - python3-seccomp for the wrapper; Ghostscript and the other parsers come from the distribution
#   and are kept patched by its security updates (unattended-upgrades on the appliance).
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ $# = 3 ] || { echo "usage: $0 <gateway-user> <inbox> <reader-work-dir>" >&2; exit 1; }
GATEWAY=$1 INBOX=$2 WORK=$3
id "$GATEWAY" >/dev/null
getent group suveryn-reader >/dev/null || groupadd --system suveryn-reader
id suveryn-reader >/dev/null 2>&1 || useradd --system --gid suveryn-reader --no-create-home \
  --home-dir /nonexistent --shell /usr/sbin/nologin suveryn-reader
[ "$GATEWAY" = root ] || usermod -a -G suveryn-reader "$GATEWAY"
install -d -o "$GATEWAY" -g suveryn-reader -m 2750 "$INBOX"
install -d -o suveryn-reader -g suveryn-reader -m 0700 "$WORK"
dpkg -s python3-seccomp >/dev/null 2>&1 || apt-get install -y -qq python3-seccomp
echo "reader sandbox provisioned: inbox $INBOX, work $WORK"
