"""suveryn-network-mode: the mode file, the approved destinations and the rules made from them (tracker #38).

Runs the tool itself against files in a temporary folder. Any machine:

    python3 -m pytest network-mode/tests

The rules are also loaded into a real firewall and resolver in test_egress.py (root, Linux).
"""

import importlib.machinery
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "suveryn-network-mode"
BUILTIN = TOOL.parent / "builtin-destinations"


@pytest.fixture
def env(tmp_path):
    return {**os.environ, "SUVERYN_NETWORK_MODE_FILE": str(tmp_path / "etc" / "suveryn" / "network-mode.env"),
            "SUVERYN_APPROVED_FILE": str(tmp_path / "etc" / "suveryn" / "approved-destinations"),
            "SUVERYN_NETWORK_MODE_TEST": "1"}


def run(env, *args):
    return subprocess.run([sys.executable, str(TOOL), *args], check=False, capture_output=True, text=True, env=env)


def mode_file(env) -> Path:
    return Path(env["SUVERYN_NETWORK_MODE_FILE"])


def load(env=None):
    """The tool as a module, with the given environment's paths."""
    saved = dict(os.environ)
    os.environ.update(env or {})
    try:
        loader = importlib.machinery.SourceFileLoader("netmode", str(TOOL))
        spec = importlib.util.spec_from_loader("netmode", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------------------- the mode file
@pytest.mark.parametrize("args, mode, outbound", [
    (["connected"], "connected", "approved"),                         # approved destinations is the default
    (["connected", "--outbound", "approved"], "connected", "approved"),
    (["connected", "--outbound", "all"], "connected", "all"),
    (["air-gapped"], "air-gapped", "none"),
])
def test_a_mode_set_at_setup_is_read_back(env, args, mode, outbound):
    assert run(env, "set", *args).returncode == 0
    assert (run(env, "get").stdout.strip(), run(env, "get", "--outbound").stdout.strip()) == (mode, outbound)
    path = mode_file(env)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # any component may read it; it holds no secret
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]  # written atomically


def test_the_file_is_a_valid_environment_file_and_shell(env):
    """What systemd's EnvironmentFile= and a shell's `.` give a component: the two variables, nothing else."""
    run(env, "set", "connected", "--outbound", "all")
    path = mode_file(env)
    out = subprocess.run(["/bin/sh", "-c", f'. "{path}"; printf "%s %s" "$SUVERYN_NETWORK_MODE" "$SUVERYN_OUTBOUND"'],
                         check=False, capture_output=True, text=True, env={})
    assert out.stdout == "connected all"
    assignments = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    assert assignments == ["SUVERYN_NETWORK_MODE=connected", "SUVERYN_OUTBOUND=all"]


def test_changing_the_mode_replaces_it(env):
    run(env, "set", "connected", "--outbound", "all")
    run(env, "set", "air-gapped")
    assert (run(env, "get").stdout.strip(), run(env, "get", "--outbound").stdout.strip()) == ("air-gapped", "none")


@pytest.mark.parametrize("args, message", [
    (["outbound-blocked"], "not a mode"),          # dropped in October 2026: Connected with approved destinations
    (["airgapped"], "not a mode"),
    (["Air-Gapped"], "not a mode"),
    ([""], "not a mode"),
    (["air-gapped", "--outbound", "all"], "doesn't fit"),
    (["connected", "--outbound", "none"], "doesn't fit"),
    (["connected", "--outbound"], "usage"),
    (["connected", "all"], "usage"),
])
def test_a_wrong_value_is_refused_and_changes_nothing(env, args, message):
    run(env, "set", "connected")
    r = run(env, "set", *args)
    assert r.returncode == 2 and message in r.stderr
    assert run(env, "get").stdout.strip() == "connected"


@pytest.mark.parametrize("content", [
    None,                                                                      # never set
    "SUVERYN_NETWORK_MODE=offline\n",                                          # not a mode
    "SUVERYN_NETWORK_MODE=outbound-blocked\n",                                 # the dropped third mode
    "SUVERYN_NETWORK_MODE=air-gapped\nSUVERYN_NETWORK_MODE=connected\n",       # two values: which one?
    "# SUVERYN_NETWORK_MODE=air-gapped\n",                                     # only a comment
    "SUVERYN_NETWORK_MODE\n",                                                  # no value
    "SUVERYN_NETWORK_MODE=air-gapped\nSUVERYN_OUTBOUND=all\n",                 # a policy that doesn't fit the mode
    "SUVERYN_NETWORK_MODE=connected\nSUVERYN_OUTBOUND=all\nSUVERYN_OUTBOUND=approved\n",
])
def test_anything_but_one_valid_consistent_mode_is_unknown(env, content):
    """Unknown is never air-gapped: a component must not claim isolation it can't vouch for."""
    path = mode_file(env)
    if content is not None:
        path.parent.mkdir(parents=True)
        path.write_text(content)
    r = run(env, "get")
    assert (r.returncode, r.stdout.strip()) == (1, "unknown")
    assert "set one with" in r.stderr
    assert run(env, "get", "--outbound").stdout.strip() == "unknown"


def test_connected_without_a_policy_reads_as_approved_destinations_only(env):
    path = mode_file(env)
    path.parent.mkdir(parents=True)
    path.write_text("SUVERYN_NETWORK_MODE=connected\n")
    assert run(env, "get", "--outbound").stdout.strip() == "approved"


def test_changing_it_needs_root(env):
    env = {k: v for k, v in env.items() if k != "SUVERYN_NETWORK_MODE_TEST"}
    if os.geteuid() == 0:
        pytest.skip("running as root")
    for args in (["set", "air-gapped"], ["allow", "portal.example.com"], ["apply"]):
        r = run(env, *args)
        assert r.returncode == 2 and "needs root" in r.stderr


def test_usage(env):
    for args in ([], ["get", "x"], ["set"], ["show", "x"], ["allow"], ["check", "x"], ["apply", "x"]):
        assert run(env, *args).returncode == 2


# ---------------------------------------------------------------------------- approved destinations
def test_the_built_in_list_is_valid_and_never_includes_a_check_address():
    netmode = load()
    builtin = netmode.domains(BUILTIN)
    lines = [ln.split("#")[0].strip() for ln in BUILTIN.read_text().splitlines()]
    assert len(builtin) == len([ln for ln in lines if ln])  # no line silently dropped as invalid
    assert "suveryn.com" in builtin and "huggingface.co" in builtin
    for host in netmode.check_hosts():
        assert not netmode.covered(host, builtin), host


def test_the_office_adds_and_removes_its_own_destinations(env):
    assert run(env, "allow", "Connector.Example.NET.").returncode == 0
    assert run(env, "allow", "legal.example.org").returncode == 0
    assert run(env, "allow", "legal.example.org").returncode == 0  # twice is once
    path = Path(env["SUVERYN_APPROVED_FILE"])
    assert [ln for ln in path.read_text().splitlines() if not ln.startswith("#")] == \
        ["connector.example.net", "legal.example.org"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    show = run(env, "show").stdout
    assert "this office's): connector.example.net, legal.example.org" in show and "suveryn.com" in show
    assert run(env, "disallow", "connector.example.net").returncode == 0
    assert "this office's): legal.example.org" in run(env, "show").stdout


@pytest.mark.parametrize("domain, message", [
    ("not a domain", "not a domain name"),
    ("http://portal.example.com", "not a domain name"),
    ("com", "not a domain name"),
    ("*.example.com", "not a domain name"),
    ("example.com", "outgoing-traffic check"),        # would make the check meaningless
    ("www.example.com", "outgoing-traffic check"),
])
def test_what_cant_be_approved(env, domain, message):
    r = run(env, "allow", domain)
    assert r.returncode == 2 and message in r.stderr
    assert not Path(env["SUVERYN_APPROVED_FILE"]).exists()


def test_the_appliances_own_destinations_cant_be_removed(env):
    r = run(env, "disallow", "suveryn.com")
    assert r.returncode == 2 and "can't be removed" in r.stderr


def test_a_damaged_list_keeps_its_valid_lines(tmp_path):
    f = tmp_path / "list"
    f.write_text("good.example.com\n  # comment\nbad line here\n*.wild.example\nother.example.org # note\n\n")
    assert load().domains(f) == ["good.example.com", "other.example.org"]


# ---------------------------------------------------------------------------- the rules made from it
def test_approved_destinations_only_lets_out_the_office_dns_and_the_approved_sets():
    rules = load().nft_ruleset("connected", "approved")
    assert "policy accept" in rules and rules.rstrip().endswith("}")
    body = rules.split("chain output {", 1)[1]
    assert body.index("oif lo accept") < body.index("ct state established,related accept") < body.index("reject")
    assert "ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } accept" in body
    assert 'meta skuid "suveryn-dns" meta l4proto { tcp, udp } th dport 53 accept' in body
    assert "ip daddr @approved4 tcp dport { 80, 443 } accept" in body
    assert "ip6 daddr @approved6 udp dport { 123 } accept" in body
    assert body.strip().splitlines()[-3].strip() == "reject with icmpx admin-prohibited"  # everything else, refused at once


def test_air_gapped_lets_out_only_the_office_network():
    rules = load().nft_ruleset("air-gapped", "none")
    assert "@approved" not in rules.split("chain output", 1)[1] and "reject with icmpx admin-prohibited" in rules


def test_all_outgoing_traffic_removes_the_table():
    rules = load().nft_ruleset("connected", "all")
    assert rules.splitlines() == ["table inet suveryn_egress", "delete table inet suveryn_egress"]


def test_the_resolver_fills_the_sets_only_with_approved_destinations_only():
    netmode = load()
    conf = netmode.dnsmasq_conf("connected", "approved", ["suveryn.com", "huggingface.co"])
    assert "listen-address=127.0.0.1" in conf and "bind-interfaces" in conf and "user=suveryn-dns" in conf
    assert "nftset=/suveryn.com/4#inet#suveryn_egress#approved4,6#inet#suveryn_egress#approved6" in conf
    for mode, outbound in (("air-gapped", "none"), ("connected", "all")):
        assert "nftset" not in netmode.dnsmasq_conf(mode, outbound, ["suveryn.com"])


@pytest.mark.skipif(not shutil.which("nft") or not shutil.which("unshare") or os.geteuid() != 0,
                    reason="checks the rules with nft in a throwaway network namespace (root)")
@pytest.mark.parametrize("mode, outbound", [("connected", "approved"), ("air-gapped", "none"), ("connected", "all")])
def test_nft_accepts_the_rules(mode, outbound):
    rules = load({"SUVERYN_DNS_USER": "nobody"}).nft_ruleset(mode, outbound)
    r = subprocess.run(["unshare", "-n", "nft", "-f", "-"], input=rules, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
