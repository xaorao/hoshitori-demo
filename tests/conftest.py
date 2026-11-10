"""A runtime guard behind the static network boundary test: no test that is not marked ``live`` may open a connection beyond this machine.

``test_network_boundary`` reads the package and sees every import of a network client by name. This sees what is done: a connection to an
address that is not loopback (or a unix socket) is refused with an error that names where it came from, whichever module made it and
however it was imported. A second door, or a test that quietly reached the real service, fails here and not on a user's machine.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import pytest

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _beyond_this_machine(address: Any) -> bool:
    if not isinstance(address, tuple) or not address:
        return False  # a unix socket path
    host = address[0]
    if not isinstance(host, str):
        return False
    if host in ("localhost", ""):
        return False
    try:
        return not ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return True  # a name: it has not been resolved to loopback, so it is not this machine


@pytest.fixture(autouse=True)
def no_connection_beyond_this_machine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.node.get_closest_marker("live") is not None:
        yield
        return

    def connect(self: socket.socket, address: Any) -> Any:
        if _beyond_this_machine(address):
            raise AssertionError(f"a test opened a connection beyond this machine: {address!r} (mark it live, or use a loopback server)")
        return _connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if _beyond_this_machine(address):
            raise AssertionError(f"a test opened a connection beyond this machine: {address!r} (mark it live, or use a loopback server)")
        return _connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield


def test_the_guard_refuses_an_outside_address_and_allows_this_machine() -> None:
    """The guard's own two decisions, without opening anything."""
    assert _beyond_this_machine(("93.184.216.34", 443)) is True
    assert _beyond_this_machine(("example.org", 443)) is True
    assert _beyond_this_machine(("2606:4700:4700::1111", 443, 0, 0)) is True
    assert _beyond_this_machine(("127.0.0.1", 8080)) is False
    assert _beyond_this_machine(("::1", 8080, 0, 0)) is False
    assert _beyond_this_machine(("localhost", 8080)) is False
    assert _beyond_this_machine("/tmp/a.sock") is False
    assert _beyond_this_machine(("fe80::1%en0", 1)) is True
