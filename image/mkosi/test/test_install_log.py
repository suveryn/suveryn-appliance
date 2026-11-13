"""The installer's log (install-log, suveryn-tracker#144): one folder per run on the stick's logs partition,
written as it goes, the last runs kept, and never a secret or a serial number in it.

    python3 -m pytest image/mkosi/test/test_install_log.py

The console's side (the reference on the screen, the folder a whole installation leaves) is in
test_installer_ui.py; the stick's partition in test_make_stick.py.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "mkosi.extra" / "usr" / "lib" / "suveryn"
KEY = "fjkcbvgt-lrhnedui-tbcvkjfg-hnulrdei-cgtbvfkj-rhnlduei-btcgkfvj-unhrlide"


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setenv("SUVERYN_RUN", str(tmp_path / "run"))
    monkeypatch.delenv("SUVERYN_INSTALL_LOG", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # nothing written into the image tree
    loader = importlib.machinery.SourceFileLoader("install_log_under_test", str(LIB / "install-log"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    yield mod
    os.environ.pop("SUVERYN_INSTALL_LOG", None)


def everything(folder: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in folder.iterdir())


def test_a_run_gets_a_folder_named_by_counter_clock_and_reference(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    assert re.fullmatch(r"0001_\d{8}-\d{4}_[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}", log.name)
    assert log.reference == log.name[-9:] and log.persistent
    s = json.loads((log.folder / "summary.json").read_text())
    assert s["reference"] == log.reference and s["run"] == 1 and s["result"] == "started" and s["kept_on_the_stick"]
    assert os.environ["SUVERYN_INSTALL_LOG"] == str(log.folder)  # the tools the screens start write to the same run
    second = tool.Log.open(tmp_path / "stick")
    assert second.name.startswith("0002_") and second.reference != log.reference  # the counter orders, whatever the clock says


def test_the_last_runs_are_kept_and_the_counter_goes_on(tool, tmp_path):
    root = tmp_path / "stick"
    names = [tool.Log.open(root).name for _ in range(tool.KEEP + 5)]
    kept = [p.name for p in tool.runs(root)]
    assert len(kept) == tool.KEEP and kept == names[-tool.KEEP:]
    (root / "holiday-photos").mkdir()  # anything else on the partition is left alone
    assert tool.Log.open(root).name.startswith(f"{tool.KEEP + 6:04d}_") and (root / "holiday-photos").is_dir()


def test_lines_commands_and_steps_are_written_as_they_happen(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    log.line("install", "checking qwen")
    log.step("1/4 partitioning /dev/nvme0n1")
    log.command("install-disk", ["systemd-repart", "--dry-run=no", "/dev/nvme0n1"], 1, 2.34, "Failed to open device\nNo such device")
    text = (log.folder / "install.log").read_text()
    assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ \+ +\d+\.\ds install: checking qwen$", text, re.M)
    assert "ran systemd-repart --dry-run=no /dev/nvme0n1  [exit 1, 2.3 s]" in text and "  | No such device" in text
    s = json.loads((log.folder / "summary.json").read_text())
    assert s["steps"][0]["step"] == "1/4 partitioning /dev/nvme0n1" and "at_seconds" in s["steps"][0]
    log.close("failed", exit=1)
    s = json.loads((log.folder / "summary.json").read_text())
    assert s["result"] == "failed" and s["exit"] == 1 and s["ended"] and "closed: failed" in (log.folder / "install.log").read_text()


def test_a_secret_commands_output_never_reaches_the_log(tool, tmp_path):
    """systemd-cryptenroll prints the recovery key: a command's standard output is never logged, and a secret
    command's standard error isn't either."""
    log = tool.Log.open(tmp_path / "stick")
    log.command("install-disk", ["systemd-cryptenroll", "--recovery-key", "/dev/nvme0n1p4"], 0, 1.0,
                f"A secret recovery key has been generated: {KEY}", secret=True)
    assert KEY not in everything(log.folder) and "ran systemd-cryptenroll --recovery-key" in everything(log.folder)


def test_a_secret_that_got_in_anyway_is_masked_and_the_summary_says_so(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    assert log.forbid(KEY) is False and "secret_masked" not in (log.folder / "summary.json").read_text()
    log.line("install", f"oops: {KEY}")
    log.line("install", f"and without its hyphens: {KEY.replace('-', '')}")
    assert log.forbid(KEY) is True
    text = everything(log.folder)
    assert KEY not in text and KEY.replace("-", "") not in text and "masked" in text
    assert json.loads((log.folder / "summary.json").read_text())["secret_masked"] is True


def test_serial_numbers_are_not_kept(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    log.hardware("== disks\nnvme0n1 Samsung 990 PRO\nusb 1-1: SerialNumber: 4C530001230512345678\nSerial No: S6Z2NJ0W123456\n")
    text = (log.folder / "hardware.txt").read_text()
    assert "4C530001230512345678" not in text and "S6Z2NJ0W123456" not in text and "Samsung 990 PRO" in text
    assert tool.SERIAL.sub(r"\1(not kept)", "[    2.1] usb 2-1: SerialNumber: AA00123") == "[    2.1] usb 2-1: SerialNumber: (not kept)"


def test_when_the_logs_partition_cant_be_used_the_log_is_kept_in_memory_and_says_so(tool, tmp_path):
    assert tool.partition() is None and tool.mount() is None
    log = tool.Log.open()
    assert log.persistent is False and log.folder.parent == tool.MEMORY
    assert json.loads((log.folder / "summary.json").read_text())["kept_on_the_stick"] is False


def test_a_tool_started_by_the_screens_writes_to_their_run_and_one_started_by_hand_opens_its_own(tool, tmp_path, monkeypatch):
    screens = tool.Log.open(tmp_path / "stick")
    engine = tool.Log.attach()
    assert engine.folder == screens.folder
    engine.line("install", "from the engine")
    assert "from the engine" in (screens.folder / "install.log").read_text()
    monkeypatch.delenv("SUVERYN_INSTALL_LOG")
    assert tool.Log.attach().folder != screens.folder


def test_what_a_tool_prints_goes_to_the_console_and_to_the_log(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    out = io.StringIO()
    tee = tool.Tee(out, log, "install")
    print("step 1/4: partitioning", file=tee)
    tee.write("half a line")
    assert out.getvalue() == "step 1/4: partitioning\nhalf a line"
    text = (log.folder / "install.log").read_text()
    assert "install: step 1/4: partitioning" in text and "half a line" not in text  # a line is logged once it is whole


def test_a_stick_that_cant_be_written_to_never_stops_the_installer(tool, tmp_path):
    log = tool.Log.open(tmp_path / "stick")
    subprocess.run(["rm", "-rf", str(log.folder)])
    log.line("install", "nobody hears this")
    log.step("2/4")
    log.hardware("x")
    assert log.forbid(KEY) is False
    log.close("failed")
    blocked = tmp_path / "a-file"
    blocked.write_text("not a folder")
    assert tool.Log.open(blocked / "stick").folder is None  # and every method of that log does nothing


def test_the_engine_logs_every_command_but_never_the_recovery_key(tool, tmp_path, monkeypatch):
    """install-disk's enrol, with a stand-in for systemd-cryptenroll that prints the key as the real one does."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "systemd-cryptenroll").write_text(
        "#!/bin/sh\ncase \"$*\" in *--recovery-key*) echo 'A secret recovery key has been generated for this volume:' >&2; "
        f"echo '    {KEY}' >&2; echo '{KEY}';; *) echo 'New TPM2 token enrolled as key slot 1.' >&2;; esac\n")
    (bin_ / "systemd-cryptenroll").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    loader = importlib.machinery.SourceFileLoader("install_disk_under_test", str(LIB / "install-disk"))
    disk = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(disk)
    disk.LOG = tool.Log.open(tmp_path / "stick")
    key = tmp_path / "key"
    key.write_bytes(b"k" * 64)
    assert disk.enrol("/dev/nvme0n1p4", key) == KEY
    text = everything(disk.LOG.folder)
    assert KEY not in text and "recovery key has been generated" not in text
    assert text.count("ran systemd-cryptenroll") == 3 and "  | New TPM2 token enrolled as key slot 1." in text
    assert "--recovery-key /dev/nvme0n1p4  [exit 0" in text
