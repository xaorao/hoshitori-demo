"""Checks of ``ryo_verdict_record.__main__``, runnable against a mutant: every command with its exact output and exit code, the file limits at
their edge, and every refusal word for word on stderr."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import tempfile
import types
import unittest.mock
from typing import Any

from ryo_verdict_record import __version__
from ryo_verdict_record.schema import EXAMPLE_CALL, TOOL_DEFINITIONS
from ryo_verdict_record.skill import run, run_rules

RECORD = {
    "outcomes": [
        {"symbol": "SOL", "reading": "UP", "direction": "ROSE", "hit": True, "plan": "TARGET"},
        {"symbol": "ETH", "reading": "DOWN", "direction": "ROSE", "hit": False, "plan": "STOP"},
        {"symbol": "ETH", "reading": "UP", "direction": "FELL", "hit": False, "plan": "STOP"},
    ]
}


def _call(mod: types.ModuleType, argv: list[str]) -> tuple[Any, str, str]:
    """The exit code (or the SystemExit code), stdout and stderr of one run of ``main``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code: Any = mod.main(argv)
        except SystemExit as exc:
            code = ("exit", exc.code)
    return code, out.getvalue(), err.getvalue()


def _without_time(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "as_of"}


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []

    def eq(label: str, got: Any, want: Any) -> None:
        if got != want:
            out.append(f"{label}: got {got!r}, expected {want!r}")

    helps = {
        (): [
            "usage: ryo-verdict-record",
            "--version",
            "score the RYO verdicts in a JSON file (a ryo.json, or a list of outcomes)",
            "print the rules of the reading",
            "serve MCP over stdio",
            "serve REST and MCP over HTTP",
            "print both tool definitions",
            "print a working example call",
            "Command line: score the verdicts in a record file, print the rules, or serve MCP (stdio) and REST/MCP (HTTP).",
        ],
        ("score",): ["usage: ryo-verdict-record score", "--symbol", "--min-scored"],
        ("serve",): ["usage: ryo-verdict-record serve", "--host HOST", "--port PORT"],
    }
    with unittest.mock.patch.dict(os.environ, {"COLUMNS": "300"}):
        for path, words in helps.items():
            code, stdout, _ = _call(mod, [*path, "-h"])
            flat = " ".join(stdout.split())
            eq(f"help {path}", (code, [w for w in words if w not in flat], "~" in stdout), (("exit", 0), [], False))
    code, stdout, _ = _call(mod, ["--version"])
    eq("--version", (code, stdout), (("exit", 0), f"{__version__}\n"))
    eq("no command", _call(mod, [])[0], ("exit", 2))
    eq("an unknown command", _call(mod, ["nope"])[0], ("exit", 2))
    eq("tool", _call(mod, ["tool"])[:2], (0, json.dumps(TOOL_DEFINITIONS, indent=2) + "\n"))
    eq("example", _call(mod, ["example"])[:2], (0, json.dumps(EXAMPLE_CALL, indent=2) + "\n"))
    code, stdout, stderr = _call(mod, ["rules"])
    eq("rules", (code, stderr, _without_time(json.loads(stdout))), (0, "", _without_time(run_rules({}))))
    eq("rules are printed with an indent of 2", stdout.startswith('{\n  "schema_version": "1.0",\n'), True)

    served: list[Any] = []
    real = mod.serve_stdio, mod.serve_http

    class Server:
        def __init__(self, host: str, port: int, interrupt: bool) -> None:
            self.args, self.interrupt, self.closed, self.served = (host, port), interrupt, 0, 0

        def serve_forever(self) -> None:
            self.served += 1
            if self.interrupt:
                raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed += 1

    def fake_stdio() -> None:
        served.append("stdio")

    try:
        mod.serve_stdio = fake_stdio
        eq("mcp", (_call(mod, ["mcp"])[:2], served), ((0, ""), ["stdio"]))
        for label, argv, interrupt, want_args in (
            ("serve", ["serve"], False, ("127.0.0.1", 8769)),
            ("serve, interrupted", ["serve"], True, ("127.0.0.1", 8769)),
            ("serve, another host and port", ["serve", "--host", "192.0.2.1", "--port", "9001"], True, ("192.0.2.1", 9001)),
        ):
            made: list[Server] = []

            def fake_http(host: str, port: int, made: list[Server] = made, interrupt: bool = interrupt) -> Server:
                made.append(Server(host, port, interrupt))
                return made[-1]

            mod.serve_http = fake_http
            code, stdout, stderr = _call(mod, argv)
            host, port = want_args
            line = f"listening on http://{host}:{port}  (POST /tools/ryo_verdict_record/call, GET /tools, GET /health, POST / for MCP)\n"
            eq(f"{label}: exit, stdout, stderr", (code, stdout, stderr), (0, "", line))
            eq(
                f"{label}: the server made, served once, closed only when interrupted",
                [(s.args, s.served, s.closed) for s in made],
                [(want_args, 1, 1 if interrupt else 0)],
            )
        _call(mod, ["serve", "--port", "x"])
        eq("a port that is not a number", _call(mod, ["serve", "--port", "x"])[0], ("exit", 2))
    finally:
        mod.serve_stdio, mod.serve_http = real

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)

        def put(name: str, data: bytes) -> str:
            path = root / name
            path.write_bytes(data)
            return str(path)

        good = put("ryo.json", json.dumps(RECORD).encode())
        for label, argv, args in (
            ("score", ["score", good], {"record": RECORD}),
            (
                "score with the options",
                ["score", good, "--symbol", "ETH", "--min-scored", "1"],
                {"record": RECORD, "symbol": "ETH", "min_scored": 1},
            ),
            ("score with the symbol", ["score", good, "--symbol", "SOL"], {"record": RECORD, "symbol": "SOL"}),
            ("score with the minimum", ["score", good, "--min-scored", "2"], {"record": RECORD, "min_scored": 2}),
        ):
            code, stdout, stderr = _call(mod, argv)
            eq(label, (code, stderr, _without_time(json.loads(stdout))), (0, "", _without_time(run(args))))
            eq(f"{label}: indent", stdout.startswith('{\n  "schema_version"'), True)
        listed = put("list.json", json.dumps(RECORD["outcomes"]).encode())
        eq("a bare list", _call(mod, ["score", listed])[0], 0)

        def refused(label: str, argv: list[str], code_want: int, body: dict[str, Any]) -> None:
            code, stdout, stderr = _call(mod, argv)
            try:
                got = json.loads(stderr)
            except ValueError:
                got = stderr
            eq(label, (code, stdout, got), (code_want, "", body))
            eq(f"{label}: stderr is the body printed with an indent of 2", stderr, json.dumps(body, indent=2) + "\n")

        refused(
            "a record of the wrong kind",
            ["score", put("five.json", b"5")],
            2,
            {
                "code": "INVALID_ARGUMENTS",
                "message": "record: must be an object with an outcomes list, or a list of outcomes",
                "details": {"field": "record", "example": EXAMPLE_CALL},
                "trace_id": "cli",
            },
        )
        refused(
            "a minimum out of range",
            ["score", good, "--min-scored", "0"],
            2,
            {
                "code": "INVALID_ARGUMENTS",
                "message": "min_scored: must be a whole number from 1 to 1000",
                "details": {"field": "min_scored", "example": EXAMPLE_CALL},
                "trace_id": "cli",
            },
        )
        refused(
            "a symbol out of range",
            ["score", good, "--symbol", "sol"],
            2,
            {
                "code": "INVALID_ARGUMENTS",
                "message": "symbol: must be 1 to 20 capital letters or digits",
                "details": {"field": "symbol", "example": EXAMPLE_CALL},
                "trace_id": "cli",
            },
        )
        not_json = {"code": "INVALID_ARGUMENTS", "message": "bad.json: is not JSON", "details": {"field": "bad.json"}, "trace_id": "cli"}
        refused("not JSON", ["score", put("bad.json", b"{nope")], 2, not_json)
        refused("not UTF-8", ["score", put("bad.json", b"\xff\xfe\x00")], 2, not_json)
        refused("empty", ["score", put("bad.json", b"")], 2, not_json)
        edge = b"[]" + b" " * (8_000_000 - 2)
        eq("a file of exactly 8000000 bytes is read", _call(mod, ["score", put("edge.json", edge)])[0], 0)
        refused(
            "a file one byte over",
            ["score", put("big.json", edge + b" ")],
            2,
            {"code": "INVALID_ARGUMENTS", "message": "big.json: is larger than 8 MB", "details": {"field": "big.json"}, "trace_id": "cli"},
        )
        unreadable = {"code": "UNREADABLE_FILE", "message": "a file could not be read"}
        refused("a missing file", ["score", str(root / "missing.json")], 2, unreadable)
        refused("a directory", ["score", str(root)], 2, unreadable)
        eq("a minimum that is not a number", _call(mod, ["score", good, "--min-scored", "x"])[0], ("exit", 2))
        eq("no file", _call(mod, ["score"])[0], ("exit", 2))
        eq("the size limit", mod.MAX_FILE_BYTES, 8_000_000)
    return out
