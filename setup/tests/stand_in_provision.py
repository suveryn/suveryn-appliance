#!/usr/bin/env python3
"""A stand-in for `suveryn-provision first-boot`, for the browser check (browser_check.py) on a machine
without a GPU, PostgreSQL or Keycloak. It takes the same arguments, prints the same "== step" lines
at a readable pace and does the real web part: the TLS step and the real nginx site (provision's own
tls() and reverse_proxy()), so the check sees setup survive the switch to the real site. Never on an
appliance.

    SUVERYN_PROVISION=setup/tests/stand_in_provision.py   (STAND_IN_FAIL_AT=Keycloak to fail there once)
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("command")
ap.add_argument("--public-url", required=True)
ap.add_argument("--network-mode", required=True)
ap.add_argument("--outbound")
ap.add_argument("--site", required=True)
ap.add_argument("--administrator", type=Path, required=True)
ap.add_argument("--models-from")
a = ap.parse_args()
admin = json.loads(a.administrator.read_text())
assert a.command == "first-boot" and len(admin["password"]) >= 15 and admin["email"]

loader = importlib.machinery.SourceFileLoader("provision", str(REPO / "provision" / "suveryn-provision"))
spec = importlib.util.spec_from_loader("provision", loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)
host = a.public_url.split("://", 1)[1]
flag = Path("/run/suveryn/stand-in-failed-once")
for title in ("GPU licence check", "Network mode", "PostgreSQL 16 + pgvector",
              "Keycloak: host name, secrets and the production realm", "Model files (suveryn-core models/manifest.json)"):
    p.step(title)
    p.note(f"(stand-in) {title}")
    time.sleep(0.8)
    if os.environ.get("STAND_IN_FAIL_AT") and title.startswith(os.environ["STAND_IN_FAIL_AT"]) and not flag.exists():
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        p.fail("Keycloak didn't become ready (stand-in failure)")
p.tls(host, None, None)
p.reverse_proxy(host)
p.start_nginx()
p.step("Verify")
time.sleep(0.8)
print("   OK   (stand-in)", flush=True)
sys.exit(0)
