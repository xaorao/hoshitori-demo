"""Command line: price an exit, describe the coverage, or serve MCP (stdio) and REST/MCP (HTTP)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .errors import InputError
from .schema import EXAMPLE_CALL, TOOL_DEFINITIONS, TOOL_NAME
from .server import serve_http, serve_stdio
from .skill import run, run_sources


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exit-liquidity-check", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="price the exit of one token and print the envelope")
    c.add_argument("--token", required=True)
    c.add_argument("--size-usd", type=float, default=None)
    c.add_argument("--max-slippage-pct", type=float, default=None)
    c.add_argument("--participation-pct", type=float, default=None)
    sub.add_parser("sources", help="describe the coverage (sends nothing)")
    sub.add_parser("mcp", help="serve MCP over stdio")
    h = sub.add_parser("serve", help="serve REST and MCP over HTTP")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8767)
    sub.add_parser("tool", help="print both tool definitions")
    sub.add_parser("example", help="print a working example call")
    ns = p.parse_args(argv)

    if ns.cmd == "tool":
        print(json.dumps(TOOL_DEFINITIONS, indent=2))
        return 0
    if ns.cmd == "example":
        print(json.dumps(EXAMPLE_CALL, indent=2))
        return 0
    if ns.cmd == "mcp":
        serve_stdio()
        return 0
    if ns.cmd == "serve":
        srv = serve_http(ns.host, ns.port)
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
            envelope = run_sources({})
        else:
            args: dict[str, Any] = {"token": ns.token}
            for key, value in (
                ("size_usd", ns.size_usd),
                ("max_slippage_pct", ns.max_slippage_pct),
                ("participation_pct", ns.participation_pct),
            ):
                if value is not None:
                    args[key] = value
            envelope = run(args)
    except InputError as exc:
        print(json.dumps(exc.to_envelope("cli"), indent=2), file=sys.stderr)
        return 2
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
