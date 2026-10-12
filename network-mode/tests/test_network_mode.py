"""suveryn-network-mode: one provisioning-time value any component can read (development context, open decision 7).

Runs the tool itself, against a file in a temporary folder (SUVERYN_NETWORK_MODE_FILE). Any machine:

    python3 -m pytest network-mode/tests
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "suveryn-network-mode"


def run(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], check=False, capture_output=True, text=True,
                          env={**os.environ, "SUVERYN_NETWORK_MODE_FILE": str(path)})


@pytest.mark.parametrize("mode", ["connected", "outbound-blocked", "air-gapped"])
def test_a_mode_set_at_provisioning_is_read_back(tmp_path, mode):
    path = tmp_path / "etc" / "suveryn" / "network-mode.env"
    assert run(path, "set", mode).returncode == 0
    r = run(path, "get")
    assert (r.returncode, r.stdout.strip()) == (0, mode)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # any component may read it; it holds no secret
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".network-mode.")]  # written atomically


def test_the_file_is_a_valid_environment_file_and_shell(tmp_path):
    """What systemd's EnvironmentFile= and a shell's `.` give a component: the variable, nothing else."""
    path = tmp_path / "network-mode.env"
    run(path, "set", "outbound-blocked")
    out = subprocess.run(["/bin/sh", "-c", f'. "{path}"; printf %s "$SUVERYN_NETWORK_MODE"'], check=False,
                         capture_output=True, text=True, env={})
    assert out.stdout == "outbound-blocked"
    assignments = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    assert assignments == ["SUVERYN_NETWORK_MODE=outbound-blocked"]


def test_changing_the_mode_replaces_it(tmp_path):
    path = tmp_path / "network-mode.env"
    run(path, "set", "connected")
    run(path, "set", "air-gapped")
    assert run(path, "get").stdout.strip() == "air-gapped"


def test_a_value_that_isnt_a_mode_is_refused_and_changes_nothing(tmp_path):
    path = tmp_path / "network-mode.env"
    run(path, "set", "connected")
    for wrong in ("airgapped", "Air-Gapped", "offline", ""):
        r = run(path, "set", wrong)
        assert r.returncode == 2 and "not a mode" in r.stderr
    assert run(path, "get").stdout.strip() == "connected"


@pytest.mark.parametrize("content", [
    None,                                                                      # never provisioned
    "SUVERYN_NETWORK_MODE=offline\n",                                          # not one of the three
    "SUVERYN_NETWORK_MODE=air-gapped\nSUVERYN_NETWORK_MODE=connected\n",       # two values: which one?
    "# SUVERYN_NETWORK_MODE=air-gapped\n",                                     # only a comment
    "SUVERYN_NETWORK_MODE\n",                                                  # no value
])
def test_anything_but_exactly_one_valid_mode_is_unknown(tmp_path, content):
    """Unknown is never air-gapped: a component must not claim isolation it can't vouch for."""
    path = tmp_path / "network-mode.env"
    if content is not None:
        path.write_text(content)
    r = run(path, "get")
    assert (r.returncode, r.stdout.strip()) == (1, "unknown")
    assert "set one with" in r.stderr


def test_usage(tmp_path):
    for args in ([], ["get", "x"], ["set"], ["show"]):
        assert run(tmp_path / "f", *args).returncode == 2
