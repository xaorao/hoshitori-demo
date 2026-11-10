"""Command line: check an address, describe the sources, fetch or confirm the OFAC list copy, or serve MCP (stdio) and REST/MCP (HTTP)."""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import pathlib
import sys
import time
from typing import Any

from . import __version__, list_store
from .errors import InputError
from .schema import EXAMPLE_CALL, SOURCES_TOOL_NAME, TOOL_DEFINITIONS, TOOL_NAME
from .server import Runner, serve_http, serve_stdio
from .skill import run, run_sources
from .transport import Caller
from .trust import default_transport

UPDATE_BUDGET_SECONDS = 120.0

TLS_HINT = (
    "This machine's Python could not check OFAC's certificate with the trust store it uses (seen on macOS with a Python from python.org "
    "or uv). Point it at another trust store and run update-list again. On macOS: security find-certificate -a -p "
    "/System/Library/Keychains/SystemRootCertificates.keychain > $HOME/.address-flag-check-roots.pem, then export "
    "SSL_CERT_FILE=$HOME/.address-flag-check-roots.pem. On Linux, update the system's CA bundle (for example the ca-certificates package)."
)


def _runners(list_dir: pathlib.Path | None, refresh: bool) -> dict[str, Runner]:
    return {
        TOOL_NAME: functools.partial(run, list_dir=list_dir, refresh_list=refresh),
        SOURCES_TOOL_NAME: functools.partial(run_sources, list_dir=list_dir),
    }


def _update(list_dir: pathlib.Path, force: bool) -> int:
    now = dt.datetime.now(dt.UTC)
    caller = Caller(default_transport(), now=now, deadline=time.monotonic() + UPDATE_BUDGET_SECONDS)
    state = list_store.ensure(list_dir, caller, now, force=force)
    out: dict[str, Any] = {
        "action": state.action,
        "store": str(list_dir / list_store.STORE_FILE),
        "error": None if state.error is None else state.error.reason,
    }
    if state.error is not None and state.error.reason == "tls_error":
        out["hint"] = TLS_HINT
    if state.stored is not None:
        out.update(
            {
                "date_of_issue": state.stored.index.date_of_issue,
                "address_strings": state.stored.index.address_strings,
                "zip_sha256": state.stored.zip_sha256,
                "downloaded_at": state.stored.downloaded_at,
                "confirmed_at": state.stored.confirmed_at,
            }
        )
    out["source_calls"] = [c.as_dict() for c in caller.records]
    print(json.dumps(out, indent=2))
    return 0 if state.stored is not None and state.error is None else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="address-flag-check", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--list-dir",
        type=pathlib.Path,
        default=None,
        help="where the OFAC list copy is kept (default: $ADDRESS_FLAG_CHECK_HOME or ~/.cache/address-flag-check)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check one address and print the envelope")
    c.add_argument("--address", required=True)
    c.add_argument("--chain", required=True)
    c.add_argument("--checks", default=None, help="comma-separated subset of ofac_sdn,usdt_freeze,usdc_freeze,chain_facts")
    c.add_argument("--max-list-age-hours", type=int, default=None)
    c.add_argument("--no-refresh", action="store_true", help="never contact OFAC; use the local copy as it is")
    s = sub.add_parser("sources", help="describe the sources and the local list copy (sends nothing)")
    s.add_argument("--chain", default=None)
    u = sub.add_parser("update-list", help="download OFAC's list, or confirm that the local copy is still current")
    u.add_argument("--force", action="store_true", help="download even if the copy was confirmed in the last hour")
    m = sub.add_parser("mcp", help="serve MCP over stdio")
    m.add_argument("--no-refresh", action="store_true")
    h = sub.add_parser("serve", help="serve REST and MCP over HTTP")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8766)
    h.add_argument("--no-refresh", action="store_true")
    sub.add_parser("tool", help="print both tool definitions")
    sub.add_parser("example", help="print a working example call")
    ns = p.parse_args(argv)
    list_dir: pathlib.Path | None = ns.list_dir

    if ns.cmd == "tool":
        print(json.dumps(TOOL_DEFINITIONS, indent=2))
        return 0
    if ns.cmd == "example":
        print(json.dumps(EXAMPLE_CALL, indent=2))
        return 0
    if ns.cmd == "update-list":
        return _update(list_dir or list_store.default_dir(), ns.force)
    if ns.cmd == "mcp":
        serve_stdio(_runners(list_dir, not ns.no_refresh))
        return 0
    if ns.cmd == "serve":
        srv = serve_http(ns.host, ns.port, _runners(list_dir, not ns.no_refresh))
        print(
            f"listening on http://{ns.host}:{ns.port}  (POST /tools/{TOOL_NAME}/call, GET /tools, GET /health, POST / for MCP)",
            file=sys.stderr,
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.server_close()
        return 0
    try:
        if ns.cmd == "sources":
            envelope = run_sources({} if ns.chain is None else {"chain": ns.chain}, list_dir=list_dir)
        else:
            args: dict[str, Any] = {"address": ns.address, "chain": ns.chain}
            if ns.checks is not None:
                args["checks"] = [x.strip() for x in ns.checks.split(",") if x.strip()]
            if ns.max_list_age_hours is not None:
                args["max_list_age_hours"] = ns.max_list_age_hours
            envelope = run(args, list_dir=list_dir, refresh_list=not ns.no_refresh)
    except InputError as exc:
        print(json.dumps(exc.to_envelope("cli"), indent=2), file=sys.stderr)
        return 2
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
