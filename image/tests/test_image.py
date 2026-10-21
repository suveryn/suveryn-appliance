"""The image's build files and first-boot rules: what is easy to break and expensive to find on a
customer's server. Any machine:

    python3 -m pytest image/tests

The image itself is checked by booting it (README.md, "Testing").
"""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
TEMPLATE = (HERE / "suveryn.pkr.hcl").read_text()
USER_DATA = (HERE / "http" / "user-data.pkrtpl").read_text()
loader = importlib.machinery.SourceFileLoader("provision", str(HERE.parent / "provision" / "suveryn-provision"))
spec = importlib.util.spec_from_loader("provision", loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


def test_a_bare_metal_disk_that_boots_on_any_uefi_machine():
    assert 'format             = "raw"' in TEMPLATE and "efi_boot           = true" in TEMPLATE
    assert "grub-install --target=x86_64-efi --efi-directory=/boot/efi --removable --no-nvram" in USER_DATA
    build = (HERE / "scripts" / "build.sh").read_text()
    assert "linux-generic linux-firmware" in build  # real hardware's drivers, not -virtual
    assert re.search(r"linux-modules-nvidia-\$\{NVIDIA_BRANCH\}-open-generic", build)  # signed: Secure Boot works


def test_nothing_unique_to_a_box_is_built_in():
    """Every office must get its own secrets, certificate and identity at first boot."""
    image_steps = re.search(r"def image\([^)]*\) -> None:(.*?)\n\n\ndef ", (HERE.parent / "provision" / "suveryn-provision").read_text(), re.S).group(1)
    for first_boot_only in ("postgres(", "keycloak(", "tls(", "reverse_proxy(", "configure(", "llm_models(", "network_mode("):
        assert first_boot_only not in image_steps, first_boot_only
    cleanup = (HERE / "scripts" / "cleanup.sh").read_text()
    assert "truncate -s 0 /etc/machine-id" in cleanup and "cloud-init clean" in cleanup
    assert "userdel -f -r suveryn-build" in TEMPLATE and "rm -f /etc/ssh/ssh_host_*" in TEMPLATE
    assert "purge -y -qq openssh-server" in TEMPLATE
    assert "build_password" not in USER_DATA.replace("password_hash", "")  # only its bcrypt hash, during the build


def test_pins():
    assert 'required_version = "= 1.16.1"' in TEMPLATE and 'version = "= 1.1.6"' in TEMPLATE
    assert re.search(r'iso_checksum = "sha256:[0-9a-f]{64}"', TEMPLATE)
    assert re.search(r"CUDA_KEYRING_SHA256=[0-9a-f]{64}", (HERE / "scripts" / "build.sh").read_text())


def test_firewall_lets_in_only_the_web():
    """ufw is for what comes in; what goes out is the network mode's own table (network-mode/tests)."""
    rules = [" ".join(r) for r in p.firewall_rules()]
    assert rules[:3] == ["--force reset", "default deny incoming", "default deny routed"]
    assert [r for r in rules if r.startswith("allow in")] == ["allow in 80/tcp", "allow in 443/tcp"]
    assert "default allow outgoing" in rules and not [r for r in rules if r.startswith("allow out")]
    assert rules[-1] == "--force enable"


def test_the_network_modes_units_load_the_rules_before_the_network():
    egress = (HERE.parent / "provision" / "systemd" / "suveryn-egress.service").read_text()
    assert "Before=network-pre.target" in egress and "WantedBy=sysinit.target" in egress
    assert "ExecStart=/opt/suveryn/bin/suveryn-network-mode apply" in egress
    dns = (HERE.parent / "provision" / "systemd" / "suveryn-dns.service").read_text()
    assert "Requires=suveryn-egress.service" in dns and "--conf-file=/etc/suveryn/dnsmasq.conf" in dns
    assert p.EGRESS_UNITS == ["suveryn-egress", "suveryn-dns"]
    assert "nameserver 127.0.0.1" in p.RESOLV_TEXT


def test_first_boot_settings_are_the_known_ones_only(tmp_path):
    cfg = tmp_path / "first-boot.env"
    cfg.write_text("PUBLIC_URL=https://suveryn.office.example\nNETWORK_MODE=connected\nOUTBOUND=approved\n"
                   "SITE=on-premise\nMODELS_FROM=/media/suveryn-models\n")
    assert p.first_boot_config(cfg)["NETWORK_MODE"] == "connected"
    assert p.first_boot_config(cfg)["OUTBOUND"] == "approved"
    cfg.write_text("PUBLIC_URL=https://x.example\nSUVERYN_EXTRACT_UNSANDBOXED=1\n")
    with pytest.raises(SystemExit):
        p.first_boot_config(cfg)  # a typo or a smuggled setting stops the first boot


def test_first_boot_runs_once_and_never_prints_the_admin_password_to_the_journal():
    unit = (HERE / "systemd" / "suveryn-first-boot.service").read_text()
    assert "ConditionPathExists=!/var/lib/suveryn/.first-boot-done" in unit
    script = (HERE / "first-boot.sh").read_text()
    shown = re.search(r"\{\n(.*?)\n  \} > /dev/tty1", script, re.S).group(1)
    assert "$admin" in shown  # on the machine's own screen
    assert script.count("$admin") == 1  # and nowhere else (stdout goes to the journal)
    assert 'rm -f "$ESP/first-boot.env" "$ESP"/*.key "$ESP"/*.pem' in script  # no private key left on the FAT partition


def test_the_network_comes_up_on_any_machine():
    """The installer configures the build VM's interface; a flashed server's ports have other names."""
    harden = (HERE / "scripts" / "harden.sh").read_text()
    assert "rm -f /etc/netplan/*.yaml" in harden and 'name: "en*"' in harden and "dhcp4: true" in harden


def test_the_build_machines_journal_is_not_left_behind():
    """Found in the boot test: a journal folder named after the build machine's machine-id."""
    assert "rm -rf /var/log/journal/*" in (HERE / "scripts" / "cleanup.sh").read_text()
