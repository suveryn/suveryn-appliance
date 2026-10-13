#!/bin/bash
# Hardening, run as root inside the image (Packer). The services themselves already listen on
# loopback only (PostgreSQL, Keycloak, llama-server, the gateway); nginx is the only door.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== Fewer packages and services"
apt-get purge -y -qq snapd modemmanager open-iscsi multipath-tools apport whoopsie popularity-contest \
  ubuntu-advantage-tools ubuntu-pro-client 2>/dev/null >/dev/null || true
systemctl disable --now motd-news.timer 2>/dev/null || true
# nginx starts at first boot, with the office's site; until then nothing answers on 80/443.
rm -f /etc/nginx/sites-enabled/default
systemctl disable nginx >/dev/null 2>&1 || true

echo "== Network: DHCP on every Ethernet port, whatever the machine names it"
# The installer wrote the build VM's interface (by name/MAC); a flashed server has other ones.
rm -f /etc/netplan/*.yaml
cat > /etc/netplan/01-suveryn.yaml <<'NETPLAN'
# sūveryn appliance: DHCP (IPv4 and IPv6) on every Ethernet port. A fixed address is set by the
# office's DHCP server (a reservation), so the image needs no per-site network settings.
network:
  version: 2
  renderer: networkd
  ethernets:
    all-ethernet:
      match:
        name: "en*"
      dhcp4: true
      dhcp6: true
      optional: true
NETPLAN
chmod 600 /etc/netplan/01-suveryn.yaml

echo "== Firewall (ufw): nothing in until first boot sets the office's rules; outgoing per network mode from first boot (suveryn-egress)"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default deny routed >/dev/null
ufw default allow outgoing >/dev/null
ufw --force enable >/dev/null

echo "== Kernel settings"
cat > /etc/sysctl.d/90-suveryn.conf <<'CONF'
# sūveryn appliance hardening (image/scripts/harden.sh)
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
kernel.kexec_load_disabled = 1
kernel.yama.ptrace_scope = 2
fs.suid_dumpable = 0
fs.protected_fifos = 2
fs.protected_regular = 2
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.tcp_syncookies = 1
CONF

echo "== No core dumps (they would hold document text); a bounded journal"
install -d /etc/systemd/coredump.conf.d /etc/systemd/journald.conf.d /etc/security/limits.d
printf '[Coredump]\nStorage=none\nProcessSizeMax=0\n' > /etc/systemd/coredump.conf.d/90-suveryn.conf
printf '* hard core 0\n' > /etc/security/limits.d/90-suveryn.conf
printf '[Journal]\nStorage=persistent\nSystemMaxUse=1G\n' > /etc/systemd/journald.conf.d/90-suveryn.conf

echo "== Logins: none by default"
passwd -l root >/dev/null
cat > /etc/issue <<'ISSUE'
sūveryn appliance (\n)

This machine has no console login. It sets itself up at its first start from
first-boot.env on its EFI partition (suveryn/first-boot.env); progress and the
result are shown here. See suveryn-appliance image/README.md.

ISSUE
