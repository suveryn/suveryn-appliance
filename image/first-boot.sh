#!/bin/bash
# First start of a flashed appliance (suveryn-first-boot.service). Reads first-boot.env from the EFI
# partition, where the installer put it (FAT32: writable from any computer), runs
# suveryn-provision first-boot, and on success removes the settings and any private key from that
# partition, leaves the local CA's certificate there for the workstations, and shows the one-time
# Keycloak admin password on this machine's screen only (never in the journal).
set -uo pipefail
ESP=/boot/efi/suveryn
install -d -m 0700 /etc/suveryn/first-boot
install -m 0600 "$ESP/first-boot.env" /etc/suveryn/first-boot/first-boot.env
# Files the settings name on the EFI partition (TLS_CERT, TLS_KEY) are copied off it first.
for f in "$ESP"/*.pem "$ESP"/*.crt "$ESP"/*.key; do
  [ -e "$f" ] && install -m 0600 "$f" "/etc/suveryn/first-boot/$(basename "$f")"
done
sed -i "s#=$ESP/#=/etc/suveryn/first-boot/#" /etc/suveryn/first-boot/first-boot.env
# Model files on a USB disk labelled SUVERYN-MODELS (outbound-blocked or air-gapped installations).
if [ -e /dev/disk/by-label/SUVERYN-MODELS ]; then
  install -d /media/suveryn-models
  mount -o ro,nosuid,nodev,noexec /dev/disk/by-label/SUVERYN-MODELS /media/suveryn-models
fi

python3 /opt/suveryn/appliance/provision/suveryn-provision first-boot --config /etc/suveryn/first-boot/first-boot.env
status=$?
mountpoint -q /media/suveryn-models && umount /media/suveryn-models
if [ $status -eq 0 ]; then
  rm -f "$ESP/first-boot.env" "$ESP"/*.key "$ESP"/*.pem
  [ -f /etc/suveryn/tls/ca.crt ] && cp /etc/suveryn/tls/ca.crt "$ESP/suveryn-local-ca.crt"
  rm -rf /etc/suveryn/first-boot
  admin=$(sed -n 's/^KC_BOOTSTRAP_ADMIN_PASSWORD=//p' /etc/suveryn/keycloak.env)
  {
    echo
    echo "sūveryn is set up. Keycloak's admin console: <public URL>/idp/admin (from the office network)."
    echo "Temporary admin: suveryn-setup / $admin"
    echo "Sign in once, create the office's own administrator, then delete suveryn-setup."
    [ -f "$ESP/suveryn-local-ca.crt" ] && echo "Workstations: trust suveryn-local-ca.crt from the EFI partition."
    echo
  } > /dev/tty1
else
  echo "sūveryn first start failed (status $status): see 'journalctl -u suveryn-first-boot'. Fix first-boot.env and restart." > /dev/tty1
fi
exit $status
