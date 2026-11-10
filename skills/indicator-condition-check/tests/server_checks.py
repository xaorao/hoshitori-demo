"""Checks of the skill's MCP and REST front ends (``indicator_condition_check.server``), runnable against a mutant.

Every expected value is written by hand from the MCP specification and the two official SDK servers (TypeScript and Python); the rule behind each
part is named in its docstring, and the file and line of each source is listed in the B5B hand-off. No expectation is copied from the module's own
output. The runner is a stand-in: it runs the skill's real argument validation (no network) and returns a fixed answer, because these checks are
about the protocol, not about candles. Servers bind 127.0.0.1 on a free port.
"""

from __future__ import annotations

import http.client
import inspect
import io
import json
import re
import socket
import threading
import time
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from indicator_condition_check.schema import TOOL_DEFINITION, validate_input

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPS_KEY = "io.modelcontextprotocol/clientCapabilities"
INFO_KEY = "io.modelcontextprotocol/serverInfo"
META = {VERSION_KEY: MODERN, CAPS_KEY: {}}
IDENTITY = {"name": "indicator-condition-check", "version": "0.2.0"}
STAMP = {INFO_KEY: IDENTITY}
TOOL = "indicator_condition_check"
STAND_IN = {"tool": TOOL, "status": "ok", "stand_in": True}
STAND_IN_TEXT = '{"tool": "indicator_condition_check", "status": "ok", "stand_in": true}'
GOOD_ARGS = {"symbol": "SOL", "condition": {"left": {"indicator": "rsi"}, "op": "<", "right": {"value": 30}}}
HEADERS = {"mcp-protocol-version": MODERN, "mcp-method": "tools/call", "mcp-name": TOOL}
DISAGREE = "Bad Request: the request headers and body disagree: "
UNSUPPORTED_HEADER = "Bad Request: Unsupported protocol version: {} (supported versions: 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05)"
NAME_B64 = "=?base64?aW5kaWNhdG9yX2NvbmRpdGlvbl9jaGVjaw==?="  # base64 of the UTF-8 bytes of "indicator_condition_check"


def stand_in(args: dict[str, Any]) -> dict[str, Any]:
    """The skill's real validation, then a fixed answer (no network, no candles); the symbol BOOM makes it fail as a broken tool would."""
    validate_input(args)
    if args["symbol"] == "BOOM":
        raise RuntimeError("secret internal detail")
    return dict(STAND_IN)


def broken(args: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("secret internal detail")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def true(self, label: str, ok: object) -> None:
        if ok is not True:
            self.bad.append(f"{label}: not true ({ok!r})")

    def guard(self, label: str, body: Callable[[], None]) -> None:
        try:
            body()
        except Exception as exc:  # a mutant that raises is a failed check, never a crash of the checks
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    def expect(self, label: str, produce: Callable[[], object], want: object) -> None:
        try:
            got = produce()
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.eq(label, got, want)


def req(method: str, params: dict[str, Any] | None = None, mid: Any = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def note(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def err(mid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": body}


def ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def modern(result: dict[str, Any]) -> dict[str, Any]:
    return {"resultType": "complete", **result, "_meta": STAMP}


def mismatch(mid: Any, header: str, body: str) -> dict[str, Any]:
    return err(mid, -32020, DISAGREE + body, {"mismatch": {"header": header, "body": body}})


def unsupported(mid: Any, requested: str) -> dict[str, Any]:
    return err(mid, -32022, "Unsupported protocol version", {"supported": [MODERN], "requested": requested})


def envelope(mid: Any, key: str, problem: str) -> dict[str, Any]:
    return err(
        mid,
        -32602,
        f"Invalid _meta envelope for protocol revision 2026-07-28: {key}: {problem}",
        {"envelope": {"key": key, "problem": problem}},
    )


INVALID = err(None, -32600, "Invalid Request")
CALL_OK = {"content": [{"type": "text", "text": STAND_IN_TEXT}], "structuredContent": STAND_IN, "isError": False}
INITIALIZED = {"capabilities": {"tools": {"listChanged": False}}, "serverInfo": IDENTITY}


def call(
    params_extra: dict[str, Any] | None = None, *, name: Any = TOOL, args: Any = None, meta: Any = None, mid: Any = 1
) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name, "arguments": GOOD_ARGS if args is None else args}
    if meta is not None:
        params["_meta"] = meta
    params.update(params_extra or {})
    return req("tools/call", params, mid)


# ----- parts -------------------------------------------------------------------------------------------------------


def part_constants(mod: types.ModuleType, c: Case) -> None:
    """Advertised revisions, error codes and limits (SPEC basic/index error codes; TS requestBody.ts MAX_BATCH_SIZE = 100)."""
    c.eq("legacy revisions, newest first", mod.LEGACY_PROTOCOLS, LEGACY)
    c.eq("modern revisions", mod.MODERN_PROTOCOLS, (MODERN,))
    c.eq("every advertised revision", mod.SUPPORTED_PROTOCOLS, (MODERN, *LEGACY))
    c.eq("header mismatch code", mod.HEADER_MISMATCH, -32020)
    c.eq("unsupported version code", mod.UNSUPPORTED_PROTOCOL_VERSION, -32022)
    c.eq("largest batch", mod.MAX_BATCH, 100)
    c.eq("server identity", mod.SERVER_INFO, IDENTITY)
    c.eq("rate limit per client and minute", mod.RATE_LIMIT_PER_MINUTE, 60)
    c.eq("concurrent requests", mod.MAX_CONCURRENT, 8)
    c.eq("largest body", mod.MAX_BODY, 65536)
    c.eq("largest drain", mod.MAX_DRAIN, 1048576)
    c.eq("tool list freshness", mod.TOOLS_TTL_MS, 300000)
    c.eq("a stalled connection is cut after ten seconds, so it gives its slot back", mod.make_http_handler(stand_in).timeout, 10)
    defaults = inspect.signature(mod.serve_http).parameters
    c.eq("serve_http binds loopback by default (SPEC streamable-http 63-64)", defaults["host"].default, "127.0.0.1")
    c.eq("serve_http default port", defaults["port"].default, 8765)


def part_origin(mod: types.ModuleType, c: Case) -> None:
    """Origin rule (SPEC streamable-http 58-62: validate Origin, 403 when present and invalid): no Origin passes; a loopback origin passes only
    with a loopback Host; listed origins pass."""
    allowed = mod._origin_allowed
    none: frozenset[str] = frozenset()
    for label, origin, host, extra, want in (
        ("no Origin", None, "evil.example", none, True),
        ("an empty Origin", "", "evil.example", none, True),
        ("localhost to localhost", "http://localhost:9", "localhost:9", none, True),
        ("127.0.0.1 to 127.0.0.1", "http://127.0.0.1:9", "127.0.0.1:9", none, True),
        ("localhost to 127.0.0.1", "http://localhost:9", "127.0.0.1", none, True),
        ("::1 to localhost", "http://[::1]:9", "localhost:9", none, True),
        ("::1 to ::1", "http://[::1]:9", "[::1]:9", none, True),
        ("::1 to ::1 without a port", "http://[::1]", "[::1]", none, True),
        ("localhost to ::1", "http://localhost:9", "[::1]:9", none, True),
        ("loopback origin, an IPv6 Host that is not loopback", "http://[::1]:9", "[::2]:9", none, False),
        ("loopback origin, an IPv6 Host with no closing bracket", "http://[::1]:9", "[::1", none, False),
        ("loopback origin, a bare IPv6 Host (a Host header brackets IPv6, RFC 3986 3.2.2)", "http://[::1]:9", "::1", none, False),
        ("a foreign origin", "https://evil.example", "127.0.0.1:9", none, False),
        ("loopback origin, foreign Host", "http://localhost:9", "evil.example:9", none, False),
        ("loopback origin, no Host", "http://localhost:9", None, none, False),
        ("a listed origin", "https://evil.example", "evil.example", frozenset({"https://evil.example"}), True),
        ("an origin that does not parse", "http://[bad", "127.0.0.1", none, False),
    ):
        c.expect(f"origin: {label}", partial(allowed, origin, host, extra), want)


def part_shape(mod: types.ModuleType, c: Case) -> None:
    """JSON-RPC shape: ids are strings or integers, never null (SPEC 2026-07-28 basic/index 46-47); method a string and params an object (TS
    core schemas.ts 74-90, 131-152); malformed bodies are 400 -32600 (TS inboundClassification.ts 950-956); posted responses are accepted
    without a reply (TS streamableHttp.ts 907-916); a request always gets an answer (TS protocol.ts 1065-1069)."""
    a = mod.answer
    for label, body in (
        ("a string", "garbage"),
        ("a number", 5),
        ("jsonrpc 1.0", {"jsonrpc": "1.0", "id": 1, "method": "ping"}),
        ("no jsonrpc member", {"id": 1, "method": "ping"}),
        ("a null id", {"jsonrpc": "2.0", "id": None, "method": "ping"}),
        ("a boolean id", {"jsonrpc": "2.0", "id": True, "method": "ping"}),
        ("a fractional id", {"jsonrpc": "2.0", "id": 1.5, "method": "ping"}),
        ("an object id", {"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"}),
        ("a method that is not text", {"jsonrpc": "2.0", "id": 1, "method": 5}),
        ("params that are a list", {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]}),
        ("params that are null", {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": None}),
        ("a notification with list params", {"jsonrpc": "2.0", "method": "notifications/initialized", "params": [1]}),
        ("result and error together", {"jsonrpc": "2.0", "id": 7, "result": {}, "error": {"code": 1, "message": "x"}}),
        ("neither method nor result", {"jsonrpc": "2.0", "id": 7}),
        ("a result with a null id", {"jsonrpc": "2.0", "id": None, "result": {}}),
    ):
        c.expect(label, partial(a, body, stand_in, None), (400, INVALID))
    c.guard("handle_rpc", lambda: c.eq("handle_rpc of a string", mod.handle_rpc("garbage", stand_in), INVALID))
    c.guard("posted result", lambda: c.eq("a posted result", a({"jsonrpc": "2.0", "id": 7, "result": {}}, stand_in, None), (202, None)))
    c.guard(
        "posted error",
        lambda: c.eq("a posted error", a({"jsonrpc": "2.0", "id": 7, "error": {"code": 1, "message": "x"}}, stand_in, None), (202, None)),
    )
    c.guard(
        "posted error no id",
        lambda: c.eq("a posted error without id", a({"jsonrpc": "2.0", "error": {"code": 1, "message": "x"}}, stand_in, None), (202, None)),
    )
    c.guard("text id", lambda: c.eq("a text id", a(req("ping", mid="abc"), stand_in, None), (200, ok("abc", {}))))
    c.guard("zero id", lambda: c.eq("id 0", a(req("ping", mid=0), stand_in, None), (200, ok(0, {}))))
    c.guard("negative id", lambda: c.eq("id -3", a(req("ping", mid=-3), stand_in, None), (200, ok(-3, {}))))
    c.guard(
        "request named like a notification",
        lambda: c.eq(
            "a request named notifications/initialized",
            a(req("notifications/initialized", mid=3), stand_in, None),
            (200, err(3, -32601, "Method not found: notifications/initialized")),
        ),
    )
    c.guard("notification", lambda: c.eq("a notification", a(note("notifications/initialized"), stand_in, None), (202, None)))
    c.guard(
        "handle_rpc notification",
        lambda: c.eq("handle_rpc of a notification", mod.handle_rpc(note("notifications/cancelled", {"requestId": 1}), stand_in), None),
    )


def part_legacy(mod: types.ModuleType, c: Case) -> None:
    """The handshake era: initialize echoes a served revision and otherwise answers the latest one (SPEC 2025-11-25 lifecycle 170-172; TS
    server.ts 939-942; PY runner.py 425); a malformed or pre-2026 claim keeps initialize legacy (TS inboundClassification.ts 745-763); ping,
    tools/list and tools/call carry no 2026 fields; server/discover is not a legacy method (PY runner.py 193-197)."""
    a = mod.answer
    for v in LEGACY:
        c.expect(
            f"initialize {v}",
            partial(a, req("initialize", {"protocolVersion": v}), stand_in, None),
            (200, ok(1, {"protocolVersion": v, **INITIALIZED})),
        )
    latest = (200, ok(1, {"protocolVersion": "2025-11-25", **INITIALIZED}))
    c.guard(
        "initialize unknown",
        lambda: c.eq("initialize 1999-01-01", a(req("initialize", {"protocolVersion": "1999-01-01"}), stand_in, None), latest),
    )
    c.guard("initialize empty", lambda: c.eq("initialize with no params", a(req("initialize", {}), stand_in, None), latest))
    c.guard("initialize no params", lambda: c.eq("initialize without params", a(req("initialize"), stand_in, None), latest))
    c.guard(
        "initialize modern asked",
        lambda: c.eq("initialize asking 2026-07-28", a(req("initialize", {"protocolVersion": MODERN}), stand_in, None), latest),
    )
    c.guard(
        "initialize malformed claim",
        lambda: c.eq(
            "initialize whose claim lacks capabilities",
            a(req("initialize", {"protocolVersion": "2025-06-18", "_meta": {VERSION_KEY: MODERN}}), stand_in, None),
            (200, ok(1, {"protocolVersion": "2025-06-18", **INITIALIZED})),
        ),
    )
    c.guard(
        "initialize legacy claim",
        lambda: c.eq(
            "initialize whose claim names 2025-11-25",
            a(req("initialize", {"protocolVersion": "2024-11-05", "_meta": {VERSION_KEY: "2025-11-25", CAPS_KEY: {}}}), stand_in, None),
            (200, ok(1, {"protocolVersion": "2024-11-05", **INITIALIZED})),
        ),
    )
    c.guard("ping", lambda: c.eq("legacy ping", a(req("ping", mid=2), stand_in, None), (200, ok(2, {}))))
    c.guard(
        "tools/list",
        lambda: c.eq("legacy tools/list", a(req("tools/list", mid=3), stand_in, None), (200, ok(3, {"tools": [TOOL_DEFINITION]}))),
    )
    c.guard("tools/call", lambda: c.eq("legacy tools/call", a(call(mid=4), stand_in, None), (200, ok(4, CALL_OK))))

    def invalid_arguments() -> None:
        status, reply = a(call(args={"symbol": "SOL"}), stand_in, None)
        c.eq("invalid arguments status", status, 200)
        result = reply["result"]
        c.eq("invalid arguments members", sorted(result), ["content", "isError"])
        c.eq("invalid arguments isError", result["isError"], True)
        c.eq("invalid arguments content is one text item", [sorted(item) for item in result["content"]], [["text", "type"]])
        c.eq("invalid arguments content type", result["content"][0].get("type"), "text")
        body = json.loads(result["content"][0]["text"])
        c.eq("invalid arguments code", body["code"], "INVALID_ARGUMENTS")
        c.eq("invalid arguments message", body["message"], "condition: is required")
        c.eq("invalid arguments field", body["details"]["field"], "condition")
        c.true(
            "invalid arguments trace id", isinstance(body["trace_id"], str) and re.fullmatch(r"[0-9a-f]{32}", body["trace_id"]) is not None
        )

    c.guard("invalid arguments", invalid_arguments)

    def internal_error() -> None:
        status, reply = a(call(), broken, None)
        c.eq("internal error status", status, 200)
        result = reply["result"]
        c.eq("internal error isError", result["isError"], True)
        body = json.loads(result["content"][0]["text"])
        c.eq("internal error code", body["code"], "INTERNAL_ERROR")
        c.eq("internal error message is the class name only", body["message"], "RuntimeError")
        c.true("internal detail is not leaked", "secret" not in result["content"][0]["text"])

    c.guard("internal error", internal_error)
    c.guard(
        "unknown tool",
        lambda: c.eq("legacy unknown tool", a(call(name="nope", mid=5), stand_in, None), (200, err(5, -32602, "Unknown tool: 'nope'"))),
    )
    c.guard(
        "discover legacy",
        lambda: c.eq(
            "claim-less server/discover",
            a(req("server/discover", mid=6), stand_in, None),
            (200, err(6, -32601, "Method not found: server/discover")),
        ),
    )
    c.guard(
        "unknown legacy",
        lambda: c.eq("legacy unknown method", a(req("no/such", mid=7), stand_in, None), (200, err(7, -32601, "Method not found: no/such"))),
    )
    c.guard(
        "cancelled",
        lambda: c.eq("legacy notifications/cancelled", a(note("notifications/cancelled", {"requestId": 1}), stand_in, None), (202, None)),
    )


def part_stateless(mod: types.ModuleType, c: Case) -> None:
    """The 2026-07-28 era without headers (stdio): the envelope is validated first (SPEC basic/index 373-382; TS inboundClassification.ts
    765-784), then the version gate (SPEC streamable-http 263-269, versioning 48-66, schema.ts 483-501; TS createMcpHandler.ts 731-740); every
    result carries resultType and the identity in _meta (SPEC basic/index 73, 396-402; TS encodeContract.ts 142-154; PY runner.py 396-418);
    discover lists the modern revisions only (TS server.ts 964-972; PY lowlevel/server.py 683-687); ping is gone (SPEC changelog 20)."""
    a = mod.answer
    discover = modern(
        {"supportedVersions": [MODERN], "capabilities": {"tools": {"listChanged": False}}, "ttlMs": 300000, "cacheScope": "public"}
    )
    c.guard("discover", lambda: c.eq("server/discover", a(req("server/discover", {"_meta": META}), stand_in, None), (200, ok(1, discover))))
    c.guard(
        "tools/list",
        lambda: c.eq(
            "stateless tools/list",
            a(req("tools/list", {"_meta": META}, 2), stand_in, None),
            (200, ok(2, modern({"tools": [TOOL_DEFINITION], "ttlMs": 300000, "cacheScope": "public"}))),
        ),
    )
    c.guard("tools/call", lambda: c.eq("stateless tools/call", a(call(meta=META, mid=3), stand_in, None), (200, ok(3, modern(CALL_OK)))))

    def invalid_arguments() -> None:
        status, reply = a(call(args={"symbol": "SOL"}, meta=META), stand_in, None)
        c.eq("stateless invalid arguments status", status, 200)
        c.eq("stateless invalid arguments members", sorted(reply["result"]), ["_meta", "content", "isError", "resultType"])
        c.eq("stateless invalid arguments stamp", reply["result"]["_meta"], STAMP)
        c.eq("stateless invalid arguments isError", reply["result"]["isError"], True)

    c.guard("invalid arguments", invalid_arguments)
    c.guard(
        "unknown tool",
        lambda: c.eq(
            "stateless unknown tool", a(call(name="nope", meta=META, mid=4), stand_in, None), (200, err(4, -32602, "Unknown tool: 'nope'"))
        ),
    )
    c.guard(
        "ping",
        lambda: c.eq("stateless ping", a(req("ping", {"_meta": META}, 5), stand_in, None), (404, err(5, -32601, "Method not found: ping"))),
    )
    c.guard(
        "unknown",
        lambda: c.eq(
            "stateless unknown method",
            a(req("no/such", {"_meta": META}, 6), stand_in, None),
            (404, err(6, -32601, "Method not found: no/such")),
        ),
    )
    c.guard(
        "initialize modern",
        lambda: c.eq(
            "initialize with a valid modern claim",
            a(req("initialize", {"protocolVersion": "2025-11-25", "_meta": META}, 7), stand_in, None),
            (404, err(7, -32601, "Method not found: initialize")),
        ),
    )
    future = {VERSION_KEY: "2031-01-01", CAPS_KEY: {}}
    c.guard(
        "initialize future",
        lambda: c.eq(
            "initialize claiming 2031-01-01",
            a(req("initialize", {"_meta": future}, 8), stand_in, None),
            (400, unsupported(8, "2031-01-01")),
        ),
    )
    for v in ("2025-11-25", "2024-11-05", "2031-01-01", "latest"):
        c.expect(
            f"tools/list claiming {v}",
            partial(a, req("tools/list", {"_meta": {VERSION_KEY: v, CAPS_KEY: {}}}, 9), stand_in, None),
            (400, unsupported(9, v)),
        )
    c.guard(
        "version not text",
        lambda: c.eq(
            "a claim that is a number",
            a(req("tools/list", {"_meta": {VERSION_KEY: 20260728, CAPS_KEY: {}}}, 10), stand_in, None),
            (400, envelope(10, VERSION_KEY, "expected a string")),
        ),
    )
    c.guard(
        "null version",
        lambda: c.eq(
            "a null claim",
            a(req("tools/list", {"_meta": {VERSION_KEY: None, CAPS_KEY: {}}}, 10), stand_in, None),
            (400, envelope(10, VERSION_KEY, "expected a string")),
        ),
    )
    c.expect(
        "a claim that is a number and no capabilities: the missing key first (TS codec.ts 138-154)",
        partial(a, req("tools/list", {"_meta": {VERSION_KEY: 7}}, 10), stand_in, None),
        (400, envelope(10, CAPS_KEY, "missing")),
    )
    c.guard(
        "no capabilities",
        lambda: c.eq(
            "no clientCapabilities",
            a(req("tools/list", {"_meta": {VERSION_KEY: MODERN}}, 11), stand_in, None),
            (400, envelope(11, CAPS_KEY, "missing")),
        ),
    )
    c.guard(
        "capabilities not object",
        lambda: c.eq(
            "clientCapabilities that are text",
            a(req("tools/list", {"_meta": {VERSION_KEY: MODERN, CAPS_KEY: "x"}}, 12), stand_in, None),
            (400, envelope(12, CAPS_KEY, "expected an object")),
        ),
    )
    c.guard(
        "envelope before version",
        lambda: c.eq(
            "an unserved claim without capabilities",
            a(req("tools/list", {"_meta": {VERSION_KEY: "2031-01-01"}}, 13), stand_in, None),
            (400, envelope(13, CAPS_KEY, "missing")),
        ),
    )
    c.guard(
        "meta not object",
        lambda: c.eq(
            "_meta that is a list is no claim",
            a(req("tools/list", {"_meta": [1]}, 14), stand_in, None),
            (200, ok(14, {"tools": [TOOL_DEFINITION]})),
        ),
    )
    c.guard(
        "notification served",
        lambda: c.eq(
            "a notification with a served claim", a(note("notifications/cancelled", {"_meta": META}), stand_in, None), (202, None)
        ),
    )
    c.guard(
        "notification no caps",
        lambda: c.eq(
            "a notification claim needs no capabilities",
            a(note("notifications/cancelled", {"_meta": {VERSION_KEY: MODERN}}), stand_in, None),
            (202, None),
        ),
    )
    c.guard(
        "notification unserved",
        lambda: c.eq(
            "a notification with an unserved claim",
            a(note("notifications/cancelled", {"_meta": {VERSION_KEY: "2031-01-01"}}), stand_in, None),
            (400, unsupported(None, "2031-01-01")),
        ),
    )
    c.guard(
        "notification bad version",
        lambda: c.eq(
            "a notification claim that is a number",
            a(note("notifications/cancelled", {"_meta": {VERSION_KEY: 7}}), stand_in, None),
            (400, envelope(None, VERSION_KEY, "expected a string")),
        ),
    )
    c.guard(
        "handle_rpc stateless",
        lambda: c.eq("handle_rpc stateless tools/call", mod.handle_rpc(call(meta=META, mid=15), stand_in), ok(15, modern(CALL_OK))),
    )


def part_headers(mod: types.ModuleType, c: Case) -> None:
    """Headers on HTTP. 2026-07-28: version and method mismatches before the version gate, presence and Mcp-Name after it, all 400 -32020
    (SPEC streamable-http 252-261, 286-293, 478-508, 570-630; TS inboundClassification.ts 485-488, 520-537, 578-653, 786-800,
    createMcpHandler.ts 743-760; sentinel TS mcpParamHeaders.ts 194-198, 274-285). Legacy: an unserved header is 400 (SPEC 2025-11-25
    transports 279-280; TS inboundClassification.ts 752-758, 804-829, streamableHttp.ts 884-889, 1110-1119; PY inbound.py 433-438)."""
    a = mod.answer
    body_says = f"the body envelope names protocol version {MODERN}"
    c.guard(
        "all headers",
        lambda: c.eq("tools/call with every header", a(call(meta=META), stand_in, dict(HEADERS)), (200, ok(1, modern(CALL_OK)))),
    )
    c.guard(
        "whitespace stripped",
        lambda: c.eq(
            "header values with outer spaces and tabs",
            a(call(meta=META), stand_in, {"mcp-protocol-version": f" {MODERN}\t", "mcp-method": "\ttools/call ", "mcp-name": f"  {TOOL} "}),
            (200, ok(1, modern(CALL_OK))),
        ),
    )
    c.guard(
        "base64 name",
        lambda: c.eq(
            "an Mcp-Name in the Base64 sentinel",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-name": NAME_B64}),
            (200, ok(1, modern(CALL_OK))),
        ),
    )
    missing = f"{body_says} but the required MCP-Protocol-Version header is absent"
    c.guard(
        "no version header",
        lambda: c.eq(
            "no MCP-Protocol-Version",
            a(call(meta=META), stand_in, {"mcp-method": "tools/call", "mcp-name": TOOL}),
            (400, mismatch(1, "(missing)", missing)),
        ),
    )
    c.guard("no headers at all", lambda: c.eq("no headers", a(call(meta=META), stand_in, {}), (400, mismatch(1, "(missing)", missing))))
    other = f"{body_says} but the MCP-Protocol-Version header names 2025-11-25"
    c.guard(
        "version header differs",
        lambda: c.eq(
            "MCP-Protocol-Version 2025-11-25",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-protocol-version": "2025-11-25"}),
            (400, mismatch(1, "2025-11-25", other)),
        ),
    )
    future = {VERSION_KEY: "2031-01-01", CAPS_KEY: {}}
    first = "the body envelope names protocol version 2031-01-01 but the MCP-Protocol-Version header names 2026-07-28"
    c.guard(
        "mismatch before gate",
        lambda: c.eq(
            "an unserved claim with a disagreeing header", a(call(meta=future), stand_in, dict(HEADERS)), (400, mismatch(1, MODERN, first))
        ),
    )
    c.guard(
        "gate on agreement",
        lambda: c.eq(
            "an unserved claim with an agreeing header",
            a(call(meta=future), stand_in, {**HEADERS, "mcp-protocol-version": "2031-01-01"}),
            (400, unsupported(1, "2031-01-01")),
        ),
    )
    c.guard(
        "gate before presence",
        lambda: c.eq("an unserved claim and no headers", a(call(meta=future), stand_in, {}), (400, unsupported(1, "2031-01-01"))),
    )
    c.guard(
        "no method header",
        lambda: c.eq(
            "no Mcp-Method",
            a(call(meta=META), stand_in, {"mcp-protocol-version": MODERN, "mcp-name": TOOL}),
            (400, mismatch(1, "(missing)", "the body names method tools/call but the required Mcp-Method header is absent")),
        ),
    )
    c.guard(
        "method header differs",
        lambda: c.eq(
            "Mcp-Method tools/list",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-method": "tools/list"}),
            (400, mismatch(1, "tools/list", "the body names method tools/call but the Mcp-Method header names tools/list")),
        ),
    )
    c.guard(
        "method header case",
        lambda: c.eq(
            "Mcp-Method in other case",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-method": "Tools/Call"}),
            (400, mismatch(1, "Tools/Call", "the body names method tools/call but the Mcp-Method header names Tools/Call")),
        ),
    )
    named = f'the body carries params.name="{TOOL}"'
    c.guard(
        "no name header",
        lambda: c.eq(
            "no Mcp-Name",
            a(call(meta=META), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "tools/call"}),
            (400, mismatch(1, "(missing)", f"{named} but the required Mcp-Name header is absent")),
        ),
    )
    c.guard(
        "name header differs",
        lambda: c.eq(
            "Mcp-Name other",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-name": "other"}),
            (400, mismatch(1, "other", f'{named} but the Mcp-Name header names "other"')),
        ),
    )
    c.guard(
        "only spaces and tabs are trimmed",
        lambda: c.eq(
            "an Mcp-Name with one more character at its end",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-name": f"{TOOL}~"}),
            (400, mismatch(1, f"{TOOL}~", f'{named} but the Mcp-Name header names "{TOOL}~"')),
        ),
    )
    c.guard(
        "notification method header differs",
        lambda: c.eq(
            "a notification claim with a disagreeing Mcp-Method",
            a(note("notifications/cancelled", {"_meta": META}), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "tools/list"}),
            (
                400,
                mismatch(
                    None,
                    "tools/list",
                    "the notification body names method notifications/cancelled but the Mcp-Method header names tools/list",
                ),
            ),
        ),
    )
    old_claim = {VERSION_KEY: "2025-11-25", CAPS_KEY: {}}
    c.guard(
        "method mismatch before the gate",
        lambda: c.eq(
            "an unserved claim with an agreeing version header and a disagreeing Mcp-Method",
            a(req("tools/list", {"_meta": old_claim}), stand_in, {"mcp-protocol-version": "2025-11-25", "mcp-method": "tools/call"}),
            (400, mismatch(1, "tools/call", "the body names method tools/list but the Mcp-Method header names tools/call")),
        ),
    )
    bad_sentinel = "the Mcp-Name header carries an invalid Base64 sentinel value"
    for label, value in (
        ("characters outside Base64", "=?base64?***?="),
        ("no padding", "=?base64?QQ?="),
        ("bytes that are not UTF-8", "=?base64?/w==?="),
    ):
        c.expect(
            f"Mcp-Name with {label}",
            partial(a, call(meta=META), stand_in, {**HEADERS, "mcp-name": value}),
            (400, mismatch(1, value, bad_sentinel)),
        )
    c.guard(
        "upper-case markers are not a sentinel",
        lambda: c.eq(
            "Mcp-Name =?BASE64?...?=",
            a(call(meta=META), stand_in, {**HEADERS, "mcp-name": "=?BASE64?QQ==?="}),
            (400, mismatch(1, "=?BASE64?QQ==?=", f'{named} but the Mcp-Name header names "=?BASE64?QQ==?="')),
        ),
    )
    prompt = req("prompts/get", {"name": "p", "_meta": META})
    c.guard(
        "prompts/get needs Mcp-Name",
        lambda: c.eq(
            "prompts/get without Mcp-Name",
            a(prompt, stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "prompts/get"}),
            (400, mismatch(1, "(missing)", 'the body carries params.name="p" but the required Mcp-Name header is absent')),
        ),
    )
    resource = req("resources/read", {"uri": "x://y", "_meta": META})
    c.guard(
        "resources/read needs Mcp-Name",
        lambda: c.eq(
            "resources/read without Mcp-Name",
            a(resource, stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "resources/read"}),
            (400, mismatch(1, "(missing)", 'the body carries params.uri="x://y" but the required Mcp-Name header is absent')),
        ),
    )
    c.guard(
        "resources/read with Mcp-Name",
        lambda: c.eq(
            "resources/read with Mcp-Name",
            a(resource, stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "resources/read", "mcp-name": "x://y"}),
            (404, err(1, -32601, "Method not found: resources/read")),
        ),
    )
    c.guard(
        "name not text",
        lambda: c.eq(
            "a tools/call whose name is a number needs no Mcp-Name",
            a(call(name=5, meta=META), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "tools/call"}),
            (200, err(1, -32602, "Unknown tool: 5")),
        ),
    )
    c.guard(
        "list needs no name",
        lambda: c.eq(
            "tools/list needs no Mcp-Name",
            a(req("tools/list", {"_meta": META}), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "tools/list"}),
            (200, ok(1, modern({"tools": [TOOL_DEFINITION], "ttlMs": 300000, "cacheScope": "public"}))),
        ),
    )
    c.guard(
        "unknown with headers",
        lambda: c.eq(
            "unknown method with headers",
            a(req("no/such", {"_meta": META}), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "no/such"}),
            (404, err(1, -32601, "Method not found: no/such")),
        ),
    )
    c.guard(
        "notification version differs",
        lambda: c.eq(
            "a notification claim with a disagreeing header",
            a(note("notifications/cancelled", {"_meta": META}), stand_in, {"mcp-protocol-version": "2025-11-25"}),
            (
                400,
                mismatch(
                    None,
                    "2025-11-25",
                    f"the notification envelope names protocol version {MODERN} but the MCP-Protocol-Version header names 2025-11-25",
                ),
            ),
        ),
    )
    c.guard(
        "notification needs no method header",
        lambda: c.eq(
            "a notification claim without Mcp-Method",
            a(note("notifications/cancelled", {"_meta": META}), stand_in, {"mcp-protocol-version": MODERN}),
            (202, None),
        ),
    )
    # legacy era over HTTP
    c.guard(
        "legacy header served",
        lambda: c.eq(
            "legacy tools/list with 2025-06-18",
            a(req("tools/list"), stand_in, {"mcp-protocol-version": "2025-06-18"}),
            (200, ok(1, {"tools": [TOOL_DEFINITION]})),
        ),
    )
    c.guard(
        "legacy no header",
        lambda: c.eq("legacy tools/list without a header", a(req("tools/list"), stand_in, {}), (200, ok(1, {"tools": [TOOL_DEFINITION]}))),
    )
    no_meta = f"Invalid params: the MCP-Protocol-Version header names protocol revision {MODERN}, but the request is missing the required per-request envelope key(s): _meta"
    c.guard(
        "modern header no meta",
        lambda: c.eq(
            "a modern header without _meta",
            a(req("tools/list"), stand_in, {"mcp-protocol-version": MODERN}),
            (400, err(1, -32602, no_meta, {"envelope": {"missing": ["_meta"]}})),
        ),
    )
    both = f"Invalid params: the MCP-Protocol-Version header names protocol revision {MODERN}, but the request is missing the required per-request envelope key(s): {VERSION_KEY}, {CAPS_KEY}"
    c.guard(
        "modern header empty meta",
        lambda: c.eq(
            "a modern header with an empty _meta",
            a(req("tools/list", {"_meta": {}}), stand_in, {"mcp-protocol-version": MODERN}),
            (400, err(1, -32602, both, {"envelope": {"missing": [VERSION_KEY, CAPS_KEY]}})),
        ),
    )
    one = f"Invalid params: the MCP-Protocol-Version header names protocol revision {MODERN}, but the request is missing the required per-request envelope key(s): {VERSION_KEY}"
    c.guard(
        "modern header caps only",
        lambda: c.eq(
            "a modern header with capabilities only",
            a(req("tools/list", {"_meta": {CAPS_KEY: {}}}), stand_in, {"mcp-protocol-version": MODERN}),
            (400, err(1, -32602, one, {"envelope": {"missing": [VERSION_KEY]}})),
        ),
    )
    foo = "Invalid params: the MCP-Protocol-Version header names protocol revision foo, but the request is missing the required per-request envelope key(s): _meta"
    c.guard(
        "header foo",
        lambda: c.eq(
            "header foo sorts after 2026-07-28",
            a(req("tools/list"), stand_in, {"mcp-protocol-version": "foo"}),
            (400, err(1, -32602, foo, {"envelope": {"missing": ["_meta"]}})),
        ),
    )
    c.guard(
        "unserved legacy header",
        lambda: c.eq(
            "legacy tools/list with 2025-01-01",
            a(req("tools/list"), stand_in, {"mcp-protocol-version": "2025-01-01"}),
            (400, err(1, -32602, UNSUPPORTED_HEADER.format("2025-01-01"))),
        ),
    )
    initialize_modern = "an initialize request (legacy handshake) was sent with a modern MCP-Protocol-Version header"
    c.guard(
        "initialize modern header",
        lambda: c.eq(
            "initialize with a modern header",
            a(req("initialize", {"protocolVersion": "2025-11-25"}), stand_in, {"mcp-protocol-version": MODERN}),
            (400, mismatch(1, MODERN, initialize_modern)),
        ),
    )
    c.guard(
        "initialize skips legacy header",
        lambda: c.eq(
            "initialize with 2025-01-01",
            a(req("initialize", {"protocolVersion": "2025-06-18"}), stand_in, {"mcp-protocol-version": "2025-01-01"}),
            (200, ok(1, {"protocolVersion": "2025-06-18", **INITIALIZED})),
        ),
    )
    c.guard(
        "legacy notification unserved header",
        lambda: c.eq(
            "legacy notification with 2025-01-01",
            a(note("notifications/initialized"), stand_in, {"mcp-protocol-version": "2025-01-01"}),
            (400, err(None, -32602, UNSUPPORTED_HEADER.format("2025-01-01"))),
        ),
    )
    c.guard(
        "legacy notification modern header",
        lambda: c.eq(
            "notification with only a modern header",
            a(note("notifications/initialized"), stand_in, {"mcp-protocol-version": MODERN}),
            (202, None),
        ),
    )
    c.guard(
        "legacy notification future header",
        lambda: c.eq(
            "notification with only header 2031-01-01",
            a(note("notifications/initialized"), stand_in, {"mcp-protocol-version": "2031-01-01"}),
            (400, unsupported(None, "2031-01-01")),
        ),
    )
    c.guard(
        "legacy notification method header",
        lambda: c.eq(
            "notification with a modern header and another Mcp-Method",
            a(note("notifications/initialized"), stand_in, {"mcp-protocol-version": MODERN, "mcp-method": "x"}),
            (400, mismatch(None, "x", "the notification body names method notifications/initialized but the Mcp-Method header names x")),
        ),
    )
    c.guard(
        "legacy method header ignored",
        lambda: c.eq("legacy request with another Mcp-Method", a(req("ping"), stand_in, {"mcp-method": "tools/list"}), (200, ok(1, {}))),
    )


def part_batch(mod: types.ModuleType, c: Case) -> None:
    """Batches, which 2025-03-26 MUST receive (SPEC 2025-03-26 basic/index 92-97): TS rules and wording (inboundClassification.ts 693-737;
    streamableHttp.ts 828-830, 863-866, 907-916, 1110-1119)."""
    a = mod.answer
    c.guard("empty", lambda: c.eq("an empty batch", a([], stand_in, None), (400, err(None, -32600, "Bad Request: empty JSON-RPC batch"))))
    c.guard(
        "two pings", lambda: c.eq("two pings", a([req("ping", mid=1), req("ping", mid=2)], stand_in, None), (200, [ok(1, {}), ok(2, {})]))
    )
    c.guard(
        "notifications only",
        lambda: c.eq(
            "two notifications", a([note("notifications/initialized"), note("notifications/cancelled")], stand_in, None), (202, None)
        ),
    )
    c.guard(
        "mixed",
        lambda: c.eq(
            "a ping, a notification, a response",
            a([req("ping", mid=1), note("notifications/initialized"), {"jsonrpc": "2.0", "id": 9, "result": {}}], stand_in, None),
            (200, [ok(1, {})]),
        ),
    )
    invalid = err(None, -32600, "Bad Request: JSON-RPC batch contains an invalid message")
    claimed = err(None, -32600, "Bad Request: JSON-RPC batches may not contain requests for protocol revision 2026-07-28 or later")
    modern_element = req("tools/list", {"_meta": META}, 2)
    c.guard("invalid element", lambda: c.eq("an invalid element", a([req("ping"), "x"], stand_in, None), (400, invalid)))
    c.guard(
        "null id element",
        lambda: c.eq(
            "an element with a null id", a([req("ping"), {"jsonrpc": "2.0", "id": None, "method": "ping"}], stand_in, None), (400, invalid)
        ),
    )
    c.guard("modern element", lambda: c.eq("a modern element", a([req("ping"), modern_element], stand_in, None), (400, claimed)))
    c.guard(
        "invalid first", lambda: c.eq("an invalid element before a modern one", a(["x", modern_element], stand_in, None), (400, invalid))
    )
    c.guard(
        "modern first", lambda: c.eq("a modern element before an invalid one", a([modern_element, "x"], stand_in, None), (400, claimed))
    )
    c.guard(
        "claim on notification",
        lambda: c.eq(
            "a modern notification element",
            a([note("notifications/cancelled", {"_meta": {VERSION_KEY: MODERN}})], stand_in, None),
            (400, claimed),
        ),
    )
    pings = [req("ping", mid=i) for i in range(100)]
    c.guard("hundred", lambda: c.eq("one hundred pings", a(pings, stand_in, None), (200, [ok(i, {}) for i in range(100)])))
    c.guard(
        "too many",
        lambda: c.eq(
            "101 pings",
            a([*pings, req("ping", mid=100)], stand_in, None),
            (400, err(None, -32600, "Invalid Request: Batch must not exceed 100 messages")),
        ),
    )
    c.guard("too many, one invalid", lambda: c.eq("101 elements, one invalid", a([*pings, "x"], stand_in, None), (400, invalid)))
    once = err(None, -32600, "Invalid Request: Only one initialization request is allowed")
    c.guard(
        "initialize and ping",
        lambda: c.eq(
            "initialize with a ping",
            a([req("initialize", {"protocolVersion": "2025-03-26"}), req("ping", mid=2)], stand_in, None),
            (400, once),
        ),
    )
    c.guard(
        "initialize alone",
        lambda: c.eq(
            "a batch of one initialize",
            a([req("initialize", {"protocolVersion": "2025-03-26"})], stand_in, None),
            (200, [ok(1, {"protocolVersion": "2025-03-26", **INITIALIZED})]),
        ),
    )
    c.guard(
        "initialize notification",
        lambda: c.eq(
            "a notification named initialize is not an initialization",
            a([note("initialize"), req("ping", mid=2)], stand_in, None),
            (200, [ok(2, {})]),
        ),
    )
    c.guard(
        "unserved header",
        lambda: c.eq(
            "a batch with header 2025-01-01",
            a([req("ping")], stand_in, {"mcp-protocol-version": "2025-01-01"}),
            (400, err(None, -32602, UNSUPPORTED_HEADER.format("2025-01-01"))),
        ),
    )
    c.guard(
        "modern header",
        lambda: c.eq(
            "a batch with header 2026-07-28",
            a([req("ping")], stand_in, {"mcp-protocol-version": MODERN}),
            (400, err(None, -32602, UNSUPPORTED_HEADER.format(MODERN))),
        ),
    )
    c.guard(
        "served header",
        lambda: c.eq(
            "a batch with header 2025-03-26", a([req("ping")], stand_in, {"mcp-protocol-version": "2025-03-26"}), (200, [ok(1, {})])
        ),
    )
    c.guard(
        "initialize skips header",
        lambda: c.eq(
            "a batch of one initialize with header 2025-01-01",
            a([req("initialize", {"protocolVersion": "2025-03-26"})], stand_in, {"mcp-protocol-version": "2025-01-01"}),
            (200, [ok(1, {"protocolVersion": "2025-03-26", **INITIALIZED})]),
        ),
    )
    c.guard(
        "tool error in batch",
        lambda: c.eq(
            "an unknown tool inside a batch", a([call(name="nope", mid=3)], stand_in, None), (200, [err(3, -32602, "Unknown tool: 'nope'")])
        ),
    )
    c.guard(
        "unknown in batch",
        lambda: c.eq(
            "an unknown method inside a batch",
            a([req("no/such", mid=4)], stand_in, None),
            (200, [err(4, -32601, "Method not found: no/such")]),
        ),
    )
    c.guard("handle_rpc batch", lambda: c.eq("handle_rpc of a batch", mod.handle_rpc([req("ping", mid=5)], stand_in), [ok(5, {})]))
    c.guard(
        "handle_rpc empty batch",
        lambda: c.eq("handle_rpc of an empty batch", mod.handle_rpc([], stand_in), err(None, -32600, "Bad Request: empty JSON-RPC batch")),
    )
    c.guard(
        "handle_rpc silent batch",
        lambda: c.eq("handle_rpc of a notification batch", mod.handle_rpc([note("notifications/initialized")], stand_in), None),
    )


def part_codec(mod: types.ModuleType, c: Case) -> None:
    """The Mcp-Name sentinel (SPEC streamable-http 478-508; TS mcpParamHeaders.ts 194-198, 274-285) and the Content-Type test (TS
    mediaType.ts 26-57)."""
    d = mod.decode_header_value
    c.guard("decode", lambda: c.eq("a plain value", d("plain"), "plain"))
    c.guard("decode b64", lambda: c.eq("a sentinel value", d("=?base64?aW5kaWNhdG9y?="), "indicator"))
    c.guard("decode utf8", lambda: c.eq("a non-ASCII value", d("=?base64?w6k=?="), "\u00e9"))
    c.guard("decode empty", lambda: c.eq("an empty sentinel", d("=?base64??="), ""))
    c.guard("decode unpadded", lambda: c.eq("an unpadded sentinel", d("=?base64?QQ?="), None))
    c.guard("decode bad chars", lambda: c.eq("a sentinel with bad characters", d("=?base64?Q*==?="), None))
    c.guard("decode bad utf8", lambda: c.eq("a sentinel of bytes that are not UTF-8", d("=?base64?/w==?="), None))
    c.guard("decode upper", lambda: c.eq("upper-case markers", d("=?BASE64?QQ==?="), "=?BASE64?QQ==?="))
    c.guard("decode prefix only", lambda: c.eq("prefix without suffix", d("=?base64?QQ=="), "=?base64?QQ=="))
    j = mod.is_json_content_type
    for value, want in (
        ("application/json", True),
        ("application/json; charset=utf-8", True),
        ("Application/JSON", True),
        (" application/json ", True),
        ("application/json;", True),
        ("text/plain", False),
        ("application/json-patch+json", False),
        ("application/jsonx", False),
        ("", False),
        (None, False),
        ("text/plain; x=application/json", False),
    ):
        c.expect(f"Content-Type {value!r}", partial(j, value), want)


def part_stdio(mod: types.ModuleType, c: Case) -> None:
    """Stdio: one JSON-RPC message or batch per line; a parse error is answered with -32700 and id null (JSON-RPC 2.0); notifications and
    responses get no line; no header rung (PY inbound.py 448)."""
    lines = [
        "not json",
        json.dumps(req("ping", mid=1)),
        "",
        json.dumps([req("ping", mid=2), note("notifications/initialized")]),
        json.dumps(note("notifications/initialized")),
        json.dumps([note("notifications/initialized")]),
        json.dumps({"jsonrpc": "2.0", "id": 9, "result": {}}),
        json.dumps(call(meta=META, mid=3)),
        "[]",
    ]

    def body() -> None:
        out = FlushCounter()
        mod.serve_stdio(stand_in, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        got = [json.loads(x) for x in out.getvalue().splitlines()]
        c.eq(
            "stdio replies",
            got,
            [
                err(None, -32700, "Parse error"),
                ok(1, {}),
                [ok(2, {})],
                ok(3, modern(CALL_OK)),
                err(None, -32600, "Bad Request: empty JSON-RPC batch"),
            ],
        )
        c.eq("each reply is one line, flushed at once", (out.getvalue().count("\n"), out.flushes), (5, 5))
        c.eq("a line holds no inner newline", all("\n" not in x for x in out.getvalue().splitlines()), True)

    c.guard("stdio", body)


class FlushCounter(io.StringIO):
    """A stdout that counts flushes: over a pipe, a reply that is not flushed never reaches the client."""

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


# ----- HTTP (loopback) ------------------------------------------------------------------------------------------------


@contextmanager
def server(mod: types.ModuleType, **kwargs: Any) -> Iterator[int]:
    srv = mod.serve_http("127.0.0.1", 0, stand_in, **kwargs)
    worker = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    try:
        yield int(srv.server_address[1])
    finally:
        srv.shutdown()
        srv.server_close()


def exchange(
    port: int, method: str, path: str, body: bytes | None = None, headers: list[tuple[str, str]] | None = None
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path)
        sent = headers if headers is not None else [("Content-Type", "application/json")]
        for name, value in sent:
            conn.putheader(name, value)
        conn.putheader("Content-Length", str(len(body or b"")))
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def post(port: int, payload: Any, headers: list[tuple[str, str]] | None = None, path: str = "/") -> tuple[int, dict[str, str], Any]:
    status, got_headers, data = exchange(port, "POST", path, json.dumps(payload).encode(), headers)
    return status, got_headers, json.loads(data) if data else None


def raw_post(port: int, content_length: str) -> tuple[int, str]:
    """A POST to / whose Content-Length is not a length (http.client always sends a valid one)."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(f"POST / HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: {content_length}\r\n\r\n".encode())
        received = b""
        while chunk := sock.recv(65536):
            received += chunk
    head, _, body = received.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body)["code"]


def raw(port: int, data: bytes, *, half_close: bool = False, wait: float = 5.0) -> bytes:
    """Send bytes as they are (optionally closing our side after them) and read until the server closes, or until ``wait`` seconds pass
    with nothing more."""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as sock:
        sock.sendall(data)
        if half_close:
            sock.shutdown(socket.SHUT_WR)
        return _until_closed(sock)


def _until_closed(sock: socket.socket) -> bytes:
    received = b""
    try:
        while chunk := sock.recv(65536):
            received += chunk
    except TimeoutError:
        pass
    return received


def responses(data: bytes) -> list[tuple[int, Any]]:
    """Every HTTP response in what a server sent on one connection, one after another, each body read by its Content-Length and decoded as
    JSON (None when empty); what cannot be read as a response is kept as (-1, the bytes)."""
    found: list[tuple[int, Any]] = []
    while data:
        head, sep, rest = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        parts = lines[0].split(b" ")
        if not sep or len(parts) < 2 or not parts[1].isdigit():
            found.append((-1, data))
            break
        length = next((int(line.split(b":", 1)[1]) for line in lines[1:] if line.lower().startswith(b"content-length:")), 0)
        body, data = rest[:length], rest[length:]
        found.append((int(parts[1]), json.loads(body) if body else None))
    return found


def said(reply: Any) -> tuple[str, str] | None:
    """The code and message of a REST error body."""
    return (reply["code"], reply["message"]) if isinstance(reply, dict) and "code" in reply and "message" in reply else None


JSON_TYPE = ("Content-Type", "application/json")
MODERN_HEADERS = [JSON_TYPE, ("MCP-Protocol-Version", MODERN), ("MCP-METHOD", "tools/call"), ("mcp-name", TOOL)]


def part_http(mod: types.ModuleType, c: Case) -> None:
    """The HTTP endpoint: 405 with Allow: POST off POST (SPEC 2025-11-25 transports 140-142, 2026-07-28 streamable-http 680-684; TS
    createMcpHandler.ts 284-285, 338-339; PY _streamable_http_modern.py 389-393); 415 for a body that is not JSON (TS createMcpHandler.ts
    949-958); -32700 at 400 (TS streamableHttp.ts 820-824); statuses of JSON-RPC errors (TS inboundClassification.ts 381-404); 202 with no body
    for notifications (SPEC streamable-http 82-84); header names case-insensitive and repeated headers combined (SPEC 570-576; TS
    createMcpHandler.ts 457-463); REST routes, Origin and rate limits unchanged."""

    def body() -> None:
        with server(mod, rate_limit_per_minute=1000) as port:
            for verb in ("GET", "DELETE", "PUT", "PATCH"):
                status, headers, data = exchange(port, verb, "/", None, [])
                c.eq(f"{verb} / status", status, 405)
                c.eq(f"{verb} / Allow", headers.get("allow"), "POST")
                c.eq(f"{verb} / code", json.loads(data)["code"], "METHOD_NOT_ALLOWED")
                c.eq(f"{verb} / message", json.loads(data)["message"], "the MCP endpoint accepts POST only")
            for verb in ("DELETE", "PUT", "PATCH"):
                status, _, data = exchange(port, verb, "/tools", None, [])
                c.eq(f"{verb} /tools", (status, json.loads(data)["code"]), (404, "NOT_FOUND"))
            status, _, data = exchange(port, "GET", "/health", None, [])
            # the members of RYO's own /health (status, protocol_version, server,
            # tools), with this server's values, and the revisions it serves
            c.eq(
                "GET /health",
                (status, json.loads(data)),
                (
                    200,
                    {
                        "status": "ok",
                        "protocol_version": "2024-11-05",
                        "supported_versions": [MODERN, *LEGACY],
                        "server": "indicator-condition-check",
                        "tools": 1,
                    },
                ),
            )
            status, _, data = exchange(port, "GET", "/tools", None, [])
            c.eq("GET /tools", (status, json.loads(data)), (200, {"tools": [TOOL_DEFINITION]}))
            status, _, data = exchange(port, "GET", "/nope", None, [])
            c.eq("GET /nope", (status, json.loads(data)["code"]), (404, "NOT_FOUND"))
            ping = json.dumps(req("ping")).encode()
            status, _, data = exchange(port, "POST", "/", ping, [("Content-Type", "text/plain")])
            c.eq(
                "text/plain",
                (status, json.loads(data)["code"], json.loads(data)["message"]),
                (415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"),
            )
            status, _, data = exchange(port, "POST", "/", ping, [])
            c.eq("no Content-Type", (status, json.loads(data)["code"]), (415, "UNSUPPORTED_MEDIA_TYPE"))
            status, _, data = exchange(port, "POST", "/", ping, [("Content-Type", "Application/JSON; charset=utf-8")])
            c.eq("Content-Type with a parameter", (status, json.loads(data)), (200, ok(1, {})))
            status, _, data = exchange(port, "POST", "/", b"{not json")
            c.eq("parse error", (status, json.loads(data)), (400, err(None, -32700, "Parse error")))
            status, _, data = exchange(port, "POST", "/", b"")
            c.eq("empty body", (status, json.loads(data)), (400, err(None, -32700, "Parse error")))
            status, _, data = exchange(port, "POST", "/", b"[" * 60000)
            c.eq("nesting too deep", (status, json.loads(data)), (400, err(None, -32700, "Parse error")))
            c.eq("a JSON string", post(port, "x")[0::2], (400, INVALID))
            status, _, reply = post(port, call(meta=META), MODERN_HEADERS)
            c.eq("stateless tools/call", (status, reply), (200, ok(1, modern(CALL_OK))))
            twice = [*MODERN_HEADERS, ("Mcp-Name", TOOL)]
            joined = f"{TOOL}, {TOOL}"
            c.eq(
                "repeated Mcp-Name",
                post(port, call(meta=META), twice)[0::2],
                (400, mismatch(1, joined, f'the body carries params.name="{TOOL}" but the Mcp-Name header names "{joined}"')),
            )
            future = {VERSION_KEY: "2031-01-01", CAPS_KEY: {}}
            c.eq("unserved version", post(port, call(meta=future), [JSON_TYPE])[0::2], (400, unsupported(1, "2031-01-01")))
            c.eq(
                "stateless unknown method",
                post(port, req("no/such", {"_meta": META}), [JSON_TYPE, ("MCP-Protocol-Version", MODERN), ("Mcp-Method", "no/such")])[0::2],
                (404, err(1, -32601, "Method not found: no/such")),
            )
            c.eq("legacy unknown method", post(port, req("no/such"))[0::2], (200, err(1, -32601, "Method not found: no/such")))
            c.eq("legacy unknown tool", post(port, call(name="nope"))[0::2], (200, err(1, -32602, "Unknown tool: 'nope'")))
            c.eq(
                "unserved legacy header",
                post(port, req("ping"), [JSON_TYPE, ("MCP-Protocol-Version", "2025-01-01")])[0::2],
                (400, err(1, -32602, UNSUPPORTED_HEADER.format("2025-01-01"))),
            )
            status, headers, data = exchange(port, "POST", "/", json.dumps(note("notifications/initialized")).encode())
            c.eq("notification", (status, data, headers.get("content-length")), (202, b"", "0"))
            c.eq("batch", post(port, [req("ping", mid=1), req("ping", mid=2)])[0::2], (200, [ok(1, {}), ok(2, {})]))
            status, _, data = exchange(port, "POST", "/", json.dumps([note("notifications/initialized")]).encode())
            c.eq("notification batch", (status, data), (202, b""))
            c.eq("empty batch", post(port, [])[0::2], (400, err(None, -32600, "Bad Request: empty JSON-RPC batch")))
            status, _, data = exchange(port, "POST", "/", b"x" * (70 * 1024))
            c.eq("oversized", (status, json.loads(data)["code"]), (413, "INVALID_BODY"))
            status, _, data = exchange(port, "POST", "/", ping, [JSON_TYPE, ("Origin", "https://evil.example")])
            c.eq(
                "foreign origin",
                (status, said(json.loads(data))),
                (403, ("FORBIDDEN_ORIGIN", "requests from this origin are not allowed")),
            )
            status, _, data = exchange(port, "POST", "/", ping, [JSON_TYPE, ("Origin", f"http://127.0.0.1:{port}")])
            c.eq("local origin", (status, json.loads(data)), (200, ok(1, {})))
            c.eq("REST call", post(port, GOOD_ARGS, path=f"/tools/{TOOL}/call")[0::2], (200, {"result": STAND_IN}))
            skills = exchange(port, "GET", "/api/skills/", None, [])
            c.eq("the platform list", (skills[0], [d["name"] for d in json.loads(skills[2])]), (200, [TOOL]))
            c.eq("the list without the closing slash", exchange(port, "GET", "/api/skills", None, [])[0], 200)
            one = exchange(port, "GET", f"/api/skills/{TOOL}", None, [])
            c.eq(
                "one definition has the platform's members",
                (one[0], sorted(json.loads(one[2])), json.loads(one[2])["requires_guard"], json.loads(one[2])["xp"]),
                (200, ["args", "description", "name", "requires_guard", "xp"], False, 0),
            )
            c.eq("an unknown skill is 404", exchange(port, "GET", "/api/skills/other", None, [])[0], 404)
            invoke = post(port, {"name": TOOL, "args": GOOD_ARGS}, path=f"/api/skills/{TOOL}/invoke")
            c.eq(
                "an invocation succeeds",
                (invoke[0], invoke[2]["status"], invoke[2]["result"], invoke[2]["guard_decision"]),
                (200, "success", STAND_IN, None),
            )
            refused = post(port, {"name": TOOL, "args": {"symbol": "SOL"}}, path=f"/api/skills/{TOOL}/invoke")
            c.eq(
                "a refused call is an error status",
                (refused[0], refused[2]["status"], refused[2]["result"]["code"]),
                (200, "error", "INVALID_ARGUMENTS"),
            )
            c.eq("a body without args is 422", post(port, {"name": TOOL}, path=f"/api/skills/{TOOL}/invoke")[0], 422)
            c.eq("a path ending in /invoke outside /api/skills/ is no route", post(port, {}, path="/other/invoke")[0], 404)
            c.eq("a deeper path under /api/skills/ is no route", post(port, {}, path=f"/api/skills/{TOOL}/extra")[0], 404)
            c.eq("an unknown skill cannot be invoked", post(port, {"name": "other", "args": {}}, path="/api/skills/other/invoke")[0], 422)
            status, _, reply = post(port, {"symbol": "SOL"}, path=f"/tools/{TOOL}/call")
            c.eq("REST invalid arguments", (status, reply["code"], reply["details"]["field"]), (400, "INVALID_ARGUMENTS", "condition"))
            status, _, reply = post(port, {}, path="/tools/other/call")
            c.eq("REST unknown tool", (status, reply["code"]), (404, "UNKNOWN_TOOL"))
            status, _, data = exchange(port, "POST", f"/tools/{TOOL}/call", b"null")
            c.eq(
                "REST null body",
                (status, json.loads(data)["code"], json.loads(data)["details"]["field"]),
                (400, "INVALID_ARGUMENTS", "arguments"),
            )
            status, _, data = exchange(port, "POST", f"/tools/{TOOL}/call", b"[" * 60000)
            c.eq("REST nesting too deep", (status, json.loads(data)["code"]), (400, "INVALID_JSON"))
            status, _, data = exchange(port, "POST", f"/tools/{TOOL}/call", b"")
            c.eq("REST empty body", (status, json.loads(data)["details"]["field"]), (400, "symbol"))
            status, _, reply = post(port, {**GOOD_ARGS, "symbol": "BOOM"}, path=f"/tools/{TOOL}/call")
            c.eq("REST broken tool", (status, reply["code"], reply["message"]), (500, "INTERNAL_ERROR", "RuntimeError"))
            status, headers, data = exchange(port, "GET", "/health", None, [])
            c.eq("JSON answers say so", (headers.get("content-type"), headers.get("content-length")), ("application/json", str(len(data))))
            c.true("Server header names the skill", headers.get("server", "").startswith("indicator-condition-check "))
            status, _, data = exchange(port, "GET", "/", None, [])
            refusal = json.loads(data)
            c.eq("REST error body members", sorted(refusal), ["code", "details", "message", "trace_id"])
            c.eq("REST error body details", refusal["details"], {})
            c.true("REST error trace id", re.fullmatch(r"[0-9a-f]{32}", refusal["trace_id"]) is not None)
            for length in ("abc", "-5"):
                c.eq(f"Content-Length {length}", raw_post(port, length), (400, "INVALID_BODY"))
            status, _, data = exchange(port, "POST", "/nope", None, [])
            c.eq("POST to no route", (status, said(json.loads(data))), (404, ("NOT_FOUND", "no route /nope")))
            status, _, data = exchange(port, "POST", f"/tools/{TOOL}/call", b"{not json")
            c.eq("REST body that is not JSON", (status, said(json.loads(data))), (400, ("INVALID_JSON", "body is not valid JSON")))
            c.eq(
                "Mcp-Name with one more character at its end, over HTTP",
                post(port, call(meta=META), [*MODERN_HEADERS[:3], ("Mcp-Name", f"{TOOL}~")])[0::2],
                (400, mismatch(1, f"{TOOL}~", f'the body carries params.name="{TOOL}" but the Mcp-Name header names "{TOOL}~"')),
            )
            # one answer per refused request, and nothing written after it (read until the server closes)
            evil = b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nOrigin: https://evil.example\r\n\r\n"
            c.eq(
                "a foreign origin gets its 403 and nothing more",
                [(s, said(b)) for s, b in responses(raw(port, evil))],
                [(403, ("FORBIDDEN_ORIGIN", "requests from this origin are not allowed"))],
            )
            call_head = f"POST /tools/{TOOL}/call HTTP/1.0\r\nContent-Type: application/json\r\n".encode()
            c.eq(
                "a REST call whose length is not a number gets its 400 and nothing more",
                [(s, said(b)) for s, b in responses(raw(port, call_head + b"Content-Length: abc\r\n\r\n"))],
                [(400, ("INVALID_BODY", "body must be at most 65536 bytes"))],
            )
            c.eq(
                "a REST call whose body is not JSON gets its 400 and nothing more",
                [(s, said(b)) for s, b in responses(raw(port, call_head + b"Content-Length: 9\r\n\r\n{not json"))],
                [(400, ("INVALID_JSON", "body is not valid JSON"))],
            )
            mcp_head = b"POST / HTTP/1.0\r\nContent-Type: application/json\r\n"
            c.eq(
                "no Content-Length is an empty body, answered at once",
                responses(raw(port, mcp_head + b"\r\n", wait=3.0)),
                [(400, err(None, -32700, "Parse error"))],
            )
            c.eq(
                "a body cut short by the client is answered once its end is read",
                [
                    (s, said(b))
                    for s, b in responses(raw(port, mcp_head + b"Content-Length: 70000\r\n\r\n" + b"x" * 1000, half_close=True, wait=3.0))
                ],
                [(413, ("INVALID_BODY", "body must be at most 65536 bytes"))],
            )
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall(mcp_head + b"Content-Length: 65537\r\n\r\n" + b"x" * 65536)
                sock.settimeout(1.0)
                early = _until_closed(sock)
                sock.settimeout(5)
                sock.sendall(b"x")
                later = _until_closed(sock)
            c.eq("an oversized body is read to its declared end before it is answered: nothing yet, one byte short", early, b"")
            c.eq(
                "and then the 413",
                [(s, said(b)) for s, b in responses(later)],
                [(413, ("INVALID_BODY", "body must be at most 65536 bytes"))],
            )
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n")
                kept, closed = b"", False
                try:
                    while chunk := sock.recv(65536):
                        kept += chunk
                    closed = True
                except TimeoutError:
                    pass
            c.eq(
                "one request per connection: a client asking to keep it gets one HTTP/1.0 answer, then the server closes",
                (kept.startswith(b"HTTP/1.0 200 "), len(responses(kept)), closed),
                (True, 1, True),
            )

    c.guard("http", body)


def part_rate(mod: types.ModuleType, c: Case) -> None:
    """Rate limits: SPEC 2026-07-28 server/tools 789-792 (servers MUST rate limit tool invocations); a batch is charged one unit per request in
    it, so a batch cannot step around the per-minute limit; the 429 body and Retry-After are unchanged."""

    def body() -> None:
        with server(mod, rate_limit_per_minute=3) as port:
            three = [req("ping", mid=i) for i in range(3)]
            c.eq("a batch of three within three", post(port, three)[0::2], (200, [ok(i, {}) for i in range(3)]))
            status, headers, reply = post(port, req("ping"))
            c.eq("the fourth request", (status, reply["code"], headers.get("retry-after")), (429, "RATE_LIMITED", "10"))
            c.eq("the fourth request's message", reply["message"], "too many requests; slow down")
        with server(mod, rate_limit_per_minute=3) as port:
            status, _, reply = post(port, [req("ping", mid=i) for i in range(4)])
            c.eq("a batch of four over three", (status, reply["code"]), (429, "RATE_LIMITED"))
            c.eq("after a refused batch, single requests still pass", post(port, req("ping"))[0::2], (200, ok(1, {})))
        with server(mod, rate_limit_per_minute=2) as port:
            notes = [note("notifications/initialized") for _ in range(5)]
            status, _, data = exchange(port, "POST", "/", json.dumps(notes).encode())
            c.eq("notifications are charged one unit per POST", status, 202)
            c.eq("one unit left", post(port, req("ping"))[0::2], (200, ok(1, {})))
            c.eq("then refused", post(port, req("ping"))[0], 429)
            status, _, data = exchange(port, "GET", "/health", None, [])
            c.eq("reads are not limited", status, 200)
        with server(mod, rate_limit_per_minute=2) as port:
            path = f"/tools/{TOOL}/call"
            c.eq("REST calls are limited: first", post(port, GOOD_ARGS, path=path)[0], 200)
            c.eq("REST calls are limited: second", post(port, GOOD_ARGS, path=path)[0], 200)
            status, headers, reply = post(port, GOOD_ARGS, path=path)
            c.eq("REST calls are limited: third", (status, reply["code"], headers.get("retry-after")), (429, "RATE_LIMITED", "10"))
            c.eq("REST calls are limited: the message", reply["message"], "too many requests; slow down")
            c.eq("REST and MCP share the client's window", post(port, req("ping"))[0], 429)

    c.guard("rate", body)


def part_limiter(mod: types.ModuleType, c: Case) -> None:
    """The per-client sliding window behind the 429s, on an injected clock: sixty seconds, one window per client address, and a refused
    charge records nothing."""
    now = [1000.0]
    limiter = mod._RateLimiter(2, lambda: now[0])
    c.eq("first", limiter.allow("a"), True)
    c.eq("second", limiter.allow("a"), True)
    c.eq("third within the minute", limiter.allow("a"), False)
    c.eq("another client has its own window", limiter.allow("b"), True)
    now[0] += 60
    c.eq("still inside the window at exactly sixty seconds", limiter.allow("a"), False)
    now[0] += 0.5
    c.eq("after sixty seconds the window has moved", limiter.allow("a"), True)
    c.eq("one unit left after the move", limiter.allow("a"), True)
    c.eq("and then none", limiter.allow("a"), False)
    fresh = mod._RateLimiter(3, lambda: now[0])
    c.eq("a charge larger than the limit is refused", fresh.allow("c", 4), False)
    c.eq("a refused charge records nothing", fresh.allow("c", 3), True)
    c.eq("the window is full", fresh.allow("c"), False)
    c.eq("default clock", inspect.signature(mod._RateLimiter).parameters["clock"].default, time.monotonic)


def part_busy(mod: types.ModuleType, c: Case) -> None:
    """Concurrency bound: with every slot held by a call in progress, the next POST is 503 BUSY with Retry-After 2, and slots are given back."""

    def body() -> None:
        inside = threading.Semaphore(0)
        release = threading.Event()

        def holding(args: dict[str, Any]) -> dict[str, Any]:
            inside.release()
            release.wait(5)
            return dict(STAND_IN)

        srv = mod.serve_http("127.0.0.1", 0, holding, rate_limit_per_minute=1000)
        worker = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True)
        worker.start()
        port = int(srv.server_address[1])
        results: list[int] = []
        callers = [threading.Thread(target=lambda: results.append(post(port, GOOD_ARGS, path=f"/tools/{TOOL}/call")[0])) for _ in range(8)]
        try:
            for t in callers:
                t.start()
            held = all(inside.acquire(timeout=5) for _ in range(8))
            c.eq("eight calls in progress", held, True)
            status, headers, reply = post(port, req("ping"))
            c.eq("the ninth POST", (status, reply["code"], headers.get("retry-after")), (503, "BUSY", "2"))
            c.eq("the ninth POST's message", reply["message"], "too many concurrent requests")
            status, _, data = exchange(port, "GET", "/health", None, [])
            c.eq("reads are not bounded", status, 200)
        finally:
            release.set()
            for t in callers:
                t.join(5)
            c.eq("the eight calls", sorted(results), [200] * 8)
            c.eq("slots are given back", post(port, req("ping"))[0::2], (200, ok(1, {})))
            srv.shutdown()
            srv.server_close()

    c.guard("busy", body)


PARTS: dict[str, Callable[[types.ModuleType, Case], None]] = {
    "constants": part_constants,
    "shape": part_shape,
    "legacy": part_legacy,
    "stateless": part_stateless,
    "headers": part_headers,
    "batch": part_batch,
    "codec": part_codec,
    "stdio": part_stdio,
    "http": part_http,
    "rate": part_rate,
    "limiter": part_limiter,
    "busy": part_busy,
    "origin": part_origin,
}
ALL_PARTS = tuple(PARTS)


def failures(mod: types.ModuleType, parts: tuple[str, ...] = ALL_PARTS) -> list[str]:
    c = Case(mod)
    for name in parts:
        c.guard(f"part {name}", partial(PARTS[name], mod, c))
    return c.bad
