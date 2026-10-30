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
    assert set(into_var.values()) == {"/etc/suveryn", "/etc/hostname", "/etc/hosts", "/etc/localtime", "/etc/default/keyboard",
                                      "/etc/systemd/network", "/etc/nginx/sites-enabled/suveryn", "/etc/postgresql"}
    for path in into_var.values():
        assert f"`{path}" in DESIGN, f"{path} isn't in the design note's list"
    assert links["/run/suveryn/resolv.conf"] == "/etc/resolv.conf"  # the stub, or suveryn-dns while it runs


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
    """In our own initrd (mkosi.images/initrd): the TPM, then the unlock screen's 5 typed attempts."""
    assert ('/usr/lib/systemd/systemd-cryptsetup attach suveryn-data "/dev/$data" - tpm2-device=auto,headless=true'
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
                       (MKOSI / "mkosi.conf.d" / "20-web-setup.conf", MKOSI),
                       (MKOSI / "mkosi.conf.d" / "30-services.conf", MKOSI),
                       (MKOSI / "mkosi.profiles" / "test" / "mkosi.conf", MKOSI / "mkosi.profiles" / "test"),
                       (MKOSI / "mkosi.images" / "initrd" / "mkosi.conf", MKOSI / "mkosi.images" / "initrd")]:
        found = trees(conf)
        assert found, conf
        for t in found:
            if t in ("../../test/keys/public", "../../test/ui", "prebuilt"):  # made by test/make-test-keys, make-test-ui and prebuild-services, never committed
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
    """The brand's dark theme as the console's 16 colours, and a font with ū and the QR code's half blocks (Terminus)."""
    rows = [[int(v) for v in line.split(",")] for line in
            (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "console-colours").read_text().split()]
    assert len(rows) == 3 and all(len(r) == 16 for r in rows)
    colour = lambda i: "#%02X%02X%02X" % (rows[0][i], rows[1][i], rows[2][i])  # noqa: E731
    assert (colour(0), colour(1), colour(6), colour(7), colour(15)) == ("#0B0D10", "#FA5B35", "#3AD4CA", "#A4A6A7", "#F3F4F5")
    setup = (MKOSI / "mkosi.extra" / "etc" / "default" / "console-setup").read_text()
    assert 'CODESET="Vietnamese"' in setup and 'FONTFACE="Terminus"' in setup
    for pkg in ("kbd", "console-setup-linux", "keyboard-configuration"):
        assert re.search(rf"^\s+{pkg}$", (MKOSI / "mkosi.conf").read_text(), re.M), pkg
    # Applied directly at every start: the package's own script saves into the read-only /etc and fails.
    dropin = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "console-setup.service.d" /
              "50-suveryn.conf").read_text()
    assert "ExecStart=\nExecStart=/bin/setupcon --force\n" in dropin and "--save" not in dropin.split("[Service]")[1]


def test_the_unlock_screen_is_in_the_initrd():
    """C8b: the TPM first without asking, then unlock-screen: 5 attempts, the TPM enrolled again (PCR 7)."""
    initrd = MKOSI / "mkosi.images" / "initrd" / "extra" / "usr" / "lib" / "suveryn"
    assert "tpm2-device=auto,headless=true" in BOOT_MODE and '/usr/lib/suveryn/unlock-screen "/dev/$data"' in BOOT_MODE
    screen = (initrd / "unlock-screen").read_text()
    assert (initrd / "unlock-screen").stat().st_mode & 0o111
    assert "TRIES=5" in screen and "headless=true" in screen
    # The old slot wiped on its own first: with the enrolment, cryptenroll skips an "already enrolled" PCR set.
    assert re.search(r'--wipe-slot=tpm2 "\$DATA" [^\n]*&&\n\s+systemd-cryptenroll --unlock-key-file="\$KEY" '
                     r'--tpm2-device=auto --tpm2-pcrs=7 --tpm2-pcrlock= \\\n\s+"\$DATA"', screen)
    assert (initrd / "console-colours").read_text() == (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" /
                                                       "console-colours").read_text()
    conf = (MKOSI / "mkosi.images" / "initrd" / "mkosi.conf").read_text()
    assert re.search(r"^\s+kbd$", conf, re.M) and re.search(r"^\s+console-setup-linux$", conf, re.M)


def test_the_console_runs_on_the_installed_appliance_only():
    unit = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-console.service").read_text()
    assert "ConditionPathExists=!/run/suveryn/installer-mode" in unit and "TTYPath=/dev/tty1" in unit
    assert "suveryn-console.service" in POSTINST and re.search(r"^\s+qrencode$", (MKOSI / "mkosi.conf").read_text(), re.M)


def test_mdns_answers_for_the_name():
    resolved = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "resolved.conf.d" / "50-suveryn-mdns.conf").read_text()
    assert "MulticastDNS=yes" in resolved
    assert "MulticastDNS=yes" in (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "network" /
                                  "80-suveryn-dhcp.network").read_text()
    assert '"MulticastDNS=yes"' in (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "install").read_text()


def test_the_web_setup_runs_on_the_installed_appliance_from_the_image():
    """#37: the setup units from setup/, its user made at build time (/etc/passwd is read-only), nginx
    with the snippets built in and the office's site on the data partition."""
    assert "systemctl enable suveryn-setup.service suveryn-setup-provision.path" in POSTINST
    assert "systemd-sysusers /usr/lib/sysusers.d/suveryn-setup.conf" in POSTINST
    assert "rm -f /etc/nginx/sites-enabled/default" in POSTINST and "ConditionPathExists=/etc/nginx/sites-enabled/suveryn" in POSTINST
    conf = (MKOSI / "mkosi.conf.d" / "20-web-setup.conf").read_text()
    assert "nginx" in conf and "../../setup:/opt/suveryn/appliance/setup" in conf
    for unit in ("suveryn-setup.service", "suveryn-setup-provision.service", "suveryn-setup-provision.path"):
        text = (MKOSI.parent.parent / "setup" / "systemd" / unit).read_text()
        assert "ConditionPathExists=!/run/suveryn/installer-mode" in text, unit
        assert "/opt/suveryn/appliance/setup/suveryn-setup" in text or unit.endswith(".path"), unit


def test_nvidia_licence_text_ships_without_usr_share_doc():
    """WithDocs=no drops the packages' copyright files; NVIDIA's driver licence (§1.1(d)) requires the
    text to reach every recipient, so it is in mkosi.extra and the build stops without it (#49)."""
    assert "WithDocs=no" in (MKOSI / "mkosi.conf").read_text()
    licence = MKOSI / "mkosi.extra" / "usr" / "share" / "suveryn" / "licences" / "nvidia-graphics-drivers-580.copyright"
    text = licence.read_text()
    assert "License: NVIDIA-graphics-drivers" in text and "NVIDIA Driver License Agreement" in text
    assert "kernel-open" in text and "dual licensed as MIT/GPL" in text
    assert "/usr/share/suveryn/licences/nvidia-graphics-drivers-580.copyright" in POSTINST
    assert "dpkg-query -W 'nvidia-utils-*'" in POSTINST and "exit 1" in POSTINST


# ---------------------------------------------------------------------------- the services (#47)
SERVICES = MKOSI / "services"


def provision():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("provision", str(REPO / "provision" / "suveryn-provision"))
    spec = importlib.util.spec_from_loader("provision", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def packages(conf: Path) -> set[str]:
    out, inside = set(), False
    for line in conf.read_text().splitlines():
        if line.startswith("Packages="):
            inside, line = True, line.split("=", 1)[1]
        elif not line.startswith((" ", "\t")):
            inside = False
        if inside and line.strip() and not line.strip().startswith("#"):
            out.add(line.strip())
    return out


def test_the_services_are_the_appliances_and_never_the_test_systems():
    conf = (MKOSI / "mkosi.conf.d" / "30-services.conf").read_text()
    assert re.search(r"^\[Match\]\nProfiles=!test$", conf, re.M) and "ExtraTrees=services" in conf


def image_release() -> str:
    """The Ubuntu release the image is built on, as provision names it (VERSION_ID)."""
    codename = re.search(r"^Release=(\S+)$", (MKOSI / "mkosi.conf").read_text(), re.M)[1]
    return {"noble": "24.04", "resolute": "26.04"}[codename]


def test_the_image_is_ubuntu_26_04_with_its_own_boot_file():
    """#57: resolute, from the archive over HTTPS; mkosi 26 reads UnifiedKernelImages=yes as a prebuilt,
    distribution-signed boot file, which Ubuntu doesn't ship, so ours is built (unsigned until #45)."""
    conf = (MKOSI / "mkosi.conf").read_text()
    assert image_release() == "26.04"
    assert re.search(r"^UnifiedKernelImages=unsigned$", conf, re.M)
    sources = (MKOSI / "mkosi.sandbox/etc/apt/sources.list.d/mkosi.sources").read_text()
    assert "Suites: resolute resolute-updates" in sources and "Suites: resolute-security" in sources
    assert "noble" not in sources and "http://" not in sources
    # 26.04 splits these off systemd; the installer stopped without systemd-repart (run-vm-tests, #57)
    assert {"systemd-repart", "systemd-cryptsetup"} <= packages(MKOSI / "mkosi.conf")


def test_the_image_has_every_package_the_services_need_at_run_time():
    """provision's RUNTIME_PACKAGES: what an ordinary Ubuntu gets from apt, the image has built in."""
    have = set()
    for conf in (MKOSI / "mkosi.conf", *sorted((MKOSI / "mkosi.conf.d").glob("*.conf"))):
        have |= packages(conf)
    p = provision()
    missing = set(p.for_postgres(p.RUNTIME_PACKAGES, p.POSTGRES_FOR_UBUNTU[image_release()])) - have
    assert not missing, f"not in the image: {sorted(missing)}"
    # and nothing an ordinary Ubuntu only needs: no ufw, no apt updates, no compilers
    assert not have & {"ufw", "unattended-upgrades", "build-essential", "cmake"}


def test_the_services_users_are_made_when_the_image_is_built():
    users = dict(re.findall(r"^u (\S+)\s+-\s+\"[^\"]*\"\s+(\S+)", (SERVICES / "usr/lib/sysusers.d/suveryn.conf").read_text(), re.M))
    assert set(users) == {"suveryn", "suveryn-llm", "keycloak", "suveryn-dns", "suveryn-reader"}
    assert users["keycloak"] == "/opt/keycloak"
    assert re.search(r"^m suveryn suveryn-reader$", (SERVICES / "usr/lib/sysusers.d/suveryn.conf").read_text(), re.M)
    assert "systemd-sysusers /usr/lib/sysusers.d/suveryn.conf" in POSTINST


def test_the_readers_sudo_rule_is_provisions_and_checked():
    built_in = (SERVICES / "etc/sudoers.d/suveryn-reader").read_text()
    assert built_in == provision().reader_sudoers()
    assert "chmod 0440 /etc/sudoers.d/suveryn-reader" in POSTINST and "visudo -cqf /etc/sudoers.d/suveryn-reader" in POSTINST


def test_the_services_folders_are_made_on_the_data_partition_as_provision_makes_them():
    lines = {f[1]: f[2:5] for f in (line.split() for line in (SERVICES / "usr/lib/tmpfiles.d/suveryn-services.conf").read_text()
                                    .splitlines() if line.strip() and not line.startswith("#"))}
    p = provision()
    assert lines[str(p.VAR / "work")] == ["0700", "suveryn", "suveryn"]
    assert lines[p.READER_SETTINGS["SUVERYN_EXTRACT_INBOX"]] == ["2750", "suveryn", "suveryn-reader"]
    assert lines[p.READER_SETTINGS["SUVERYN_EXTRACT_WORK_DIR"]] == ["0700", "suveryn-reader", "suveryn-reader"]
    assert "/var/lib/suveryn/postgresql-etc" in lines


def test_postgresqls_cluster_is_made_at_first_boot_not_in_the_image():
    pg = provision().POSTGRES_FOR_UBUNTU[image_release()]
    assert f"pg_dropcluster {pg} main" in POSTINST and "link /var/lib/suveryn/postgresql-etc   /etc/postgresql" in POSTINST
    assert '"pg_createcluster", pg, "main"' in (REPO / "provision" / "suveryn-provision").read_text()


def test_only_the_web_ports_come_in():
    rules = (MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "inbound.nft").read_text()
    assert "policy drop;" in rules and "tcp dport { 80, 443 } accept" in rules
    assert re.findall(r"tcp dport", rules) == ["tcp dport"]  # no other TCP port
    assert re.findall(r"udp dport (\d+)", rules) == ["68", "546", "5353"]  # DHCP, DHCPv6, mDNS
    assert "systemctl enable suveryn-inbound.service" in POSTINST
    unit = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system" / "suveryn-inbound.service").read_text()
    assert "Before=network-pre.target" in unit and "/usr/lib/suveryn/inbound.nft" in unit


def test_the_inbound_rules_parse():
    import shutil
    import subprocess
    if not shutil.which("nft"):
        pytest.skip("no nft here")
    r = subprocess.run(["nft", "-c", "-f", str(MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn" / "inbound.nft")],
                       capture_output=True, text=True)
    if r.returncode and "Operation not permitted" in r.stderr:
        pytest.skip("nft -c needs privileges here")
    assert r.returncode == 0, r.stderr


def test_the_packer_images_hardening_is_in_the_read_only_system_too():
    """The data lifecycle promises a 1 GB journal and no core dumps (image/scripts/harden.sh did it on the
    Packer image only)."""
    extra = MKOSI / "mkosi.extra"
    harden = (REPO / "image" / "scripts" / "harden.sh").read_text()
    sysctl = (extra / "usr/lib/sysctl.d/90-suveryn.conf").read_text()
    for line in re.findall(r"^([a-z][\w.]+ = \S+)$", harden, re.M):
        assert line in sysctl, line
    assert "SystemMaxUse=1G" in (extra / "usr/lib/systemd/journald.conf.d/90-suveryn.conf").read_text()
    assert "Storage=none" in (extra / "usr/lib/systemd/coredump.conf.d/90-suveryn.conf").read_text()
    assert "* hard core 0" in (extra / "etc/security/limits.d/90-suveryn.conf").read_text()


def test_suveryn_itself_comes_prebuilt_and_checked_against_the_pins():
    conf = (MKOSI / "mkosi.conf.d" / "30-services.conf").read_text()
    assert re.search(r"^\s+prebuilt$", conf, re.M) and "../../provision/systemd:/usr/lib/systemd/system" in conf
    assert "image/mkosi/prebuilt/" in (REPO / ".gitignore").read_text()
    assert "suveryn-provision check-prebuilt" in POSTINST
    script = (MKOSI / "prebuild-services").read_text()
    assert "prebuild --read-only-image" in script and "check-prebuilt --root" in script
    assert "--exclude=opt/suveryn/core/.git" in script and "--exclude=opt/keycloak/data" in script


def test_on_the_read_only_system_the_services_write_only_the_data_partition():
    assert "ln -s /var/lib/suveryn/keycloak /opt/keycloak/data" in POSTINST
    tmpfiles = (SERVICES / "usr/lib/tmpfiles.d/suveryn-services.conf").read_text()
    assert re.search(r"^d /var/lib/suveryn/keycloak\s+0700 keycloak\s+keycloak", tmpfiles, re.M)
    assert "/opt/suveryn/lib/cuda > /etc/ld.so.conf.d/suveryn-cuda.conf && ldconfig" in POSTINST


def test_the_services_are_enabled_in_the_image_and_wait_for_first_boots_settings():
    assert "systemctl enable suveryn-llm.service keycloak.service suveryn-gateway.service" in POSTINST
    for unit, settings in (("suveryn-gateway", "/etc/suveryn/gateway.env"), ("keycloak", "/etc/suveryn/keycloak.env"),
                           ("suveryn-llm", "/etc/suveryn/llama-presets.ini")):
        text = (REPO / "provision" / "systemd" / f"{unit}.service").read_text()
        unit_section = text.split("[Service]")[0]
        assert f"ConditionPathExists={settings}" in unit_section, unit
    # the network mode's outgoing rules and resolver: enabled too, waiting for the mode first boot sets
    assert "systemctl enable suveryn-egress.service suveryn-dns.service" in POSTINST
    for unit in ("suveryn-egress", "suveryn-dns"):
        text = (REPO / "provision" / "systemd" / f"{unit}.service").read_text()
        assert "ConditionPathExists=/etc/suveryn/network-mode.env" in text.split("[Service]")[0], unit


def test_resolv_conf_is_the_stub_until_the_appliances_own_resolver_runs():
    var = (MKOSI / "mkosi.extra" / "usr" / "lib" / "tmpfiles.d" / "suveryn-var.conf").read_text()
    assert "L /run/suveryn/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf" in var
    dns = (REPO / "provision" / "systemd" / "suveryn-dns.service").read_text()
    assert "nameserver 127.0.0.1" in dns.split("ExecStartPost=")[1].splitlines()[0]
    assert "ln -s /run/systemd/resolve/stub-resolv.conf /run/suveryn/resolv.conf" in dns
    assert "ReadWritePaths=-/run/suveryn" in dns  # ProtectSystem=strict leaves /run read-only otherwise
    assert provision().RESOLV_TEXT.endswith("nameserver 127.0.0.1\noptions edns0 trust-ad\n")


def test_the_reader_sandbox_and_the_network_mode_tool_are_where_provisioning_puts_them():
    p = provision()
    assert f"/opt/suveryn/bin/suveryn-reader-sandbox" in POSTINST and p.READER_WRAPPER.endswith("/opt/suveryn/bin/suveryn-reader-sandbox")
    assert "/opt/suveryn/bin/suveryn-sandbox-probe.py" in POSTINST
    assert f"ln -sf /opt/suveryn/network-mode/suveryn-network-mode {p.NETWORK_MODE_TOOL}" in POSTINST


def test_the_gpu_driver_is_ubuntus_signed_one_with_its_licence_and_nouveau_off():
    have = packages(MKOSI / "mkosi.conf.d" / "30-services.conf")
    assert {"linux-modules-nvidia-580-open-generic", "libnvidia-compute-580", "nvidia-utils-580"} <= have
    assert not any("dkms" in p or "-server" in p for p in have)  # nothing built on the appliance; the licence file is 580's
    sources = (MKOSI / "mkosi.sandbox" / "etc" / "apt" / "sources.list.d" / "mkosi.sources").read_text()
    assert re.findall(r"^Components: (.+)$", sources, re.M) == ["main universe restricted"] * 2
    assert "Repositories=universe,restricted" in (MKOSI / "mkosi.conf").read_text()
    blacklist = (SERVICES / "usr/lib/modprobe.d/suveryn-nvidia.conf").read_text()
    assert "blacklist nouveau" in blacklist and "options nouveau modeset=0" in blacklist


def test_what_the_vm_build_of_the_image_needed():
    """VM check of #47: openjdk's postinst needs /usr/share/man/man1; check-prebuilt reads the theme's pin;
    llama-server was linked against NCCL, which the image doesn't carry."""
    assert (MKOSI / "mkosi.skeleton" / "usr" / "share" / "man" / "man1").is_dir()
    assert "! -name brand-lock.json -exec rm -rf {} +" in POSTINST and "rm -rf /usr/share/man" in POSTINST
    assert "command -v python3 >/dev/null || " in (MKOSI / "prebuild-services").read_text()
    assert "../../keycloak/theme:/opt/suveryn/appliance/keycloak/theme" in (MKOSI / "mkosi.conf.d" / "30-services.conf").read_text()
    src = (REPO / "provision" / "suveryn-provision").read_text()
    assert '"-DGGML_CUDA_NCCL=OFF"' in src and "rpath-link" in src
    assert "GGML_CUDA_NCCL:BOOL=OFF" in (MKOSI / "prebuild-services").read_text()


def test_backups_are_in_the_image_nightly_and_without_rpcbind():
    conf = (MKOSI / "mkosi.conf.d" / "30-services.conf").read_text()
    assert {"age", "zstd", "cifs-utils", "nfs-common"} <= packages(MKOSI / "mkosi.conf.d" / "30-services.conf")
    assert "../../backup:/opt/suveryn/appliance/backup" in conf and "../../backup/systemd:/usr/lib/systemd/system" in conf
    assert "systemctl enable suveryn-backup.timer suveryn-backup-key-test.path suveryn-backup-request.path" in POSTINST
    assert "systemctl mask rpcbind.service rpcbind.socket" in POSTINST
    assert "../../backup/tmpfiles:/usr/lib/tmpfiles.d" in conf  # the key test's folder, for the gateway
    key_test = (REPO / "backup" / "systemd" / "suveryn-backup-key-test.path").read_text()
    assert "PathExists=/run/suveryn/backup-key-test/request" in key_test
    assert re.search(r"^d /run/suveryn/backup-key-test\s+0700 suveryn\s+suveryn", (REPO / "backup" / "tmpfiles" / "suveryn-backup.conf").read_text(), re.M)
    # Administration › Backups: back up now, the disks, a share's test, the settings
    assert "PathExists=/run/suveryn/backup-requests/request.json" in (REPO / "backup" / "systemd" / "suveryn-backup-request.path").read_text()
    assert re.search(r"^d /run/suveryn/backup-requests\s+0700 suveryn\s+suveryn", (REPO / "backup" / "tmpfiles" / "suveryn-backup.conf").read_text(), re.M)
    # the clock's corrections (clock/): the gateway's requests, as root, within suveryn-clock's bounds; hwclock keeps them
    assert "util-linux-extra" in packages(MKOSI / "mkosi.conf.d" / "30-services.conf")
    assert "../../clock:/opt/suveryn/appliance/clock" in conf and "../../clock/systemd:/usr/lib/systemd/system" in conf
    assert "../../clock/tmpfiles:/usr/lib/tmpfiles.d" in conf and "systemctl enable suveryn-clock-request.path" in POSTINST
    assert re.search(r"^d /run/suveryn/clock-requests\s+0700 suveryn\s+suveryn", (REPO / "clock" / "tmpfiles" / "suveryn-clock.conf").read_text(), re.M)
    unit = (REPO / "backup" / "systemd" / "suveryn-backup.service").read_text()
    assert "ExecStart=/usr/bin/python3 -I /opt/suveryn/appliance/backup/suveryn-backup run --scheduled" in unit
    assert "ConditionPathExists=/etc/suveryn/backups.json" in unit
    timer = (REPO / "backup" / "systemd" / "suveryn-backup.timer").read_text()
    assert "Persistent=true" in timer and "OnCalendar=*-*-* *:00:00" in timer  # hourly; the hour is in the settings
    tmpfiles = (SERVICES / "usr/lib/tmpfiles.d/suveryn-services.conf").read_text()
    assert re.search(r"^d /var/lib/suveryn/backup\s+0755 root\s+root", tmpfiles, re.M)


def test_the_nvidia_packages_empty_modules_folder_doesnt_stop_the_build(tmp_path):
    """VM check of #57: Ubuntu's NVIDIA modules package ships an empty /lib/modules/kernel/, which mkosi 26
    took for a kernel version. The prepare script removes it while it holds folders only."""
    import os
    import subprocess
    script = MKOSI / "mkosi.prepare.chroot"
    assert os.access(script, os.X_OK)
    stray = tmp_path / "kernel"
    (stray / "nvidia-580-open").mkdir(parents=True)
    subprocess.run([script, "final"], env={"SUVERYN_STRAY_MODULES": str(stray), "PATH": "/usr/bin:/bin"}, check=True)
    assert not stray.exists()
    (stray / "nvidia-580-open").mkdir(parents=True)
    (stray / "nvidia-580-open" / "nvidia.ko").write_text("a module")
    subprocess.run([script, "final"], env={"SUVERYN_STRAY_MODULES": str(stray), "PATH": "/usr/bin:/bin"}, check=True)
    assert (stray / "nvidia-580-open" / "nvidia.ko").exists()  # a folder with files is never removed
