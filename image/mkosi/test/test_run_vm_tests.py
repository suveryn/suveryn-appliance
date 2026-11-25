"""run-vm-tests' own helpers that run without a virtual machine. The tests in the machine: run-vm-tests itself."""
import importlib.machinery
import importlib.util
import socket
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "run-vm-tests"
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("run_vm_tests_under_test", str(TOOL))
vm = importlib.util.module_from_spec(importlib.util.spec_from_loader("run_vm_tests_under_test", _loader))
_loader.exec_module(vm)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_free_port_is_free():
    assert vm.port_free("127.0.0.1", free_port())


def test_a_server_on_every_address_takes_the_port():
    """As the host's nginx or smbd did on the round machine (suveryn-tracker#265)."""
    with socket.socket() as server:
        server.bind(("0.0.0.0", 0))
        server.listen()
        assert not vm.port_free("127.0.0.1", server.getsockname()[1])


def test_a_connection_still_closing_does_not_take_the_port():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen()
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()  # the server side closes first: its end waits in TIME_WAIT
        client.close()
    assert vm.port_free("127.0.0.1", port)
