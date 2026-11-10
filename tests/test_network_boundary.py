"""N-1: every outbound connection is made by the gateway's transports; no other module of the league can open one.

A static reading of the package: which modules import a network client. The boundary is a short allow-list of the modules that are the
transports (the one HTTP client, the guarded fetcher of public pages and its socket layer), and nothing else may import ``socket``, ``ssl``,
``http.client``, ``urllib.request`` or a third-party HTTP library, directly or through ``importlib``. A module that needs the network goes
through a transport, which the gateway paces, bounds and records. This does not see a call made through a library that is itself imported
elsewhere for another purpose; it sees every import of a client by name, which is how a second door would first appear.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "hoshitori"
CLIENTS = frozenset(
    {
        "socket",
        "ssl",
        "http.client",
        "urllib.request",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "websockets",
        "websocket",
        "ftplib",
        "smtplib",
        "telnetlib",
    }
)
ALLOWED = frozenset({"http_transport", "safe_fetch", "safe_fetch_net"})


def clients_imported(source: str) -> set[str]:
    """The network clients a module's text imports, by name, through an import statement or through ``importlib`` or ``__import__``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name in CLIENTS or a.name.split(".")[0] in CLIENTS}
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module in CLIENTS or node.module.split(".")[0] in CLIENTS:
                found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names if f"{node.module}.{a.name}" in CLIENTS}
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in ("import_module", "__import__") and (node.args[0].value in CLIENTS or node.args[0].value.split(".")[0] in CLIENTS):
                found.add(node.args[0].value)
    return found


def test_the_scan_sees_each_way_a_client_can_be_imported() -> None:
    assert clients_imported("import socket") == {"socket"}
    assert clients_imported("import urllib.request as u") == {"urllib.request"}
    assert clients_imported("from http import client\nfrom http.client import HTTPSConnection") == {"http.client"}
    assert clients_imported("from urllib import request") == {"urllib.request"}
    assert clients_imported("import importlib\nimportlib.import_module('requests')") == {"requests"}
    assert clients_imported("m = __import__('ssl')") == {"ssl"}
    assert clients_imported("import json\nfrom urllib.parse import urlsplit") == set()
    assert clients_imported("x = 'socket'") == set()


def test_the_boundary_names_modules_that_exist() -> None:
    assert {p.stem for p in SOURCE.glob("*.py")} >= ALLOWED


def test_network_clients_are_imported_only_by_the_gateway_transports() -> None:
    offenders = {}
    for path in sorted(SOURCE.glob("*.py")):
        if path.stem in ALLOWED:
            continue
        found = clients_imported(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = sorted(found)
    assert offenders == {}, f"modules that import a network client without being a transport: {offenders}"


def test_each_allowed_transport_really_is_one() -> None:
    """The allow-list is not a place to park a module: every module on it imports a client (otherwise it has no business there)."""
    for name in sorted(ALLOWED):
        assert clients_imported((SOURCE / f"{name}.py").read_text(encoding="utf-8")), f"{name} imports no network client"
