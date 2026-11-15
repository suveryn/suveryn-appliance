"""The appliance's services say suveryn-core's codes and keep no list of their own (suveryn-tracker#141, #154):
messages/suveryn-messages loads core's module, and every code a service names is one core's registry lists.

    SUVERYN_CORE=../suveryn-core python3 -m pytest messages/tests

Needs a suveryn-core checkout beside this one (or SUVERYN_CORE) for the registry; the test of a missing core doesn't.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "messages" / "suveryn-messages"
CORE = Path(os.environ.get("SUVERYN_CORE", REPO.parent / "suveryn-core"))
CORE_MESSAGES = CORE / "packages" / "harness" / "src" / "suveryn_harness" / "messages.py"
needs_core = pytest.mark.skipif(not CORE_MESSAGES.exists() or '"backup"' not in CORE_MESSAGES.with_suffix(".json").read_text(encoding="utf-8"),
                                reason=f"no suveryn-core with the backup service's messages at {CORE} (set SUVERYN_CORE)")
# The services that say codes: the web setup, the backup service, the clock's helper.
SERVICES = [REPO / "setup" / "suveryn-setup", REPO / "backup" / "suveryn-backup", REPO / "clock" / "suveryn-clock"]


def said_by(code_path: Path, snippet: str) -> str:
    """What a fresh Python prints for the snippet, with suveryn-messages loaded as ``m`` and core at code_path."""
    program = ("import importlib.machinery, importlib.util, json\n"
               f"loader = importlib.machinery.SourceFileLoader('suveryn_messages', {str(TOOL)!r})\n"
               "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('suveryn_messages', loader))\n"
               "loader.exec_module(m)\n" + snippet)
    r = subprocess.run([sys.executable, "-I", "-c", program], env={**os.environ, "SUVERYN_CORE_MESSAGES": str(code_path)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@needs_core
def test_a_code_is_said_as_cores_registry_has_it():
    out = json.loads(said_by(CORE_MESSAGES, "print(json.dumps([m.said('backup.disk_full'), m.said('clock.synced'), m.reference()]))"))
    assert out[0] == {"kind": "unavailable", "code": "backup.disk_full", "params": {}}
    assert out[1] == {"kind": "refused", "code": "clock.synced", "params": {}}
    assert re.fullmatch(r"[0-9A-Z]{4}-[0-9A-Z]{4}", out[2])


def test_without_core_a_helper_still_says_its_code(tmp_path):
    # a backup must run whatever the state of /opt/suveryn/core: the code goes out as it is, and the gateway reads
    # it against its own registry (core's messages.stored)
    out = json.loads(said_by(tmp_path / "no-core" / "messages.py",
                             "print(json.dumps([m.said('backup.disk_full'), m.load() is None, bool(m.problem_loading), bool(m.reference())]))"))
    assert out == [{"code": "backup.disk_full", "params": {}}, True, True, True]


@needs_core
def test_every_code_a_service_names_is_one_the_registry_lists():
    registry = json.loads(CORE_MESSAGES.with_suffix(".json").read_text(encoding="utf-8"))
    areas = "|".join(map(re.escape, registry["areas"]))
    for service in SERVICES:
        named = set(re.findall(rf'"((?:{areas})\.[a-z_]+)"', service.read_text(encoding="utf-8")))
        named -= {"request.json"}  # a file's name, not a code
        assert named, service.name
        unknown = sorted(named - set(registry["codes"]))
        assert not unknown, f"{service.name} names codes suveryn-core's registry doesn't list: {unknown}"


def test_no_service_keeps_a_word_of_its_own_for_a_failure():
    # the words the helpers once answered with; a failure is `failure`, a message, never `step` or `error`
    for service in SERVICES[1:]:
        text = service.read_text(encoding="utf-8")
        assert not re.search(r'"step": e\.|(?<![a-z_])error=|"errors":|\.step\b', text), service.name
