"""Command line: read a JSON request on stdin and print the answer, or serve MCP (stdio) and REST/MCP (HTTP)."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .errors import InputError
from .schema import EXAMPLE_CALL, TOOL_DEFINITIONS, TOOL_NAME
from .server import serve_http, serve_stdio
from .skill import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidence-sensitivity", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("check", help="read a JSON request from stdin and print the answer")
    commands.add_parser("example", help="print a working request")
    commands.add_parser("tools", help="print the tool definition")
    commands.add_parser("serve-stdio", help="serve MCP over standard input and output")
    http = commands.add_parser("serve-http", help="serve REST and MCP over HTTP on this machine")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8767)
    args = parser.parse_args(argv)
    if args.command == "example":
        print(json.dumps(EXAMPLE_CALL, indent=2))
        return 0
    if args.command == "tools":
        print(json.dumps(TOOL_DEFINITIONS, indent=2))
        return 0
    if args.command == "serve-stdio":
        serve_stdio()
        return 0
    if args.command == "serve-http":
        server = serve_http(args.host, args.port)
        print(f"serving on http://{args.host}:{args.port} (Ctrl-C to stop)", file=sys.stderr, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
        return 0
    if args.command == "check":
        try:
            print(json.dumps(run(json.load(sys.stdin)), indent=2))
        except InputError as exc:
            print(json.dumps(exc.to_envelope("cli"), indent=2))
            return 2
        except ValueError:
            print(json.dumps({"code": "INVALID_ARGUMENTS", "message": "the input is not JSON"}, indent=2))
            return 2
        return 0
    parser.print_help()
    print(f"\nTool: {TOOL_NAME}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
