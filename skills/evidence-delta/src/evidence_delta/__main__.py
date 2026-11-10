"""Command line: compare two reads held in JSON files, print the rules, or serve MCP (stdio) and REST/MCP (HTTP)."""

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

MAX_FILE_BYTES = 4_000_000


def _load(path: pathlib.Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InputError(str(path.name), "is larger than 4 MB", None)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InputError(str(path.name), "is not JSON", None) from exc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evidence-delta", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="compare two JSON files (RYO envelopes, or their data) and print the envelope")
    c.add_argument("before", type=pathlib.Path)
    c.add_argument("after", type=pathlib.Path)
    c.add_argument("--fields", default=None, help="comma-separated dotted paths")
    c.add_argument("--min-change-pct", type=float, default=None)
    sub.add_parser("rules", help="print the rules of the comparison")
    sub.add_parser("mcp", help="serve MCP over stdio")
    h = sub.add_parser("serve", help="serve REST and MCP over HTTP")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8768)
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
            args: dict[str, Any] = {"before": _load(ns.before), "after": _load(ns.after)}
            if ns.fields is not None:
                args["fields"] = [f.strip() for f in ns.fields.split(",") if f.strip()]
            if ns.min_change_pct is not None:
                args["min_change_pct"] = ns.min_change_pct
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
