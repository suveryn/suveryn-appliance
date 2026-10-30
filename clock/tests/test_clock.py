"""suveryn-clock: the gateway's requests, within the bounds, without touching this machine's clock
(SUVERYN_CLOCK_TEST=1 records what would be done)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "suveryn-clock"


def ask(tmp_path: Path, request, synced: bool = False):
    requests, state = tmp_path / "requests", tmp_path / "state"
    requests.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    (requests / "request.json").write_text(request if isinstance(request, str) else json.dumps(request))
    env = {**os.environ, "SUVERYN_CLOCK_TEST": "1", "SUVERYN_CLOCK_TEST_SYNCED": "1" if synced else "0",
           "SUVERYN_CLOCK_REQUESTS": str(requests), "SUVERYN_CLOCK_STATE": str(state)}
    r = subprocess.run([sys.executable, "-I", str(TOOL), "request"], env=env, capture_output=True, text=True)
    assert not (requests / "request.json").exists()  # deleted whatever happens
    result = json.loads((state / "result.json").read_text()) if (state / "result.json").exists() else None
    return r.returncode, result, state


def read(state: Path, name: str):
    p = state / name
    return int(p.read_text()) if p.exists() else None


def test_a_nudge_slews_the_clock_back_by_what_it_is_ahead(tmp_path):
    code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": "nudge", "offset_ms": 4200})
    assert code == 0 and result["ok"] and result["moved_ms"] == -4200 and result["hardware_clock"] is True
    assert read(state, "test-slewed") == -4200 and read(state, "test-set") is None
    assert (state / "result.json").stat().st_mode & 0o777 == 0o644


def test_a_nudge_is_small_and_at_most_once_an_hour(tmp_path):
    for offset in (300, 61_000, -61_000):  # under half a second: nothing to do; over a minute: an administrator's
        code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": "nudge", "offset_ms": offset})
        assert code == 1 and result["error"] == "out_of_range" and read(state, "test-slewed") is None
    ask(tmp_path, {"id": "0123456789abcdef", "kind": "nudge", "offset_ms": -2000})
    code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": "nudge", "offset_ms": 3000})
    assert code == 1 and result["error"] == "too_soon" and read(state, "test-slewed") == 2000
    (state / "last-nudge").write_text(f"{time.time() - 3600:.0f}\n")
    code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": "nudge", "offset_ms": 3000})
    assert code == 0 and read(state, "test-slewed") == -3000


def test_an_administrators_set_moves_it_at_once_up_to_a_year(tmp_path):
    code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": "set", "offset_ms": -95_000})
    assert code == 0 and read(state, "test-set") == 95_000  # 95 s behind: forward
    code, result, _ = ask(tmp_path, {"id": "fedcba9876543210", "kind": "set", "offset_ms": 400 * 24 * 3600 * 1000})
    assert code == 1 and result["error"] == "out_of_range" and result["id"] == "fedcba9876543210"


def test_nothing_while_a_time_server_keeps_the_clock(tmp_path):
    for kind in ("nudge", "set"):
        code, result, state = ask(tmp_path, {"id": "0123456789abcdef", "kind": kind, "offset_ms": 5000}, synced=True)
        assert code == 1 and result["error"] == "synced"
    assert read(state, "test-slewed") is None and read(state, "test-set") is None


def test_a_request_the_gateway_doesnt_make_is_ignored(tmp_path):
    for bad in ("not json", {"id": "x", "kind": "set", "offset_ms": 1}, {"id": "0123456789abcdef", "kind": "set", "offset_ms": 1.5},
                {"id": "0123456789abcdef", "kind": "reboot", "offset_ms": 1}, "x" * 2000):
        code, result, state = ask(tmp_path, bad)
        assert code == 1 and read(state, "test-set") is None and read(state, "test-slewed") is None
        if result is not None:
            assert not result["ok"] and result["error"] == "invalid"
        (state / "result.json").unlink(missing_ok=True)


def test_a_clock_this_machine_may_not_set_is_an_answer_not_a_crash(monkeypatch):
    """Where setting the clock is refused (a container without CAP_SYS_TIME), the answer says so; before, the tool
    stopped with an exception and the page waited for an answer that never came (round 4b's brief)."""
    import importlib.machinery
    import importlib.util

    import pytest
    loader = importlib.machinery.SourceFileLoader("suveryn_clock", str(TOOL))
    clock = importlib.util.module_from_spec(importlib.util.spec_from_loader("suveryn_clock", loader))
    loader.exec_module(clock)
    monkeypatch.delenv("SUVERYN_CLOCK_TEST", raising=False)

    def refused(*_a):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(clock.time, "clock_settime", refused)
    with pytest.raises(clock.Refused) as e:
        clock.set_to(1000)
    assert e.value.code == "not_allowed" and e.value.detail == "clock_settime: Operation not permitted"  # a code the page words


def test_without_hwclock_the_correction_is_answered_and_said_to_last_until_a_restart(monkeypatch):
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("suveryn_clock", str(TOOL))
    clock = importlib.util.module_from_spec(importlib.util.spec_from_loader("suveryn_clock", loader))
    loader.exec_module(clock)
    monkeypatch.delenv("SUVERYN_CLOCK_TEST", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert clock.to_hardware_clock() is False


def test_the_answer_is_readable_by_the_gateway_whatever_the_umask(tmp_path):
    """Round 4b, finding 1: run by hand from a shell with umask 077, the state folder was made 0700 and the gateway
    couldn't read the answer; the page then said a set clock wasn't set."""
    requests, state = tmp_path / "requests", tmp_path / "state"
    requests.mkdir()
    (requests / "request.json").write_text(json.dumps({"id": "0123456789abcdef", "kind": "set", "offset_ms": 1000}))
    env = {**os.environ, "SUVERYN_CLOCK_TEST": "1", "SUVERYN_CLOCK_REQUESTS": str(requests), "SUVERYN_CLOCK_STATE": str(state)}
    r = subprocess.run(["sh", "-c", f"umask 077 && exec {sys.executable} -I {TOOL} request"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert state.stat().st_mode & 0o777 == 0o755 and (state / "result.json").stat().st_mode & 0o777 == 0o644
