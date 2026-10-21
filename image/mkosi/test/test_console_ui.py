"""The installed appliance's console (mkosi.extra/usr/lib/suveryn/console-ui; #37, C8), driven with keys in
a pseudo-terminal and read with pyte, with a JSON file standing in for the machine:

    python3 -m pytest image/mkosi/test/test_console_ui.py
"""

import copy
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("pyte")
from test_installer_ui import Console as Terminal, DOWN, ENTER, ESC, TAB, BACKSPACE  # noqa: E402

LIB = Path(__file__).resolve().parent.parent / "mkosi.extra" / "usr" / "lib" / "suveryn"
CONSOLE = LIB / "console-ui"

MACHINE = {
    "interfaces": [{"name": "eno1", "mac": "52:54:00:12:34:56", "model": "Intel I226-V", "carrier": True, "speed": 1000}],
    "dhcp": {"eno1": {"address": "192.168.1.50/24", "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}},
    "status": {
        "version": "2026.10", "hostname": "notary-brussels-01", "tty": "tty1",
        "ports": [{"name": "eno1", "mac": "52:54:00:12:34:56", "model": "Intel I226-V", "carrier": True, "speed": 1000,
                   "addresses": ["192.168.1.50/24"], "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}],
        "fixed": False, "unlocked_by": "tpm", "tpm_again": False, "setup_complete": False,
        "fingerprint": "4F:9A:1C:E2:07:B3:58:D6:21:AF:90:3C:7E:55:C1:08:B2:6D:F4:19:83:0A:E7:42:5B:CC:61:9E:D0:37:2A:F5",
        "identity": {"uuid": "7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41",
                     "fingerprint": "cf79c57e87d030fc85927cae01a0cd47dc551f75234ea29943fb148408fe8a16"},
        "installed": "2026-10-12T12:00:00Z", "language": "en", "manifest": None, "timezone": "Europe/Brussels",
        "now": 1791807120,
    },
}


class ConsoleTerminal(Terminal):
    def __init__(self, tmp, machine, **kw):
        import fcntl, os, pty, struct, sys, termios  # noqa: E401
        import pyte
        self.fake = tmp / "machine.json"
        self.fake.write_text(json.dumps(machine))
        cols, rows = kw.get("cols", 100), kw.get("rows", 40)
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.environ.update(TERM="linux", COLUMNS=str(cols), LINES=str(rows), SUVERYN_CONSOLE_FAKE=str(self.fake),
                              SUVERYN_LIB=str(LIB), TZ="Europe/Brussels")
            os.execv(sys.executable, [sys.executable, str(CONSOLE)])
        self.wait("Choose an option" if machine["status"]["language"] == "en" else "1  ")


@pytest.fixture
def console(tmp_path):
    made = []

    def make(change=None, **kw):
        m = copy.deepcopy(MACHINE)
        if change:
            change(m)
        c = ConsoleTerminal(tmp_path, m, **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def flat(c) -> str:
    return " ".join(c.text.replace("│", " ").split())


def test_c8_waiting_for_setup(console):
    c = console()
    s = flat(c)
    assert "Sūveryn 2026.10 · notary-brussels-01" in s and "tty1" in s
    assert "Installation is complete. Setup is waiting for you." in s
    assert "› https://notary-brussels-01.local" in s and "› https://192.168.1.50" in s
    assert "check that the browser shows this fingerprint" in s and "SHA-256 4F:9A:1C:E2:07:B3:58:D6" in s
    assert "eno1 · 192.168.1.50/24 · DHCP √ connected" in s and "encrypted · unlocked by TPM √" in s
    assert "open to this network until setup is finished" in s and "7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41" in s
    assert "no valid manifest · banner for everyone in" in s and "Europe/Brussels" in s
    for item in ("1 Show network details", "2 Change network settings", "3 Show the registration QR code",
                 "4 Restart", "5 Shut down"):
        assert item in s, item


def test_c8_setup_complete_and_recovery(console):
    c = console(lambda m: m["status"].update(setup_complete=True, unlocked_by="recovery-key", tpm_again=True))
    s = flat(c)
    assert "The appliance is running." in s and "Setup is complete and no longer accepts connections." in s
    assert "unlocked with the recovery key, linked to the TPM again" in s and "fingerprint" not in s.lower()


def test_network_details_and_qr(console):
    c = console()
    c.press("1")
    s = flat(c)
    assert "Network details" in s and "notary-brussels-01.local" in s and "52:54:00:12:34:56" in s
    assert "Gateway 192.168.1.1" in s
    c.press(" ")
    c.wait("Choose an option")
    c.press("3")
    s = flat(c)
    assert "Scan this code with a phone" in s and "█▀▀▀█" in s
    qr = next(e["qr"] for e in c.log() if "qr" in e)
    assert qr == "https://portal.suveryn.com/r/7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41?k=cf79c57e87d030fc"


def test_change_network_settings(console):
    c = console()
    c.press("2")
    c.wait("Change network settings")
    assert "notary-brussels-01" in c.text and "< Apply >" in c.text
    c.press(TAB)
    c.press(*[BACKSPACE] * 2)
    c.type("02")
    c.press(ENTER)
    c.wait("Choose an option")
    change = next(e["apply_network"] for e in c.log() if "apply_network" in e)
    assert change["name"] == "notary-brussels-02" and "DHCP=yes" in change["network"]
    assert "MulticastDNS=yes" in change["network"] and "MACAddress=52:54:00:12:34:56" in change["network"]
    assert "notary-brussels-02.local" in c.text


def test_change_network_back_changes_nothing(console):
    c = console()
    c.press("2")
    c.wait("Change network settings")
    c.press(ESC)
    c.wait("Choose an option")
    assert not [e for e in c.log() if "apply_network" in e]


def test_restart_and_shut_down_ask_first(console):
    c = console()
    c.press("4")
    assert "Restart the appliance?" in flat(c)
    c.press(ESC)
    c.wait("Choose an option")
    assert not [e for e in c.log() if "power" in e]
    c.press("5")
    assert "Shut down the appliance?" in flat(c)
    c.press(ENTER, settle=0.5)
    assert {"power": "poweroff"} in c.log()


def test_in_dutch(console):
    c = console(lambda m: m["status"].update(language="nl"))
    c.wait("Kies een optie")
    s = flat(c)
    assert "De installatie is voltooid. De configuratie wacht op u." in s and "versleuteld · ontgrendeld door de TPM" in s
    assert "Netwerkgegevens tonen" in s and "Uitschakelen" in s
    assert "dinsdag" in s or "maandag" in s or "zondag" in s  # the date in Dutch


@pytest.mark.parametrize("language", ["en", "nl", "fr"])
def test_c8_fits_a_25_line_console_in_every_language(console, language):
    """Pod check of f0ff96c: in Dutch and French the menu ran over the bottom border and the prompt was gone."""
    c = console(lambda m: m["status"].update(language=language), cols=80, rows=25)
    c.pump(1.0)
    lines = c.text.splitlines()
    bottom = next(i for i, x in enumerate(lines) if "└" in x)
    assert set(lines[bottom].strip().strip("└┘")) == {"─"}  # nothing drawn over the border
    prompt = {"en": "Choose an option:", "nl": "Kies een optie:", "fr": "Choisissez une option :"}[language]
    assert any(prompt in x and "█" in x for x in lines[:bottom])


def test_the_disk_unlock_screen_wraps_at_word_boundaries_and_has_its_bars(tmp_path):
    """C8b at 80 columns (Pod check of f0ff96c): lines broke inside words ("opt / ional"), no bottom bar."""
    import os
    import re
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "mkosi.images" / "initrd" / "extra" / "usr" / "lib" / "suveryn" / "unlock-screen"
    text = script.read_text()
    body = text[text.index("A=$'"):text.index("read_key() {")]
    body = "\n".join(x for x in body.splitlines() if not x.startswith(("cols=", '[ "$cols"', "version=")))
    harness = tmp_path / "draw.sh"
    harness.write_text("TRIES=5; version=2026.10; cols=80\n" + body +
                       '\ndraw 2 "That key doesn\'t open this disk. Check it against the sheet and try again."\n')
    out = subprocess.run(["bash", str(harness)], capture_output=True, text=True, env={**os.environ, "LINES": "25"},
                         check=True).stdout
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "\n", out)
    known = set(re.findall(r"\w+", text))  # every word the script writes, whole
    for line in plain.splitlines():
        assert len(line) <= 79, line  # never into the last column, where the console would wrap it itself
        for w in re.findall(r"\w+", line):
            assert w in known or w.isdigit(), (w, line)  # a word broken in two isn't one of them
    assert "\x1b[25;1H\x1b[45m" in out and "Ctrl+Alt+Del" in out  # the bottom bar, with the keys
