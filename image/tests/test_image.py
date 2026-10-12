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
    image_steps = re.search(r"def image\(\) -> None:(.*?)\n\n\ndef ", (HERE.parent / "provision" / "suveryn-provision").read_text(), re.S).group(1)
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


@pytest.mark.parametrize("mode", ["connected", "outbound-blocked", "air-gapped"])
def test_firewall_lets_in_only_the_web_and_keeps_offline_modes_offline(mode):
    rules = [" ".join(r) for r in p.firewall_rules(mode)]
    assert rules[:3] == ["--force reset", "default deny incoming", "default deny routed"]
    assert [r for r in rules if r.startswith("allow in")] == ["allow in 80/tcp", "allow in 443/tcp"]
    if mode == "connected":
        assert "default allow outgoing" in rules
    else:
        assert "default deny outgoing" in rules and "default allow outgoing" not in rules
        assert all(r.split()[-1] in p.PRIVATE_NETS for r in rules if r.startswith("allow out"))
    assert rules[-1] == "--force enable"


def test_first_boot_settings_are_the_known_ones_only(tmp_path):
    cfg = tmp_path / "first-boot.env"
    cfg.write_text("PUBLIC_URL=https://suveryn.office.example\nNETWORK_MODE=air-gapped\nSITE=on-premise\n"
                   "MODELS_FROM=/media/suveryn-models\n")
    assert p.first_boot_config(cfg)["NETWORK_MODE"] == "air-gapped"
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
