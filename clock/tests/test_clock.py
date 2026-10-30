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
    assert code == 0 and result["ok"] and result["moved_ms"] == -4200
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
