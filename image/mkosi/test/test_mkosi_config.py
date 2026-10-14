"""The read-only system's build configuration against the design (docs/design/appliance-architecture.md).

Any machine, no build needed:

    python3 -m pytest image/mkosi/test

The built system itself is checked in a virtual machine by run-vm-tests.
"""

import configparser
import re
from pathlib import Path

import pytest

MKOSI = Path(__file__).resolve().parent.parent
REPO = MKOSI.parent.parent
DESIGN = (REPO / "docs" / "design" / "appliance-architecture.md").read_text()
POSTINST = (MKOSI / "mkosi.postinst.chroot").read_text()
FSTAB = [line.split() for line in (MKOSI / "mkosi.extra" / "etc" / "fstab").read_text().splitlines()
         if line.strip() and not line.startswith("#")]


def partitions(folder: Path) -> list[dict[str, str]]:
    out = []
    for f in sorted(folder.glob("*.conf")):
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        cp.read_string(f.read_text())
        out.append({"file": f.name, **dict(cp["Partition"])})
    return out


def size(value: str) -> int:
    n, unit = re.fullmatch(r"(\d+)([MG])", value).groups()
    return int(n) * (2**20 if unit == "M" else 2**30)


def test_a_release_brings_the_efi_partition_and_slot_a_with_its_verity():
    for folder in (MKOSI / "mkosi.repart", MKOSI / "mkosi.profiles" / "test" / "mkosi.repart"):
        parts = partitions(folder)
        assert [p["Type"] for p in parts] == ["esp", "root", "root-verity"], folder
        esp, root, verity = parts
        assert size(esp["SizeMinBytes"]) >= 512 * 2**20  # smaller FAT32 has too few clusters for UEFI
        assert root["Format"] == "erofs" and root["Verity"] == "data" and verity["Verity"] == "hash"
        assert root["VerityMatchKey"] == verity["VerityMatchKey"]
        assert root["CopyFiles"] == "/"


def test_the_appliance_slot_is_the_designs_32_gib():
    root = partitions(MKOSI / "mkosi.repart")[1]
    assert root["SizeMinBytes"] == root["SizeMaxBytes"] == "32G"
    assert "32 GiB" in DESIGN


@pytest.mark.parametrize("profile", ["appliance", "test"])
def test_the_whole_disk_is_the_designs_layout(profile):
    """ESP, A, A-verity (matched, left as they are), then B, B-verity (empty) and the data partition."""
    parts = partitions(MKOSI / "target" / profile)
    assert [p["Type"] for p in parts] == ["esp", "root", "root-verity", "root", "root-verity", "linux-generic"]
    b, b_verity, data = parts[3:]
    assert b["Label"] == b_verity["Label"] == "_empty"
    assert data["Label"] == "suveryn-data" and data["Format"] == "ext4"
    assert "SizeMaxBytes" not in data  # the rest of the disk
    assert data["CopyFiles"] == "/usr/lib/suveryn/empty-machine-id:/lib/suveryn/host/machine-id"
    # The matched partitions say nothing that could change what the release brought.
    for p in parts[:3]:
        assert set(p) == {"file", "Type"}, p


def test_appliance_and_test_differ_only_in_sizes():
    strip = lambda ps: [{k: v for k, v in p.items() if not k.startswith("Size")} for p in ps]  # noqa: E731
    assert strip(partitions(MKOSI / "target" / "appliance")) == strip(partitions(MKOSI / "target" / "test"))


def test_both_slots_verity_fits_a_32_gib_slot():
    """SHA-256 per 4 KiB block: 256 MiB of leaf hashes for 32 GiB, plus the tree above them."""
    leaves = 32 * 2**30 // 4096 * 32
    a_verity = partitions(MKOSI / "mkosi.repart")[2]
    b_verity = partitions(MKOSI / "target" / "appliance")[4]
    for v in (a_verity, b_verity):
        assert size(v["SizeMinBytes"]) >= leaves * 129 // 128
    assert a_verity["SizeMinBytes"] == b_verity["SizeMinBytes"]  # an update writes either slot


def test_var_is_the_data_partition_and_nothing_on_it_runs():
    var = next(e for e in FSTAB if e[1] == "/var")
    assert var[0] == "PARTLABEL=suveryn-data"
    assert set(var[3].split(",")) >= {"noexec", "nosuid", "nodev", "x-initrd.mount"}


def test_tmp_in_memory_and_no_swap():
    assert next(e for e in FSTAB if e[1] == "/tmp")[2] == "tmpfs"
    assert not [e for e in FSTAB if "swap" in e]


def test_the_machine_id_is_mounted_from_the_data_partition_in_the_initrd():
    """systemd won't follow a link for /etc/machine-id; a bind mount set up in the initrd works."""
    mid = next(e for e in FSTAB if e[1] == "/etc/machine-id")
    assert mid[0] == "/var/lib/suveryn/host/machine-id" and set(mid[3].split(",")) >= {"bind", "x-initrd.mount"}
    assert ": > /etc/machine-id" in POSTINST and "link /var/lib/suveryn/host/machine-id" not in POSTINST


def test_only_the_listed_etc_paths_link_into_the_data_partition():
    links = dict(re.findall(r"^link (\S+)\s+(\S+)$", POSTINST, re.M))
    into_var = {target: dest for target, dest in links.items() if target.startswith("/var/")}
    assert set(into_var.values()) == {"/etc/suveryn", "/etc/hostname", "/etc/hosts", "/etc/systemd/network"}
    for path in into_var.values():
        assert f"`{path}" in DESIGN, f"{path} isn't in the design note's list"
    assert links["/run/systemd/resolve/stub-resolv.conf"] == "/etc/resolv.conf"


def test_the_images_own_var_is_restored_on_a_fresh_data_partition():
    assert "cp -a /var /usr/share/factory/var" in POSTINST and '"C ${d#/usr/share/factory} - - - -"' in POSTINST


def test_packages_come_over_https_only():
    sources = (MKOSI / "mkosi.sandbox" / "etc" / "apt" / "sources.list.d" / "mkosi.sources").read_text()
    uris = re.findall(r"^URIs: (\S+)", sources, re.M)
    assert uris and all(u.startswith("https://") for u in uris)
    assert any("security.ubuntu.com" in u for u in uris)


def test_nothing_is_installed_on_the_appliance_later():
    assert "systemctl mask apt-daily.timer apt-daily-upgrade.timer" in POSTINST


def test_the_test_profile_adds_only_the_self_test_and_a_small_kernel():
    conf = (MKOSI / "mkosi.profiles" / "test" / "mkosi.conf").read_text()
    assert "Packages=linux-image-virtual" in conf and "ExtraTrees=extra" in conf
    appliance = (MKOSI / "mkosi.conf.d" / "10-appliance.conf").read_text()
    assert "Profiles=!test" in appliance and "linux-generic" in appliance and "linux-firmware" in appliance


def test_the_appliance_initrd_holds_only_what_reaches_the_disk():
    """Every module and all firmware made the boot file 483 MB; the EFI partition holds one per slot."""
    appliance = (MKOSI / "mkosi.conf.d" / "10-appliance.conf").read_text()
    assert "KernelModulesInitrdExclude=.*" in appliance and "KernelModulesInitrdInclude=default" in appliance
    for module in ("/vmd.ko", "/megaraid_sas.ko", "/uas.ko", "/tpm_crb.ko"):
        assert module in appliance
