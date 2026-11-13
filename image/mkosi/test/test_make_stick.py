"""The stick's partitions (make-stick, suveryn-tracker#144): after the system come a small FAT partition for
the installer's logs, which any laptop reads, and the bundles partition, which the installer never writes to.

    sudo python3 -m pytest image/mkosi/test/test_make_stick.py

Needs root, sfdisk, losetup, mkfs.vfat, mount and mtools; skips without. The image here is a made-up one with
an EFI partition only: make-stick copies it block for block and doesn't look inside.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MKOSI = Path(__file__).resolve().parent.parent
LIB = MKOSI / "mkosi.extra" / "usr" / "lib" / "suveryn"
NEEDS = ("sfdisk", "losetup", "mkfs.vfat", "mount", "mdir", "mtype")
pytestmark = pytest.mark.skipif(os.geteuid() != 0 or any(not shutil.which(t) for t in NEEDS),
                                reason=f"needs root and {', '.join(NEEDS)}")


def sh(*cmd, **kw) -> str:
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True, **kw).stdout


@pytest.fixture
def stick(tmp_path):
    image = tmp_path / "image.raw"
    with open(image, "wb") as f:
        f.truncate(64 * 2**20)
    sh("sfdisk", "-q", image, input="label: gpt\nsize=32MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=esp\n")
    out = tmp_path / "stick.raw"
    r = subprocess.run([sys.executable, MKOSI / "make-stick", image, out, "--size", "400M", "--fs", "vfat", "--logs-size", "64M"],
                       capture_output=True, text=True)
    if r.returncode != 0 and "losetup" in r.stderr:
        pytest.skip(f"no loop devices here: {r.stderr.strip()[-120:]}")
    assert r.returncode == 0, r.stderr
    return out


def parts(stick: Path) -> dict:
    table = json.loads(sh("sfdisk", "--json", stick))["partitiontable"]
    return {p["name"]: {**p, "offset": p["start"] * table.get("sectorsize", 512), "bytes": p["size"] * table.get("sectorsize", 512)}
            for p in table["partitions"]}


def test_the_stick_has_a_logs_partition_any_laptop_reads_and_the_bundles_take_the_rest(stick):
    p = parts(stick)
    assert list(p) == ["esp", "suveryn-logs", "suveryn-bundles"]
    logs, bundles = p["suveryn-logs"], p["suveryn-bundles"]
    assert logs["bytes"] == 64 * 2**20 and logs["type"] == "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"  # shown by Windows, macOS and Linux
    assert bundles["start"] > logs["start"] and bundles["bytes"] > 250 * 2**20
    assert "SUVLOGS" in sh("mdir", "-i", f"{stick}@@{logs['offset']}", "::")      # FAT, by its label
    assert "SUVBUNDLES" in sh("mdir", "-i", f"{stick}@@{bundles['offset']}", "::")


def test_a_run_written_by_the_installer_is_read_back_from_the_stick_and_the_bundles_are_untouched(stick, tmp_path, monkeypatch):
    """The whole way: the logs partition as the initrd names it, mounted by install-log, a run written and
    forced to the stick, unmounted, then read from the raw stick as a laptop would."""
    p = parts(stick)

    def digest(part) -> str:
        with open(stick, "rb") as f:
            f.seek(part["offset"])
            return hashlib.sha256(f.read(part["bytes"])).hexdigest()
    before = digest(p["suveryn-bundles"])
    logs = p["suveryn-logs"]
    loop = sh("losetup", "--find", "--show", f"--offset={logs['offset']}", f"--sizelimit={logs['bytes']}", stick).strip()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "logs-partition").write_text(loop + "\n")
    monkeypatch.setenv("SUVERYN_RUN", str(run_dir))
    monkeypatch.delenv("SUVERYN_INSTALL_LOG", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = importlib.machinery.SourceFileLoader("install_log_on_a_stick", str(LIB / "install-log"))
    tool = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(tool)
    try:
        if tool.mount() is None:
            pytest.skip("the FAT partition can't be mounted here (no vfat in this kernel, or a container without the right)")
        log = tool.Log.open()
        assert log.persistent and log.folder.parent == tool.MOUNT
        log.line("install", "step 1/4: partitioning")
        log.close("failed", exit=1)
        name = log.name
    finally:
        tool.unmount()
        sh("losetup", "--detach", loop)
        os.environ.pop("SUVERYN_INSTALL_LOG", None)
    image = f"{stick}@@{logs['offset']}"
    assert "install: step 1/4: partitioning" in sh("mtype", "-i", image, f"::{name}/install.log")
    assert json.loads(sh("mtype", "-i", image, f"::{name}/summary.json"))["result"] == "failed"
    assert digest(p["suveryn-bundles"]) == before
