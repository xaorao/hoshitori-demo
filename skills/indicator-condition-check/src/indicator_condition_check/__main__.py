"""Command line: run once, serve MCP over stdio, or serve REST/MCP over HTTP."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .errors import InputError
from .schema import EXAMPLE_CALL, TOOL_DEFINITION
from .server import serve_http, serve_stdio
from .skill import error_envelope, run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="indicator-condition-check", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="evaluate a condition once and print the envelope")
    c.add_argument("--symbol", required=True)
    c.add_argument("--condition", required=True, help="condition JSON, or @file.json")
    c.add_argument("--timeframe", default=None, choices=["1h", "4h", "1d"])
    c.add_argument("--lookback-bars", type=int, default=None)
    c.add_argument("--venue", default=None, choices=["auto", "binance", "coinbase", "kraken", "bybit"])
    c.add_argument("--cross-venue-check", action="store_true")
    sub.add_parser("mcp", help="serve MCP over stdio")
    s = sub.add_parser("serve", help="serve REST and MCP-over-HTTP")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    sub.add_parser("tool", help="print the tool definition (name, description, input and output schema)")
    sub.add_parser("example", help="print a working example call")
    ns = p.parse_args(argv)

    if ns.cmd == "tool":
        print(json.dumps(TOOL_DEFINITION, indent=2))
    elif ns.cmd == "example":
        print(json.dumps(EXAMPLE_CALL, indent=2))
    elif ns.cmd == "mcp":
        serve_stdio()
    elif ns.cmd == "serve":
        srv = serve_http(ns.host, ns.port)
        print(f"listening on http://{ns.host}:{ns.port}  (POST /tools/indicator_condition_check/call, GET /tools, GET /health, POST / for MCP)", file=sys.stderr)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.server_close()
    else:
        raw = ns.condition
        if raw.startswith("@"):
            try:
                with open(raw[1:], encoding="utf-8") as fh:
                    raw = fh.read()
            except (OSError, UnicodeDecodeError) as e:
                refusal = {
                    "code": "UNREADABLE_FILE",
                    "message": f"--condition names the file {raw[1:]}, which cannot be read: {type(e).__name__}: {e}",
                    "details": {"field": "condition", "example": EXAMPLE_CALL},
                }
                print(json.dumps(refusal, indent=2), file=sys.stderr)
                return 2
        try:
            args = {"symbol": ns.symbol, "condition": json.loads(raw)}
        except ValueError as e:
            refusal = {
                "code": "INVALID_JSON",
                "message": f"--condition is not valid JSON: {e}",
                "details": {"field": "condition", "example": EXAMPLE_CALL},
            }
            print(json.dumps(refusal, indent=2), file=sys.stderr)
            return 2
        for key, val in (("timeframe", ns.timeframe), ("lookback_bars", ns.lookback_bars), ("venue", ns.venue)):
            if val is not None:
                args[key] = val
        if ns.cross_venue_check:
            args["cross_venue_check"] = True
        try:
            print(json.dumps(run(args), indent=2))
        except InputError as e:
            print(json.dumps(error_envelope(e), indent=2), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
