"""Command line: score the verdicts in a record file, print the rules, or serve MCP (stdio) and REST/MCP (HTTP)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from . import __version__
from .errors import InputError
from .schema import EXAMPLE_CALL, TOOL_DEFINITIONS, TOOL_NAME
from .server import serve_http, serve_stdio
from .skill import run, run_rules

MAX_FILE_BYTES = 8_000_000


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ryo-verdict-record", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("score", help="score the RYO verdicts in a JSON file (a ryo.json, or a list of outcomes)")
    c.add_argument("file", type=pathlib.Path)
    c.add_argument("--symbol", default=None)
    c.add_argument("--min-scored", type=int, default=None)
    sub.add_parser("rules", help="print the rules of the reading")
    sub.add_parser("mcp", help="serve MCP over stdio")
    h = sub.add_parser("serve", help="serve REST and MCP over HTTP")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8769)
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
        if ns.cmd == "rules":
            envelope = run_rules({})
        else:
            if ns.file.stat().st_size > MAX_FILE_BYTES:
                raise InputError(ns.file.name, "is larger than 8 MB", None)
            try:
                record = json.loads(ns.file.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise InputError(ns.file.name, "is not JSON", None) from exc
            args: dict[str, Any] = {"record": record}
            if ns.symbol is not None:
                args["symbol"] = ns.symbol
            if ns.min_scored is not None:
                args["min_scored"] = ns.min_scored
            envelope = run(args)
    except (InputError, OSError) as exc:
        detail = (
            exc.to_envelope("cli") if isinstance(exc, InputError) else {"code": "UNREADABLE_FILE", "message": "a file could not be read"}
        )
        print(json.dumps(detail, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
