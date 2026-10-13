"""The outgoing rules, for real: a resolver and a firewall in a small test network (tracker #38).

Two network namespaces stand in for the appliance and the world outside it:

    appliance 192.168.50.2 ── 192.168.50.1 office DNS and an office server (8443)
                              203.0.113.10  approved.test       (an approved destination)
                              203.0.113.11  sub.approved.test   (covered by approved.test)
                              203.0.113.20  outside.test        (not approved: must stay unreachable)
                              203.0.113.30  connector.test      (approved by the office during a test)

The tool's own ``apply`` loads the rules and writes the resolver's configuration; dnsmasq then runs
with it, as on the appliance. Needs Linux, root, ``ip``, ``nft`` and ``dnsmasq``; skipped otherwise:

    sudo python3 -m pytest network-mode/tests/test_egress.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "suveryn-network-mode"
APP, NET = "suv-test-app", "suv-test-net"
SERVER = """
import socket, threading
def serve(addr, port):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind((addr, port)); s.listen()
    while True:
        c, _ = s.accept(); c.close()
for a, p in [("203.0.113.10", 443), ("203.0.113.11", 443), ("203.0.113.20", 443), ("203.0.113.20", 80),
             ("203.0.113.30", 443), ("192.168.50.1", 8443)]:
    threading.Thread(target=serve, args=(a, p), daemon=True).start()
threading.Event().wait()
"""
PROBE = """
import errno, json, socket, sys
out = {}
for target in sys.argv[1:]:
    host, port = target.rsplit(":", 1)
    try:
        addr = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)[0][4]
        s = socket.create_connection(addr, timeout=2); s.close(); out[target] = "open"
    except OSError as e:
        # The firewall's reject arrives as "no route to host" (ICMP admin-prohibited) or "permission denied".
        out[target] = "refused" if e.errno in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.EACCES, errno.EPERM,
                                                errno.ENETUNREACH) else type(e).__name__
print(json.dumps(out))
"""

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0 or not all(shutil.which(t) for t in ("ip", "nft", "dnsmasq")),
    reason="needs Linux, root, ip, nft and dnsmasq")


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def network(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("egress")
    for ns in (APP, NET):
        sh("ip", "netns", "del", ns, check=False)
        sh("ip", "netns", "add", ns)
    sh("ip", "link", "add", "suv-va", "type", "veth", "peer", "name", "suv-vn")
    sh("ip", "link", "set", "suv-va", "netns", APP)
    sh("ip", "link", "set", "suv-vn", "netns", NET)
    sh("ip", "-n", APP, "addr", "add", "192.168.50.2/24", "dev", "suv-va")
    sh("ip", "-n", NET, "addr", "add", "192.168.50.1/24", "dev", "suv-vn")
    for ns, dev in ((APP, "suv-va"), (NET, "suv-vn")):
        sh("ip", "-n", ns, "link", "set", dev, "up")
        sh("ip", "-n", ns, "link", "set", "lo", "up")
    sh("ip", "-n", APP, "route", "add", "default", "via", "192.168.50.1")
    for a in ("203.0.113.10", "203.0.113.11", "203.0.113.20", "203.0.113.30"):
        sh("ip", "-n", NET, "addr", "add", f"{a}/32", "dev", "lo")
    # The office's DNS, which knows the test names.
    office_dns = subprocess.Popen(
        ["ip", "netns", "exec", NET, "dnsmasq", "-k", "--no-resolv", "--no-hosts", "--listen-address=192.168.50.1",
         "--bind-interfaces", "--user=nobody", "--address=/approved.test/203.0.113.10",
         "--address=/sub.approved.test/203.0.113.11", "--address=/outside.test/203.0.113.20",
         "--address=/connector.test/203.0.113.30",
         f"--pid-file={tmp}/office-dns.pid"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    servers = subprocess.Popen(["ip", "netns", "exec", NET, sys.executable, "-c", SERVER])
    # Inside the appliance, programs resolve through its own resolver (ip netns exec mounts this file).
    etc = Path("/etc/netns") / APP
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "resolv.conf").write_text("nameserver 127.0.0.1\n")
    # The resolver rereads its upstream list after dropping to an unprivileged user: a readable folder, as on
    # the appliance (/run/systemd/resolve/resolv.conf).
    public = Path(tempfile.mkdtemp(prefix="suveryn-egress-", dir="/run"))
    public.chmod(0o755)
    (public / "upstream.conf").write_text("nameserver 192.168.50.1\n")
    (tmp / "builtin").write_text("approved.test\n")
    env = {**os.environ, "SUVERYN_NETWORK_MODE_FILE": str(tmp / "network-mode.env"),
           "SUVERYN_APPROVED_FILE": str(tmp / "approved"), "SUVERYN_BUILTIN_DESTINATIONS": str(tmp / "builtin"),
           "SUVERYN_DNSMASQ_CONF": str(tmp / "dnsmasq.conf"), "SUVERYN_UPSTREAM_RESOLV": str(public / "upstream.conf"),
           "SUVERYN_DNS_USER": "nobody", "SUVERYN_DNS_RELOAD": "0",
           "SUVERYN_CHECK_TARGETS": "outside.test:443 203.0.113.20:80", "SUVERYN_CHECK_TIMEOUT": "1"}
    state = {"env": env, "tmp": tmp, "resolver": None}
    time.sleep(0.5)
    yield state
    if state["resolver"]:
        state["resolver"].terminate()
    for p in (office_dns, servers):
        p.terminate()
    for ns in (APP, NET):
        sh("ip", "netns", "del", ns, check=False)
    shutil.rmtree(etc, ignore_errors=True)
    shutil.rmtree(public, ignore_errors=True)


def tool(net, *args, check=True):
    r = sh("ip", "netns", "exec", APP, sys.executable, str(TOOL), *args, env=net["env"], check=False)
    if check:
        assert r.returncode == 0, r.stderr + r.stdout
    return r


def configure(net, *mode):
    """Set the mode and apply it, then (re)start the appliance's resolver with the written configuration."""
    if mode:
        tool(net, "set", *mode)
    tool(net, "apply")
    if net["resolver"]:
        net["resolver"].terminate()
        net["resolver"].wait()
    net["resolver"] = subprocess.Popen(["ip", "netns", "exec", APP, "dnsmasq", "-k",
                                        f"--conf-file={net['env']['SUVERYN_DNSMASQ_CONF']}"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)


def probe(*targets) -> dict:
    r = sh("ip", "netns", "exec", APP, sys.executable, "-c", PROBE, *targets)
    return json.loads(r.stdout)


def test_approved_destinations_only(network):
    configure(network, "connected")
    # An approved name's address isn't reachable before the name was looked up through the resolver ...
    assert probe("203.0.113.11:443")["203.0.113.11:443"] != "open"
    # ... it is once looked up, also for a subdomain of an approved name.
    assert probe("approved.test:443", "sub.approved.test:443") == {"approved.test:443": "open",
                                                                    "sub.approved.test:443": "open"}
    assert probe("203.0.113.11:443")["203.0.113.11:443"] == "open"
    # Not approved: refused at once, by name or by address, on any port.
    p = probe("outside.test:443", "203.0.113.20:80")
    assert set(p.values()) == {"refused"}, p
    # The office's own network stays reachable.
    assert probe("192.168.50.1:8443")["192.168.50.1:8443"] == "open"
    r = json.loads(tool(network, "check", "--json").stdout)
    assert (r["passed"], r["firewall_active"], r["outside_reached"]) == (True, True, [])
    assert r["summary"] == "only approved destinations reachable"


def test_the_office_adds_a_destination(network):
    configure(network, "connected")
    assert probe("connector.test:443")["connector.test:443"] == "refused"
    tool(network, "allow", "connector.test")
    configure(network)
    assert probe("connector.test:443")["connector.test:443"] == "open"
    tool(network, "disallow", "connector.test")
    configure(network)  # a fresh table: what was let out before is forgotten
    assert probe("connector.test:443")["connector.test:443"] == "refused"


def test_air_gapped(network):
    configure(network, "air-gapped")
    p = probe("approved.test:443", "outside.test:443", "203.0.113.10:443")
    assert set(p.values()) == {"refused"}, p
    assert probe("192.168.50.1:8443")["192.168.50.1:8443"] == "open"  # the office network stays reachable
    r = json.loads(tool(network, "check", "--json").stdout)
    assert r["passed"] and r["summary"] == "no outgoing connections beyond the office's own network"


def test_all_outgoing_traffic(network):
    configure(network, "connected", "--outbound", "all")
    assert set(probe("approved.test:443", "outside.test:443").values()) == {"open"}
    assert sh("ip", "netns", "exec", APP, "nft", "list", "table", "inet", "suveryn_egress", check=False).returncode != 0
    r = json.loads(tool(network, "check", "--json").stdout)
    assert r["passed"] and "doesn't limit it" in r["summary"] and r["outside_reached"]


def test_the_check_fails_when_traffic_gets_out(network):
    configure(network, "connected")
    # Something lets an outside address through, e.g. a rule added by hand.
    sh("ip", "netns", "exec", APP, "nft", "add", "element", "inet", "suveryn_egress", "approved4", "{ 203.0.113.20 }")
    r = tool(network, "check", "--json", check=False)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and not out["passed"] and out["summary"] == "reached an address outside the approved list"
    assert out["outside_reached"] == ["outside.test:443", "203.0.113.20:80"]


def test_the_check_fails_when_the_rules_arent_loaded(network):
    configure(network, "air-gapped")
    sh("ip", "netns", "exec", APP, "nft", "delete", "table", "inet", "suveryn_egress")
    r = tool(network, "check", check=False)
    assert r.returncode == 1 and "FAILED: the appliance's own outgoing rules aren't loaded" in r.stdout


def test_without_a_valid_mode_nothing_gets_out(network):
    Path(network["env"]["SUVERYN_NETWORK_MODE_FILE"]).write_text("SUVERYN_NETWORK_MODE=outbound-blocked\n")
    configure(network)
    assert set(probe("approved.test:443", "outside.test:443").values()) == {"refused"}
    assert probe("192.168.50.1:8443")["192.168.50.1:8443"] == "open"
    r = tool(network, "check", check=False)
    assert r.returncode == 1 and "no valid network mode" in r.stdout
