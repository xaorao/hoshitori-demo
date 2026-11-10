"""Checks of ``exit_liquidity_check.__main__``, runnable against a mutant: every subcommand with the skill and the servers replaced by recorders."""

from __future__ import annotations

import contextlib
import io
import json
import re
import types
from typing import Any

from exit_liquidity_check.errors import InputError
from tests.checks_support import Case

EXAMPLE = {"token": "SOL", "size_usd": 50000}


class _Server:
    def __init__(self, stop: bool) -> None:
        self.stop, self.events = stop, []

    def serve_forever(self) -> None:
        self.events.append("serve")
        if self.stop:
            raise KeyboardInterrupt

    def server_close(self) -> None:
        self.events.append("close")


def _call(mod: types.ModuleType, argv: list[str]) -> tuple[Any, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code: Any = mod.main(argv)
        except SystemExit as exit_:
            code = ("exit", exit_.code)
    return code, out.getvalue(), err.getvalue()


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    saved = (mod.run, mod.run_sources, mod.serve_stdio, mod.serve_http)
    ran: list[Any] = []
    served: list[Any] = []
    servers: list[_Server] = []
    mod.run = lambda args: (ran.append(args), {"answer": args})[1]
    mod.run_sources = lambda args: (ran.append(("sources", args)), {"sources": True})[1]
    mod.serve_stdio = lambda: served.append("stdio")

    def make_http(host: str, port: int) -> _Server:
        served.append((host, port))
        servers.append(_Server(stop=False))
        return servers[-1]

    mod.serve_http = make_http
    try:
        code, out, err = _call(mod, ["tool"])
        c.eq("tool", (code, json.loads(out), out == json.dumps(json.loads(out), indent=2) + "\n", err), (0, mod.TOOL_DEFINITIONS, True, ""))
        code, out, err = _call(mod, ["example"])
        c.eq("example", (code, out, err), (0, json.dumps(EXAMPLE, indent=2) + "\n", ""))
        code, out, err = _call(mod, ["mcp"])
        c.eq("mcp", (code, out, err, served), (0, "", "", ["stdio"]))
        served.clear()
        code, out, err = _call(mod, ["sources"])
        c.eq("sources", (code, json.loads(out), ran, err), (0, {"sources": True}, [("sources", {})], ""))
        ran.clear()

        code, out, err = _call(mod, ["check", "--token", "bonk"])
        c.eq("check with a token only", (code, ran, json.loads(out), err), (0, [{"token": "bonk"}], {"answer": {"token": "bonk"}}, ""))
        c.eq("... printed with an indent of 2", out, json.dumps({"answer": {"token": "bonk"}}, indent=2) + "\n")
        ran.clear()
        code, out, err = _call(
            mod, ["check", "--token", "SOL", "--size-usd", "5000", "--max-slippage-pct", "1.5", "--participation-pct", "20"]
        )
        c.eq(
            "check with every option",
            (code, ran),
            (0, [{"token": "SOL", "size_usd": 5000.0, "max_slippage_pct": 1.5, "participation_pct": 20.0}]),
        )
        ran.clear()
        _call(mod, ["check", "--token", "SOL", "--participation-pct", "20"])
        c.eq("only the given options are sent", ran, [{"token": "SOL", "participation_pct": 20.0}])
        ran.clear()
        _call(mod, ["check", "--token", "SOL", "--size-usd", "0", "--max-slippage-pct", "0", "--participation-pct", "0"])
        c.eq(
            "a zero is a value, not an omission",
            ran,
            [{"token": "SOL", "size_usd": 0.0, "max_slippage_pct": 0.0, "participation_pct": 0.0}],
        )
        ran.clear()
        _call(mod, ["check", "--token", "SOL", "--max-slippage-pct", "2"])
        c.eq("slippage alone", ran, [{"token": "SOL", "max_slippage_pct": 2.0}])

        def refuse(args: Any) -> dict[str, Any]:
            raise InputError("token", "is not covered", EXAMPLE)

        mod.run = refuse
        code, out, err = _call(mod, ["check", "--token", "DOGE"])
        body = json.loads(err) if err else {}
        c.eq("the refusal is printed with an indent of 2", err, json.dumps(body, indent=2) + "\n")
        c.eq(
            "a refusal exits 2, to stderr, with the typed error and nothing on stdout",
            (code, out, body.get("code"), body.get("message"), body.get("details"), body.get("trace_id"), err.startswith("{\n  ")),
            (2, "", "INVALID_ARGUMENTS", "token: is not covered", {"field": "token", "example": EXAMPLE}, "cli", True),
        )

        served.clear()
        code, out, err = _call(mod, ["serve"])
        c.eq("serve defaults to this machine, port 8767", (code, served, servers[-1].events), (0, [("127.0.0.1", 8767)], ["serve"]))
        c.eq(
            "serve announces its routes on stderr",
            (out, err),
            ("", "listening on http://127.0.0.1:8767  (POST /tools/exit_liquidity_check/call, GET /tools, GET /health, POST / for MCP)\n"),
        )
        served.clear()
        code, out, err = _call(mod, ["serve", "--host", "0.0.0.0", "--port", "9000"])  # noqa: S104
        c.eq("serve with a host and port", (code, served), (0, [("0.0.0.0", 9000)]))  # noqa: S104
        c.eq("... and says so", err.startswith("listening on http://0.0.0.0:9000  ("), True)
        mod.serve_http = lambda host, port: (servers.append(_Server(stop=True)), servers[-1])[1]
        code, out, err = _call(mod, ["serve"])
        c.eq("Ctrl-C closes the server and exits 0", (code, servers[-1].events), (0, ["serve", "close"]))

        code, out, err = _call(mod, ["--version"])
        c.eq("--version", (code, out.strip()), (("exit", 0), "0.1.0"))
        code, out, err = _call(mod, ["--version~"])
        c.eq("only --version prints the version", (code, out), (("exit", 2), ""))
        code, out, err = _call(mod, [])
        c.eq("a command is required", code, ("exit", 2))
        code, out, err = _call(mod, ["check"])
        c.eq("check needs a token", code, ("exit", 2))
        code, out, err = _call(mod, ["nonsense"])
        c.eq("an unknown command", code, ("exit", 2))
        code, out, err = _call(mod, ["check", "--token", "SOL", "--size-usd", "abc"])
        c.eq("a size that is not a number", code, ("exit", 2))
        code, out, err = _call(mod, ["--help"])
        c.eq("help names the program", (code, out.startswith("usage: exit-liquidity-check [-h] [--version]")), (("exit", 0), True))
        flat = " ".join(out.split())
        for text in (
            "price the exit of one token and print the envelope",
            "describe the coverage (sends nothing)",
            "serve MCP over stdio",
            "serve REST and MCP over HTTP",
            "print both tool definitions",
            "print a working example call",
        ):
            c.eq(f"help says: {text}", re.search(re.escape(text) + r"(\s|$)", flat) is not None, True)
    finally:
        mod.run, mod.run_sources, mod.serve_stdio, mod.serve_http = saved
    return c.failed
