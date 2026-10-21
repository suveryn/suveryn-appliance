"""The console installer's screens (mkosi.extra/usr/lib/suveryn/installer-ui; #37, C1 to C7), driven like
a person would: in a pseudo-terminal, with keys, reading what the screen shows (pyte, a terminal
emulator). A JSON file stands in for the machine (FakeSystem). Linux with python3-pyte or `pip install pyte`:

    python3 -m pytest image/mkosi/test/test_installer_ui.py

The real thing, on the test system's console, is run-vm-tests step 1.
"""

import copy
import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

import pytest

pyte = pytest.importorskip("pyte")

LIB = Path(__file__).resolve().parent.parent / "mkosi.extra" / "usr" / "lib" / "suveryn"
UI = LIB / "installer-ui"
KEY = "fjkcbvgt-lrhnedui-tbcvkjfg-hnulrdei-cgtbvfkj-rhnlduei-btcgkfvj-unhrlide"
TAB, ENTER, ESC, SPACE, UP, DOWN, F1, BACKSPACE = "\t", "\r", "\x1b", " ", "\x1b[A", "\x1b[B", "\x1b[[A", "\x7f"

MACHINE = {
    "version": "2026.10",
    "hardware": {"gpu": {"name": "NVIDIA GeForce RTX 4090", "memory_gb": 24}, "cores": 16, "memory_gb": 32, "tpm": True},
    "boot_disk": "sdb",
    "disks": [{"name": "nvme0n1", "size": 2_000_398_934_016, "model": "Samsung 990 PRO", "removable": False},
              {"name": "nvme1n1", "size": 1_000_204_886_016, "model": "WD Black SN850X", "removable": False},
              {"name": "sda", "size": 32_000_000_000, "model": "Small SSD", "removable": False},
              {"name": "sdb", "size": 64_000_000_000, "model": "Stick", "removable": True}],
    "min_disk": 140_000_000_000,
    "models": [{"folder": "qwen", "valid": True, "name": "qwen3.8-27b", "title": "Qwen3.8-27B", "version": "1",
                "size": 17_000_000_000, "key": "0f61e8d1aa3bfa1a8e4c", "default": True,
                "description": "Fewest legal errors on Belgian deeds in our benchmark.",
                "description_fr": "Le moins d'erreurs juridiques sur des actes belges dans notre banc d'essai."},
               {"folder": "mistral", "valid": True, "name": "mistral-small", "title": "Mistral Small 3.2 24B",
                "version": "1", "size": 15_000_000_000, "key": "0f61e8d1aa3bfa1a8e4c", "default": False,
                "description": "20 to 40% faster. People choose it per conversation."},
               {"folder": "third", "valid": False, "error": "signed by a key this system doesn't trust (abc…)"}],
    "interfaces": [{"name": "eno1", "mac": "52:54:00:12:34:56", "model": "Intel I226-V", "carrier": True, "speed": 1000},
                   {"name": "eno2", "mac": "52:54:00:12:34:57", "model": "Intel I226-V", "carrier": False, "speed": -1}],
    "dhcp": {"eno1": {"address": "192.168.1.50/24", "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}},
    "install": {"lines": ["install: checking qwen", "bundle: [1/1] model.gguf (16000 MiB)",
                          "step 1/4: partitioning", "install-disk [ 1.0s] partitioning",
                          "step 2/4: writing", "install: copying qwen", "bundle: [1/1] model.gguf (16000 MiB)",
                          "step 3/4: tpm", "step 4/4: boot"],
                "result": {"recovery_key": KEY}, "delay": 0.05},
}


class Console:
    """The installer in a pseudo-terminal of 100 x 30, as on the test system's serial console."""

    def __init__(self, tmp: Path, machine: dict, cols=100, rows=30, env=None):
        self.fake = tmp / "machine.json"
        self.fake.write_text(json.dumps(machine))
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))  # before curses starts
            os.environ.update(TERM="linux", COLUMNS=str(cols), LINES=str(rows), SUVERYN_INSTALLER_FAKE=str(self.fake),
                              SUVERYN_LIB=str(LIB), **(env or {}))
            os.execv(sys.executable, [sys.executable, str(UI)])
        self.wait("Language, keyboard and time zone")

    def pump(self, seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                self.stream.feed(data)

    @property
    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display)

    def wait(self, what: str, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.1)
            if what in self.text:
                return
        raise AssertionError(f"{what!r} didn't appear; the screen shows:\n{self.text}")

    def press(self, *keys, settle=0.25):
        for k in keys:
            os.write(self.fd, k.encode())
            self.pump(0.05)
        self.pump(settle)

    def type(self, text):
        self.press(*list(text))

    def log(self) -> list[dict]:
        p = self.fake.with_suffix(".log")
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def close(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(self.fd)


@pytest.fixture
def console(tmp_path):
    made = []

    def make(machine=None, **kw):
        c = Console(tmp_path, copy.deepcopy(machine or MACHINE), **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def step(c, n):
    """Wait for step n, in whichever language the screens are in."""
    end = time.time() + 10
    while time.time() < end:
        c.pump(0.1)
        if any(f"{word} {n} " in c.text for word in ("Step", "Stap", "Étape")):
            return
    raise AssertionError(f"step {n} didn't appear:\n{c.text}")


def to_disks(c):
    c.press(TAB, TAB, TAB, TAB, ENTER)  # language, keyboard, test field, time zone, Next
    step(c, 2)


def to_models(c):
    to_disks(c)
    c.press(ENTER)
    step(c, 3)
    c.wait("suveryn-bundles")


def to_network(c):
    to_models(c)
    c.press(ENTER)
    step(c, 4)


def to_summary(c, name="notary-brussels-01"):
    to_network(c)
    c.press(TAB)
    c.type(name)
    c.wait("Received 192.168.1.50/24")
    c.press(ENTER)
    c.wait("Ready to install")


# ---------------------------------------------------------------------------- C1
def test_c1_language_keyboard_time_zone(console):
    c = console()
    s = c.text
    assert "Sūveryn installer" in s and "2026.10 · Step 1 of 6" in s
    assert "(*) English" in s and "( ) Nederlands" in s and "( ) Français" in s
    assert "Belgian (AZERTY)" in s and "Europe/Brussels" in s and "Check a few letters" in s
    c.press(DOWN)  # Nederlands
    assert "(*) Nederlands" in c.text
    c.press(UP)  # back to English
    c.press(TAB, ENTER)  # open the keyboard list
    c.type("swiss")
    assert "Swiss French" in c.text and "Belgian" not in c.text.split("Type to search")[1]
    c.press(ENTER)
    assert "[ Swiss French" in c.text
    assert {"keyboard": "ch:fr"} in c.log()  # loaded on the console at once


def test_c1_help(console):
    c = console()
    c.press(F1)
    assert "Help" in c.text and "layout of the keyboard" in c.text and "Ctrl+Alt+Del" in c.text
    c.press(ESC)
    assert "layout of the keyboard" not in c.text


# ---------------------------------------------------------------------------- C2
def test_c2_hardware_and_disks(console):
    c = console()
    to_disks(c)
    s = c.text
    assert "√ Graphics" in s and "NVIDIA GeForce RTX 4090 · 24 GB" in s and "licensed for on-premise office use" in s
    assert "√ Processor" in s and "16 cores" in s and "√ Security" in s and "TPM 2.0" in s
    assert "! Memory" in s and "Recommended: 64 GB" in s and "You can continue" in s
    assert "(*) nvme0n1" in s and "Samsung 990 PRO" in s and "2.0 TB" in s
    assert "too small" in s.split("sda")[1].splitlines()[0]
    assert "sdb" in s and "installation medium" in s
    assert "Everything on the selected disk will be erased" in s
    c.press(DOWN, DOWN)  # nvme1n1, then past the disabled sda back to nvme0n1
    assert "(*) nvme0n1" in c.text


@pytest.mark.parametrize("change, message", [
    (lambda m: m["hardware"].update(tpm=False), "No TPM 2.0 found"),
    (lambda m: m["hardware"].update(gpu=None), "No NVIDIA graphics card found"),
    (lambda m: m.update(disks=[d for d in m["disks"] if d["name"] in ("sda", "sdb")]), "No disk to install onto"),
])
def test_c2_what_stops_the_installation(console, change, message):
    m = copy.deepcopy(MACHINE)
    change(m)
    c = console(m)
    to_disks(c)
    c.press(ENTER)
    assert message in c.text and "Hardware and target disk" in c.text


def test_c2_a_machine_without_gpu_continues_when_allowed(console):
    m = copy.deepcopy(MACHINE)
    m["hardware"]["gpu"] = None
    c = console(m, env={"SUVERYN_INSTALLER_ALLOW_NO_GPU": "1"})
    to_disks(c)
    c.press(ENTER)
    c.wait("Language models")


# ---------------------------------------------------------------------------- C3
def test_c3_models(console):
    c = console()
    to_models(c)
    s = c.text
    assert "Partition suveryn-bundles on sdb" in s and "Signed by Sūveryn Labs" in s and "0f61e8d1aa3bfa1a" in s
    assert "[x]  Qwen3.8-27B" in s and "default · 17.0 GB" in s and "Fewest legal errors" in s
    assert "[ ]  Mistral Small 3.2 24B" in s and "optional · 15.0 GB" in s
    assert "[-]  third" in s and "! skipped" in s and "Signature not valid." in s and "Ask your partner" in s
    assert "1 model · 17.0 GB of 2.0 TB on nvme0n1" in s and "24 GB · one model active at a time" in s
    c.press(DOWN, SPACE)
    assert "[x]  Mistral" in c.text and "2 models · 32.0 GB" in c.text
    c.press(UP, SPACE, DOWN, SPACE)  # untick both
    c.press(ENTER)
    assert "Choose at least one model" in c.text


def test_c3_waits_for_the_check(console):
    m = copy.deepcopy(MACHINE)
    m["models_delay"] = 2
    c = console(m)
    to_disks(c)
    c.press(ENTER)
    c.wait("Checking the language models on this stick")
    c.wait("Found on this installation medium", timeout=6)


# ---------------------------------------------------------------------------- C4
def test_c4_network_dhcp(console):
    c = console()
    to_network(c)
    s = c.text
    assert "(*) eno1" in s and "connected · 1 Gbit/s" in s and "eno2" in s and "no cable" in s
    c.wait("Received 192.168.1.50/24")
    assert "gateway 192.168.1.1 · DNS 192.168.1.1" in c.text and "reserve this address" in c.text
    c.press(TAB)
    c.type("Notary_1")
    assert "Reachable as Notary_1.local" in c.text
    c.press(ENTER)
    assert "letters, digits and hyphens" in c.text.lower() and "Network" in c.text
    c.press(*[BACKSPACE] * 8)
    c.type("notary-1")
    c.press(ENTER)
    c.wait("Ready to install")


def test_c4_fixed_address(console):
    m = copy.deepcopy(MACHINE)
    m["dhcp"] = {}
    c = console(m)
    to_network(c)
    c.press(TAB)
    c.type("office")
    c.press(TAB, DOWN)  # Fixed address
    s = c.text
    assert "(*) Fixed address" in s and "IP address" in s and "Gateway" in s and "DNS server" in s
    assert "free address in your office network" in s
    c.press(TAB)
    c.type("192.168.1.50")
    c.press(ENTER)
    assert "network prefix" in c.text
    c.press(*[BACKSPACE] * 12)
    c.type("192.168.1.50/24")
    c.press(TAB)
    c.type("10.0.0.1")
    c.press(TAB)
    c.type("192.168.1.1")
    c.press(ENTER)
    assert "isn't in 192.168.1.0/24" in c.text
    c.press(UP)
    c.press(*[BACKSPACE] * 8)
    c.type("192.168.1.1")
    c.press(ENTER)
    c.wait("Ready to install")
    assert "192.168.1.50/24 · fixed" in c.text


def test_c4b_no_dhcp_after_30_seconds(console, monkeypatch):
    m = copy.deepcopy(MACHINE)
    m["dhcp"] = {}
    c = console(m)
    to_network(c)
    c.wait("No address received from DHCP after 30 seconds", timeout=40)
    assert "(*) Fixed address" in c.text and "< Retry DHCP >" in c.text and "Check the cable" in c.text


# ---------------------------------------------------------------------------- C5 to C7
def test_c5_summary_needs_erase(console):
    c = console()
    to_summary(c)
    s = c.text
    for want in ("Belgian (AZERTY)", "Europe/Brussels", "nvme0n1 · Samsung 990 PRO · 2.0 TB", "LUKS2 · unlocks with",
                 "notary-brussels-01", "192.168.1.50/24 · DHCP", "Qwen3.8-27B", "32 GB, below the recommended 64 GB",
                 "All data on nvme0n1 will be permanently erased", "Type ERASE to confirm"):
        assert want in s, want
    c.type("erase")
    c.press(ENTER)
    assert "Type ERASE in capitals" in c.text
    assert not [e for e in c.log() if "install" in e]
    c.press(ESC)
    c.wait("Interface")


def test_install_recovery_key_and_restart(console):
    c = console()
    to_summary(c)
    c.type("ERASE")
    c.press(ENTER)
    c.wait("Installing the sūveryn appliance")
    c.wait("Write down the disk recovery key", timeout=10)
    install = next(e["install"] for e in c.log() if "install" in e)
    assert install == ["--disk", "/dev/nvme0n1", "--name", "notary-brussels-01", "--language", "en", "--keyboard", "be",
                       "--timezone", "Europe/Brussels", "--interface", "eno1", "--model", "qwen"]
    s = c.text
    assert "fjkcbvgt-lrhnedui-tbcvkjfg-hnulrdei" in s and "cgtbvfkj-rhnlduei-btcgkfvj-unhrlide" in s
    assert "only time it is shown" in s and "Remove the installation medium" in s
    c.press(ENTER)
    assert "Type the first group" in c.text and not [e for e in c.log() if "reboot" in e]
    c.press(ESC)
    assert "Write down the disk recovery key" in c.text  # no way back from here
    c.type("fjkcbvgt")
    assert "√ matches" in c.text
    c.press(ENTER, settle=0.5)
    assert {"reboot": True} in c.log()


def test_a_failed_installation_says_so(console):
    m = copy.deepcopy(MACHINE)
    m["install"] = {"lines": ["install: checking qwen", "install: the bundle qwen isn't valid: model.gguf: its SHA-256 "
                              "doesn't match the manifest"], "exit": 2, "result": {}}
    c = console(m)
    to_summary(c)
    c.type("ERASE")
    c.press(ENTER)
    c.wait("The installation failed")
    s = " ".join(c.text.replace("│", " ").split())
    assert "! Checking the language models" in s and "The bundle qwen isn't valid" in s
    assert "installing again erases it anew" in s and "Enter Back to the summary" in s
    c.press(ENTER)
    c.wait("Ready to install")


def test_it_fits_an_80_by_25_console(console):
    c = console(cols=80, rows=25)
    to_models(c)
    assert "Found on this installation medium" in c.text and "< Next >" in c.text


# ---------------------------------------------------------------------------- Dutch and French
TEXT = json.loads((LIB / "installer-text.json").read_text())


def test_every_text_is_in_all_three_languages():
    import string
    for key, entry in TEXT.items():
        if key.startswith("_"):
            continue
        assert set(entry) == {"en", "nl", "fr"}, key
        fields = {lang: {f for _, f, _, _ in string.Formatter().parse(v) if f} for lang, v in entry.items()}
        assert fields["nl"] == fields["en"] == fields["fr"], key  # the same {values} filled in


def test_the_house_style():
    """No em dashes; formal Dutch (u, uw) and French (vous); sūveryn lower case mid-sentence."""
    import re
    for key, entry in TEXT.items():
        if key.startswith("_"):
            continue
        for lang, v in entry.items():
            assert "—" not in v, (key, lang)
            assert not re.search(r"(?<!^)(?<![.:]\s)Sūveryn(?! Labs)(?!-installatie)(?! installer)", v) or \
                key in ("c2_stick", "installer"), (key, lang, v)
        assert not re.search(r"\b(je|jij|jouw|jullie)\b", entry["nl"], re.I), key
        assert not re.search(r"\b(tu|ton|ta|tes|toi)\b", entry["fr"], re.I), key
    assert TEXT["c3_signed"]["nl"].startswith("Ondertekend door Sūveryn Labs")


def test_the_screens_switch_language_at_once(console):
    c = console()
    c.press(DOWN)  # Nederlands
    s = c.text
    assert "Taal, toetsenbord en tijdzone" in s and "Stap 1 van 6" in s and "Belgisch (AZERTY)" in s
    assert "Hiermee wordt deze machine een sūveryn-appliance" in s and "< Volgende >" in s and "Kiezen" in s
    c.press(DOWN)  # Français
    s = c.text
    assert "Langue, clavier et fuseau horaire" in s and "Étape 1 sur 6" in s and "Belge (AZERTY)" in s
    assert "Cette machine devient ainsi une appliance sūveryn" in s and "< Suivant >" in s


def test_installing_in_french(console):
    c = console()
    c.press(DOWN, DOWN)  # Français
    to_disks(c)
    s = c.text
    assert "Matériel et disque cible" in s and "Carte graphique" in s and "16 cœurs" in s
    assert "32 Go. Recommandé : 64 Go." in s and "2,0 To" in s and "trop petit" in s
    c.press(ENTER)
    c.wait("Trouvés sur ce support d'installation")
    s = c.text
    assert "Signé par Sūveryn Labs" in s and "par défaut · 17,0 Go" in s and "Signature non valide." in s
    assert "Le moins d'erreurs juridiques" in s  # the manifest's French description
    assert "20 to 40% faster" in s  # no French one: the English
    c.press(ENTER)
    c.wait("Réseau")
    c.press(TAB)
    c.type("bureau-1")
    c.wait("Reçue : 192.168.1.50/24")
    c.press(ENTER)
    c.wait("Prêt pour l'installation")
    s = c.text
    assert "Tapez EFFACER pour confirmer" in s and "Toutes les données de nvme0n1 seront définitivement effacées" in s
    c.type("ERASE")
    c.press(ENTER)
    assert "Tapez EFFACER en majuscules pour confirmer." in c.text
    c.press(*[BACKSPACE] * 5)
    c.type("EFFACER")
    c.press(ENTER)
    c.wait("Notez la clé de récupération du disque")
    install = next(e["install"] for e in c.log() if "install" in e)
    assert install[install.index("--language") + 1] == "fr"
    assert "Tapez le premier groupe pour confirmer" in c.text


def test_dutch_network_errors(console):
    m = copy.deepcopy(MACHINE)
    m["dhcp"] = {}
    c = console(m)
    c.press(DOWN)  # Nederlands
    to_network(c)
    c.press(TAB)
    c.type("kantoor")
    c.press(TAB, DOWN, TAB)  # Vast adres, IP-adres
    c.type("192.168.1.50")
    c.press(ENTER)
    assert "Vul het adres in met de netwerkprefix" in c.text
    c.press(*[BACKSPACE] * 12)
    c.type("192.168.1.50/24")
    c.press(ENTER)
    assert "Een vast adres heeft een DNS-server nodig." in c.text


# ---------------------------------------------------------------------------- as designed (setup check of 21 October)
def _ui_module(monkeypatch):
    import importlib.machinery
    import importlib.util
    monkeypatch.setenv("SUVERYN_LIB", str(LIB))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # nothing written into the image tree
    loader = importlib.machinery.SourceFileLoader("installer_ui", str(UI))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def test_french_punctuation_never_starts_a_line(monkeypatch):
    """K9: "navigateur :" wrapped with the colon alone on the next line."""
    ui = _ui_module(monkeypatch)
    for width in range(20, 90):
        c = ui.Canvas(width)
        c.add(("Sur un autre ordinateur de ce réseau, ouvrez l'une de ces adresses dans un navigateur :", "text"))
        c.wrap("Votre navigateur signale le certificat une fois. Vérifiez que le navigateur affiche cette empreinte : oui ?")
        for line in c.lines:
            text = "".join(t for t, _ in line).strip()
            assert not text or text[0] not in ui.NO_BREAK_BEFORE, (width, text)


def test_a_text_field_lines_up_with_a_dropdown_and_the_key_is_framed(console):
    """K5 and K6: "]" of Test it under "]" of Keyboard; the recovery key in a frame, its match on the field's line."""
    c = console(cols=160, rows=50)
    lines = c.text.splitlines()
    keyboard = next(x for x in lines if "Belgian (AZERTY)" in x)
    test_it = next(x for x in lines if "type here to check" in x)
    assert keyboard.rindex("]") == test_it.rindex("]")
    to_summary(c)
    c.type("ERASE")
    c.press(ENTER)
    c.wait("Write down the disk recovery key", timeout=10)
    c.type("fjkcbvgt")
    lines = c.text.splitlines()
    first = next(i for i, x in enumerate(lines) if "fjkcbvgt-lrhnedui" in x)
    assert "┌" in lines[first - 1] and "└" in lines[first + 2] and lines[first].count("│") >= 3
    assert any("[ fjkcbvgt" in x and "√ matches" in x for x in lines)
