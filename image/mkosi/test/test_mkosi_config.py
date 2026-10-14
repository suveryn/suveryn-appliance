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
    """On an empty disk: the EFI partition and slot A copied from the stick, B and its verity empty, data."""
    parts = partitions(MKOSI / "target" / profile)
    assert [p["Type"] for p in parts] == ["esp", "root", "root-verity", "root", "root-verity", "linux-generic"]
    esp, a, a_verity, b, b_verity, data = parts
    assert esp["CopyBlocks"] == "@SOURCE_ESP@"            # install-disk names the stick's EFI partition
    assert a["CopyBlocks"] == a_verity["CopyBlocks"] == "auto"  # the running system's slot A
    # systemd finds a verity root by UUIDs derived from the root hash; a block copy doesn't keep them.
    assert a["UUID"] == "@ROOT_UUID@" and a_verity["UUID"] == "@ROOT_VERITY_UUID@"
    assert b["Label"] == b_verity["Label"] == "_empty" and "CopyBlocks" not in b
    assert data["Label"] == "suveryn-data" and data["Format"] == "ext4" and data["Encrypt"] == "key-file"
    assert "SizeMaxBytes" not in data  # the rest of the disk
    assert data["CopyFiles"] == "/usr/lib/suveryn/empty-machine-id:/lib/suveryn/host/machine-id"


def test_install_disk_fills_in_every_placeholder():
    tool = (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "install-disk").read_text()
    used = set(re.findall(r"@[A-Z_]+@", "".join(f.read_text() for f in (MKOSI / "target" / "appliance").glob("*.conf"))))
    assert used == {"@SOURCE_ESP@", "@ROOT_UUID@", "@ROOT_VERITY_UUID@"}
    for placeholder in used:
        assert placeholder in tool


def test_the_test_copies_keep_their_size():
    """A copied slot that grows puts unverified space after the verified content."""
    parts = partitions(MKOSI / "target" / "test")
    assert parts[1]["Weight"] == parts[2]["Weight"] == "0"


def test_appliance_and_test_differ_only_in_sizes():
    strip = lambda ps: [{k: v for k, v in p.items() if not k.startswith("Size") and k != "Weight"}  # noqa: E731
                        for p in ps]
    assert strip(partitions(MKOSI / "target" / "appliance")) == strip(partitions(MKOSI / "target" / "test"))


def test_both_slots_verity_fits_a_32_gib_slot():
    """SHA-256 per 4 KiB block: 256 MiB of leaf hashes for 32 GiB, plus the tree above them."""
    leaves = 32 * 2**30 // 4096 * 32
    a_verity = partitions(MKOSI / "mkosi.repart")[2]
    b_verity = partitions(MKOSI / "target" / "appliance")[4]
    for v in (a_verity, b_verity):
        assert size(v["SizeMinBytes"]) >= leaves * 129 // 128
    assert a_verity["SizeMinBytes"] == b_verity["SizeMinBytes"]  # an update writes either slot


BOOT_MODE = (MKOSI / "mkosi.images" / "initrd" / "extra" / "usr" / "lib" / "suveryn" / "boot-mode").read_text()
BOOT_MODE_UNIT = MKOSI / "mkosi.images" / "initrd" / "extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-boot-mode.service"


def test_var_is_the_data_partition_and_nothing_on_it_runs():
    """Mounted in the initrd by suveryn-boot-mode: the data partition (installed) or memory (installer)."""
    assert 'mount -t ext4 -o noexec,nosuid,nodev /dev/mapper/suveryn-data "$SYSROOT/var"' in BOOT_MODE
    assert 'mount -t tmpfs -o mode=0755,noexec,nosuid,nodev' in BOOT_MODE
    assert not [e for e in FSTAB if e[1] in ("/var", "/etc/machine-id")]  # only the initrd mounts them


def test_only_the_boot_disk_is_looked_at():
    """A stick never opens an installed appliance's data, and an appliance only its own disk's."""
    assert "LoaderDevicePartUUID" in BOOT_MODE and '/sys/class/block/"$disk"/"$disk"*' in BOOT_MODE
    assert "PARTLABEL" not in BOOT_MODE and "PARTLABEL" not in (MKOSI / "mkosi.extra" / "etc" / "crypttab").read_text()
    unit = BOOT_MODE_UNIT.read_text()
    assert "Before=initrd-fs.target" in unit and "After=initrd-root-fs.target" in unit
    assert (BOOT_MODE_UNIT.parent / "initrd-fs.target.wants" / BOOT_MODE_UNIT.name).is_symlink()


def test_tmp_in_memory_and_no_swap():
    assert next(e for e in FSTAB if e[1] == "/tmp")[2] == "tmpfs"
    assert not [e for e in FSTAB if "swap" in e]


def test_the_machine_id_is_mounted_from_the_data_partition_in_the_initrd():
    """systemd won't follow a link for /etc/machine-id; a bind mount set up in the initrd works."""
    assert 'mount --bind "$SYSROOT/var/lib/suveryn/host/machine-id" "$SYSROOT/etc/machine-id"' in BOOT_MODE
    assert ": > /etc/machine-id" in POSTINST and "link /var/lib/suveryn/host/machine-id" not in POSTINST


def test_only_the_listed_etc_paths_link_into_the_data_partition():
    links = dict(re.findall(r"^link (\S+)\s+(\S+)$", POSTINST, re.M))
    into_var = {target: dest for target, dest in links.items() if target.startswith("/var/")}
    assert set(into_var.values()) == {"/etc/suveryn", "/etc/hostname", "/etc/hosts", "/etc/localtime", "/etc/default/keyboard", "/etc/systemd/network"}
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


def test_the_data_partition_opens_with_the_tpm_or_five_typed_keys():
    """In our own initrd (mkosi.images/initrd): the TPM, then 5 typed attempts (the TPM counts as one try)."""
    assert ('/usr/lib/systemd/systemd-cryptsetup attach suveryn-data "/dev/$data" - tpm2-device=auto,tries=6'
            in BOOT_MODE)
    assert "tpm2.target" in BOOT_MODE_UNIT.read_text()
    conf = (MKOSI / "mkosi.conf").read_text()
    assert "Dependencies=initrd" in conf and "Initrds=%O/initrd" in conf
    for lib in ("libtss2-esys", "libtss2-tcti-device"):
        assert lib in (MKOSI / "mkosi.images" / "initrd" / "mkosi.conf").read_text() and lib in conf
    assert "dmsetup" in conf  # without its udev rules libdevmapper waits forever (install-disk hung)


def test_the_tpm_binding_is_pcr_7_and_never_pcrlock():
    tool = (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "install-disk").read_text()
    assert 'TPM_PCRS = "7"' in tool and '"--tpm2-pcrlock="' in tool
    assert "systemctl mask" in POSTINST and "systemd-pcrlock" in POSTINST


def test_every_extra_tree_exists():
    """mkosi resolves paths in mkosi.conf.d/ against the main folder, and in a profile against the profile's."""
    def trees(conf: Path) -> list[str]:
        out, inside = [], False
        for line in conf.read_text().splitlines():
            if line.startswith("ExtraTrees="):
                inside = True
                line = line.split("=", 1)[1]
            elif not line.startswith((" ", "\t")):
                inside = False
            if inside and line.strip() and not line.strip().startswith("#"):
                out.append(line.strip().split(":")[0])
        return out
    for conf, base in [(MKOSI / "mkosi.conf.d" / "10-appliance.conf", MKOSI),
                       (MKOSI / "mkosi.profiles" / "test" / "mkosi.conf", MKOSI / "mkosi.profiles" / "test"),
                       (MKOSI / "mkosi.images" / "initrd" / "mkosi.conf", MKOSI / "mkosi.images" / "initrd")]:
        found = trees(conf)
        assert found, conf
        for t in found:
            if t == "../../test/keys/public":  # made by test/make-test-keys, never committed
                continue
            assert (base / t).is_dir(), f"{conf.name}: {t}"


def test_the_identity_is_made_once_at_first_boot_without_network():
    unit = configparser.ConfigParser(strict=False, interpolation=None)
    unit.read(MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-identity.service")
    text = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-identity.service").read_text()
    assert "ConditionPathExists=!/var/lib/suveryn/identity/identity.json" in text  # never replaced
    assert "ConditionPathExists=!/run/suveryn/installer-mode" in text  # not on the installer stick
    assert "tpm2.target" in unit["Unit"]["After"] and "local-fs.target" in unit["Unit"]["After"]
    assert unit["Service"]["ExecStart"] == "/usr/lib/suveryn/identity create"
    assert unit["Service"]["PrivateNetwork"] == "yes"
    assert "systemctl enable suveryn-identity.service" in POSTINST
    assert re.search(r"^\s+openssl$", (MKOSI / "mkosi.conf").read_text(), re.M)
    for tool in ("identity", "system-state"):
        path = MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / tool
        assert path.stat().st_mode & 0o111, tool


def test_the_identity_is_sealed_without_pcrs():
    """Bound to the TPM itself, not to PCR 7: a firmware update must not lose the identity (#35)."""
    tool = (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "identity").read_text()
    assert '"--tpm2-pcrs="' in tool and '"host+tpm2" if has_tpm() else "host"' in tool


def test_every_python_tool_in_the_image_compiles():
    """A syntax error in a tool only shows when the system runs it; catch it here."""
    for path in (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn").iterdir():
        if path.is_file() and path.read_text().startswith("#!/usr/bin/python3"):
            compile(path.read_text(), str(path), "exec")  # raises SyntaxError


def test_no_bytecode_in_the_image_tree():
    """A test that imports a tool from mkosi.extra must not leave a __pycache__ for the image to pick up."""
    assert not list((MKOSI / "mkosi.extra").rglob("__pycache__"))


BUNDLE_KEYS = MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "bundle-keys"


def test_the_test_bundle_keys_never_reach_the_appliance():
    profile = (MKOSI / "mkosi.profiles" / "test" / "mkosi.conf").read_text()
    assert "../../test/keys/public" in profile
    assert "test/keys" not in (MKOSI / "mkosi.conf.d" / "10-appliance.conf").read_text()
    assert "image/mkosi/test/keys/" in (REPO / ".gitignore").read_text()
    assert all(p.name.startswith("release-") for p in BUNDLE_KEYS.glob("*.pem"))


@pytest.mark.xfail(strict=True, reason="waiting for the two release keys from AWS KMS (bundle-keys/README.md)")
def test_the_appliance_trusts_two_release_keys():
    assert sorted(p.name for p in BUNDLE_KEYS.glob("*.pem")) == ["release-current.pem", "release-spare.pem"]


def test_the_console_installer_runs_on_the_stick_only():
    unit = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-installer.service").read_text()
    assert "ConditionPathExists=/run/suveryn/installer-mode" in unit and "TTYPath=/dev/tty1" in unit
    assert "ExecStart=/usr/lib/suveryn/installer-ui" in unit and "Conflicts=getty@tty1.service" in unit
    assert "systemctl enable suveryn-installer.service" in POSTINST
    test = (MKOSI / "mkosi.profiles" / "test" / "extra" / "usr" / "lib" / "systemd" / "system" /
            "suveryn-installer.service.d" / "test.conf").read_text()
    assert "ConditionCredential=suveryn.installer-on-serial" in test  # never on the appliance's own console


def test_the_console_looks_like_the_design():
    """The brand's dark theme as the console's 16 colours, and a font with ū (Uni2 Terminus)."""
    rows = [[int(v) for v in line.split(",")] for line in
            (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "console-colours").read_text().split()]
    assert len(rows) == 3 and all(len(r) == 16 for r in rows)
    colour = lambda i: "#%02X%02X%02X" % (rows[0][i], rows[1][i], rows[2][i])  # noqa: E731
    assert (colour(0), colour(1), colour(6), colour(7), colour(15)) == ("#0B0D10", "#FA5B35", "#3AD4CA", "#A4A6A7", "#F3F4F5")
    setup = (MKOSI / "mkosi.extra" / "etc" / "default" / "console-setup").read_text()
    assert 'CODESET="Uni2"' in setup and 'FONTFACE="Terminus"' in setup
    for pkg in ("kbd", "console-setup-linux", "keyboard-configuration"):
        assert re.search(rf"^\s+{pkg}$", (MKOSI / "mkosi.conf").read_text(), re.M), pkg
