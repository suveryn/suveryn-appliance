"""The installer's answers (mkosi.extra/usr/lib/suveryn/install): name, time zone and network, checked
before anything is written (#37, C1 and C4). Any machine:

    python3 -m pytest image/mkosi/test/test_install.py

Installing itself is tested in the virtual machine (run-vm-tests, step 1).
"""

import argparse
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "mkosi.extra" / "usr" / "lib" / "suveryn" / "install"
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("install_under_test", str(TOOL))
install = importlib.util.module_from_spec(importlib.util.spec_from_loader("install_under_test", _loader))
_loader.exec_module(install)


@pytest.mark.parametrize("name", ["notary-brussels-01", "a", "x" * 63, "0office"])
def test_good_names(name):
    assert install.check_name(name) == name


@pytest.mark.parametrize("name", ["", "-office", "office-", "Office", "notary_brussels", "x" * 64, "notary.brussels",
                                  "kantoor-ū"])
def test_bad_names(name):
    with pytest.raises(install.Refused):
        install.check_name(name)


def test_time_zones():
    if not Path("/usr/share/zoneinfo/Europe/Brussels").exists():
        pytest.skip("no tzdata here")
    assert install.check_timezone("Europe/Brussels") == "Europe/Brussels"
    for tz in ("Europe/Nowhere", "../../etc/passwd", "/etc/passwd", "", "Europe"):
        with pytest.raises(install.Refused):
            install.check_timezone(tz)


def network(monkeypatch, **kw):
    monkeypatch.setattr(install, "interfaces", lambda: {"eno1": "52:54:00:12:34:56", "eno2": "52:54:00:aa:bb:cc"})
    args = argparse.Namespace(**{"interface": None, "address": "dhcp", "gateway": None, "dns": None, **kw})
    return install.network_file(args)


def test_dhcp_on_the_first_port_by_default(monkeypatch):
    text, summary = network(monkeypatch)
    assert "MACAddress=52:54:00:12:34:56" in text and "DHCP=yes" in text
    assert summary == {"interface": "eno1", "mac": "52:54:00:12:34:56", "address": "dhcp"}


def test_a_fixed_address(monkeypatch):
    text, summary = network(monkeypatch, interface="eno2", address="192.168.1.50/24", gateway="192.168.1.1",
                            dns="192.168.1.1,9.9.9.9")
    assert text.splitlines()[-4:] == ["Address=192.168.1.50/24", "Gateway=192.168.1.1", "DNS=192.168.1.1", "DNS=9.9.9.9"]
    assert "MACAddress=52:54:00:aa:bb:cc" in text and "DHCP" not in text
    assert summary["gateway"] == "192.168.1.1"


@pytest.mark.parametrize("kw, message", [
    ({"interface": "eth9"}, "no Ethernet port"),
    ({"address": "192.168.1.50"}, "network prefix"),
    ({"address": "192.168.1.0/24", "dns": "1.1.1.1"}, "network prefix"),
    ({"address": "192.168.1.50/24", "gateway": "10.0.0.1", "dns": "1.1.1.1"}, "isn't in"),
    ({"address": "192.168.1.50/24", "gateway": "192.168.1.1"}, "needs a DNS server"),
    ({"address": "300.1.1.1/24", "dns": "1.1.1.1"}, "unusable address"),
    ({"address": "192.168.1.50/24", "dns": "not-an-ip"}, "unusable address"),
    ({"gateway": "192.168.1.1"}, "go with a fixed address"),
])
def test_unusable_network_settings(monkeypatch, kw, message):
    with pytest.raises(install.Refused, match=message):
        network(monkeypatch, **kw)


def test_it_runs_only_on_the_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "RUN", tmp_path)
    (tmp_path / "boot-mode").write_text("installed\n")
    args = argparse.Namespace(name="x", timezone="Europe/Brussels", disk="/dev/null")
    with pytest.raises(install.Refused, match="not as the installer"):
        install.install(args)


# ---------------------------------------------------------------------------- the stick's bundles (C3)
RELEASE = Path(__file__).resolve().parents[3] / "bundles" / "suveryn-bundle-release"


def signed(tmp_path: Path, keys: Path, folder: str, kind: str, name: str, files: dict[str, str]) -> Path:
    import subprocess
    b = tmp_path / "stick" / folder
    for rel, text in files.items():
        (b / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / rel).write_text(text)
    key = tmp_path / "release.key"
    if not key.exists():
        subprocess.run([sys.executable, str(RELEASE), "test-key", str(key)], check=True, capture_output=True)
        keys.mkdir(exist_ok=True)
        pem = subprocess.run([sys.executable, str(RELEASE), "public-key", "--key-file", str(key)], check=True,
                             capture_output=True, text=True).stdout
        (keys / "current.pem").write_text(pem)
    subprocess.run([sys.executable, str(RELEASE), "make", str(b), "--kind", kind, "--name", name, "--version", "2026.11.1"],
                   check=True, capture_output=True)
    subprocess.run([sys.executable, str(RELEASE), "sign", str(b), "--key-file", str(key)], check=True, capture_output=True)
    return b


def test_c3_lists_the_models_and_the_packs_are_installed_without_a_choice(tmp_path, monkeypatch):
    """A packs bundle on the stick is not a model to choose (C3 shows models); install() takes every valid one."""
    keys = tmp_path / "keys"
    monkeypatch.setenv("SUVERYN_BUNDLE_KEYS", str(keys))
    monkeypatch.setattr(install, "LIB", TOOL.parent)
    signed(tmp_path, keys, "qwen-test", "model", "qwen-test", {"model.gguf": "not a model, a test"})
    signed(tmp_path, keys, "be-test-2026.11.1", "packs", "be-test",
           {"company-formation.yaml": "manifest: 3\n", "text/en.json": "{}", "text/nl.json": "{}", "text/fr.json": "{}"})
    stick = tmp_path / "stick"
    models = install.list_models(stick)
    assert [(m["folder"], m["kind"]) for m in models] == [("qwen-test", "model")]
    packs = install.list_models(stick, "packs")
    assert [(m["name"], m["version"], m["valid"]) for m in packs] == [("be-test", "2026.11.1", True)]
    # the pack installed under an installer's target becomes the pack's current version, by a relative link
    bundle = install.load("bundle")
    target = tmp_path / "target"
    r = bundle.install(packs and stick / packs[0]["folder"], target / "lib/suveryn/bundles")
    assert r["current"] == str(target / "lib/suveryn/packs/be-test")
    assert (target / "lib/suveryn/packs/be-test/text/nl.json").read_text() == "{}"
