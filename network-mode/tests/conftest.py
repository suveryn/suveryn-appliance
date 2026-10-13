"""Whether this machine can run the network-mode tests that need their own network namespace.

Ten tests load real nftables rules into a throwaway network namespace: test_egress.py (the rules and
the resolver end to end) and test_network_mode.py::test_nft_accepts_the_rules. That needs Linux,
root, the tools, and a kernel or container that allows network namespaces and nftables in them. An
unprivileged container (such as a RunPod pod) doesn't, so there they are skipped, with the reason.

On a machine meant to run them (a VM, a privileged container, CI), set
SUVERYN_REQUIRE_EGRESS_TESTS=1: then a missing requirement fails them instead of skipping them, so a
skip can't hide a real problem.
"""

import functools
import os
import shutil
import subprocess
import sys

import pytest

ELSEWHERE = "run them on a VM or in a privileged container (README, Tests)"


def _refused(cmd: list[str], stdin: str | None = None) -> str | None:
    """None when the command works, else the first line of what it said."""
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if r.returncode == 0:
        return None
    return ((r.stderr or r.stdout).strip().splitlines() or [f"exit {r.returncode}"])[0]


@functools.cache
def namespace_problem(tools: tuple[str, ...]) -> str | None:
    """Why the namespace tests can't run here, or None when they can (checked once per run)."""
    if sys.platform != "linux":
        return "they need Linux"
    if os.geteuid() != 0:
        return "they need root"
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        return f"{', '.join(missing)} not installed"
    probe = "table inet suveryn_probe { }\n"
    if "unshare" in tools and (err := _refused(["unshare", "-n", "nft", "-f", "-"], probe)):
        return f"network namespaces or nftables aren't allowed here (an unprivileged container?): {err}"
    if "ip" in tools:
        ns = f"suveryn-probe-{os.getpid()}"
        err = _refused(["ip", "netns", "add", ns])
        if not err:
            err = _refused(["ip", "netns", "exec", ns, "nft", "-f", "-"], probe)
            _refused(["ip", "netns", "del", ns])
        if err:
            return f"network namespaces or nftables aren't allowed here (an unprivileged container?): {err}"
    return None


def require_namespaces(*tools: str) -> None:
    """Skip (or, with SUVERYN_REQUIRE_EGRESS_TESTS=1, fail) when this machine can't run namespace tests."""
    problem = namespace_problem(tuple(sorted(tools)))
    if problem is None:
        return
    if os.environ.get("SUVERYN_REQUIRE_EGRESS_TESTS") == "1":
        pytest.fail(f"SUVERYN_REQUIRE_EGRESS_TESTS=1, but {problem}", pytrace=False)
    pytest.skip(f"{problem}; {ELSEWHERE}")
