"""Checks of ``address_flag_check.server``, runnable against a mutant: MCP JSON-RPC in both protocol eras (the 2024-11-05 to 2025-11-25
handshake and the stateless 2026-07-28 revision, with the official TypeScript SDK's HTTP behaviour read in its source: identity in
``_meta``, -32022 on HTTP 400 with ``supported`` and ``requested``, the header cross-checks with -32020, the 2025-11-25 counter-offer, 405 on
GET, batches refused), and the REST routes in RYO's shapes, all against a real server on 127.0.0.1. The tools answer from recorded data.
"""

from __future__ import annotations

import base64
import collections
import dataclasses
import datetime as dt
import inspect
import io
import json
import pathlib
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from typing import Any

from address_flag_check import list_store, ofac_index, skill
from address_flag_check.schema import OUTPUT_SCHEMA, TOOL_DEFINITIONS
from tests.checks_support import ETH_DEAD, Case, Recorded, extract_xml, server_date

DEAD = "0x000000000000000000000000000000000000dEaD"
ARGS = {"address": DEAD, "chain": "ethereum"}
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"}}
SUPPORTED = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
NOW = server_date("ethereum/eth_block.json")


def _runners(home: pathlib.Path) -> dict[str, Any]:
    index = ofac_index.parse_xml(extract_xml())
    stamp = (NOW - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    list_store.save(home, list_store.StoredList(index, index.xml_sha256, index.xml_bytes, "recorded", '"recorded"', stamp, stamp))
    return {
        "address_flag_check": lambda args: skill.run(
            args, now=NOW, transport=Recorded(ETH_DEAD), list_dir=home, sleep=lambda s: None, cooldowns={}
        ),
        "address_flag_sources": lambda args: skill.run_sources(args, now=NOW, list_dir=home),
    }


def _rpc(
    mod: types.ModuleType, runners: dict[str, Any], method: str, params: Any = None, mid: Any = 1, headers: dict[str, str] | None = None
) -> Any:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return mod.handle_rpc(msg, runners, headers)


def _headers(method: str, name: str | None = None, version: str = "2026-07-28") -> dict[str, str]:
    h = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        h["Mcp-Name"] = name
    return h


def check_legacy_era(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    c = Case(mod)
    for v in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
        out = _rpc(mod, runners, "initialize", {"protocolVersion": v})
        c.eq(f"initialize {v}", (out.http_status, out.reply["result"]["protocolVersion"]), (200, v))
    out = _rpc(mod, runners, "initialize", {"protocolVersion": "1999-01-01"})
    c.eq("an unknown version gets the latest handshake revision", out.reply["result"]["protocolVersion"], "2025-11-25")
    out = _rpc(mod, runners, "initialize", {"protocolVersion": "2026-07-28"})
    c.eq("the stateless revision is never offered in a handshake", out.reply["result"]["protocolVersion"], "2025-11-25")
    r = _rpc(mod, runners, "initialize", {}).reply["result"]
    c.eq(
        "initialize result",
        r,
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "address-flag-check", "version": "0.1.0"},
        },
    )
    c.eq("ping", _rpc(mod, runners, "ping").reply, {"jsonrpc": "2.0", "id": 1, "result": {}})
    tools = _rpc(mod, runners, "tools/list").reply["result"]
    c.eq("tools/list in the handshake era", tools, {"tools": TOOL_DEFINITIONS})
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_check", "arguments": ARGS}).reply["result"]
    c.eq("a tool result has no resultType in the handshake era", "resultType" in res, False)
    c.eq("success", res["isError"], False)
    envelope = json.loads(res["content"][0]["text"])
    c.eq("text and structured content agree", envelope, res["structuredContent"])
    c.eq("the answer", (envelope["tool"], envelope["data"]["verdict"]), ("address_flag_check", "no_flag_found"))
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_sources", "arguments": {}}).reply["result"]
    c.eq("the sources tool", json.loads(res["content"][0]["text"])["tool"], "address_flag_sources")
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_check", "arguments": {"address": "0x12", "chain": "ethereum"}}).reply[
        "result"
    ]
    body = json.loads(res["content"][0]["text"])
    c.eq(
        "invalid arguments are a tool error with the exact message",
        (res["isError"], body["code"], body["message"], body["details"]["field"], body["details"]["example"]),
        (True, "INVALID_ARGUMENTS", "address: is not an EVM address (0x followed by 40 hex characters)", "address", ARGS),
    )
    c.true("a tool error has a trace id", bool(body["trace_id"]))
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_check"}).reply["result"]
    c.eq("missing arguments are invalid arguments", json.loads(res["content"][0]["text"])["message"], "address: is required")

    def boom(args: object) -> dict[str, Any]:
        raise RuntimeError("secret internal detail")

    res = _rpc(mod, {**runners, "address_flag_check": boom}, "tools/call", {"name": "address_flag_check", "arguments": ARGS}).reply[
        "result"
    ]
    text = res["content"][0]["text"]
    c.eq(
        "an internal error is a tool error with the class name only",
        (res["isError"], json.loads(text)["code"], json.loads(text)["message"], "secret" in text),
        (True, "INTERNAL_ERROR", "RuntimeError", False),
    )
    out = _rpc(mod, runners, "tools/call", {"name": "nope", "arguments": {}})
    c.eq(
        "an unknown tool",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
        (200, -32602, "Unknown tool: 'nope'"),
    )
    out = _rpc(mod, runners, "no/such")
    c.eq(
        "an unknown method in the handshake era",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
        (200, -32601, "Method not found: no/such"),
    )
    c.eq(
        "a notification has no reply", mod.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, runners, None).reply, None
    )
    c.eq("an unknown notification has no reply", mod.handle_rpc({"jsonrpc": "2.0", "method": "x/y"}, runners, None).reply, None)
    for label, msg in (
        ("text", "garbage"),
        ("another version", {"jsonrpc": "1.0", "id": 1, "method": "ping"}),
        ("no method", {"jsonrpc": "2.0", "id": 1}),
    ):
        out = mod.handle_rpc(msg, runners, None)
        c.eq(
            f"an invalid request ({label})",
            (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
            (400, -32600, "Invalid Request"),
        )
    out = mod.handle_rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], runners, None)
    c.eq(
        "a batch",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"], out.reply["id"]),
        (400, -32600, "Bad Request: JSON-RPC batches are not supported by this endpoint", None),
    )
    out = _rpc(mod, runners, "ping", headers={"MCP-Protocol-Version": "2025-06-18"})
    c.eq("a supported handshake-era header", out.http_status, 200)
    out = _rpc(mod, runners, "ping", headers={"MCP-Protocol-Version": "2019-01-01"})
    c.eq(
        "an unsupported handshake-era header",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
        (400, -32000, f"Bad Request: Unsupported protocol version: 2019-01-01 (supported versions: {', '.join(SUPPORTED)})"),
    )
    out = _rpc(mod, runners, "initialize", {"protocolVersion": "2025-11-25"}, headers={"MCP-Protocol-Version": "2026-07-28"})
    c.eq(
        "a handshake with a stateless header",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["data"]),
        (
            400,
            -32020,
            {
                "mismatch": {
                    "header": "2026-07-28",
                    "body": "an initialize request (legacy handshake) was sent with a modern MCP-Protocol-Version header",
                }
            },
        ),
    )
    out = _rpc(mod, runners, "tools/list", {}, headers={"MCP-Protocol-Version": "2026-07-28"})
    c.eq(
        "a stateless header without the envelope",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
        (
            400,
            -32602,
            "Invalid params: the MCP-Protocol-Version header names protocol revision 2026-07-28, but the request is missing the required per-request envelope key(s): _meta",
        ),
    )
    out = _rpc(mod, runners, "tools/list", {"_meta": {}}, headers={"MCP-Protocol-Version": "2026-07-28"})
    c.eq(
        "an envelope without its version key",
        out.reply["error"]["message"],
        "Invalid params: the MCP-Protocol-Version header names protocol revision 2026-07-28, but the request is missing the required per-request envelope key(s): io.modelcontextprotocol/protocolVersion",
    )
    return c.bad


def check_stateless_era(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    c = Case(mod)
    out = _rpc(mod, runners, "server/discover")
    r = out.reply["result"]
    c.eq(
        "server/discover",
        r,
        {
            "resultType": "complete",
            "supportedVersions": SUPPORTED,
            "capabilities": {"tools": {"listChanged": False}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "address-flag-check", "version": "0.1.0"}},
            "ttlMs": 300000,
            "cacheScope": "public",
        },
    )
    c.eq("no server identity outside _meta", "serverInfo" in r, False)
    out = _rpc(mod, runners, "tools/list", {"_meta": META}, headers=_headers("tools/list"))
    c.eq(
        "stateless tools/list",
        (out.http_status, out.reply["result"]),
        (200, {"resultType": "complete", "tools": TOOL_DEFINITIONS, "ttlMs": 300000, "cacheScope": "public"}),
    )
    out = _rpc(mod, runners, "tools/list", {"_meta": META})
    c.eq("stateless over stdio needs no headers", out.reply["result"]["resultType"], "complete")
    out = _rpc(
        mod,
        runners,
        "tools/call",
        {"name": "address_flag_check", "arguments": ARGS, "_meta": META},
        headers=_headers("tools/call", "address_flag_check"),
    )
    res = out.reply["result"]
    c.eq(
        "a stateless call without a handshake",
        (out.http_status, res["resultType"], res["isError"], res["structuredContent"]["data"]["verdict"]),
        (200, "complete", False, "no_flag_found"),
    )
    encoded = "=?base64?" + base64.b64encode(b"address_flag_check").decode() + "?="
    out = _rpc(
        mod,
        runners,
        "tools/call",
        {"name": "address_flag_check", "arguments": ARGS, "_meta": META},
        headers=_headers("tools/call", encoded),
    )
    c.eq("an Mcp-Name in the Base64 sentinel form", (out.http_status, out.reply.get("result", {}).get("isError")), (200, False))
    out = _rpc(mod, runners, "ping", {"_meta": META}, headers=_headers("ping"))
    c.eq("stateless ping", out.reply["result"], {"resultType": "complete"})
    out = _rpc(mod, runners, "initialize", {"protocolVersion": "2025-11-25", "_meta": META}, headers=_headers("initialize"))
    c.eq("there is no handshake in the stateless era", (out.http_status, out.reply["error"]["code"]), (404, -32601))
    out = _rpc(mod, runners, "nope/nope", {"_meta": META}, headers=_headers("nope/nope"))
    c.eq(
        "an unknown stateless method",
        (out.http_status, out.reply["error"]["code"], out.reply["error"]["message"]),
        (404, -32601, "Method not found: nope/nope"),
    )
    for label, version in (("a future version", "2031-01-01"), ("a handshake version in the envelope", "2025-11-25")):
        bad = {"io.modelcontextprotocol/protocolVersion": version}
        out = _rpc(mod, runners, "tools/list", {"_meta": bad}, mid=3, headers=_headers("tools/list", version=version))
        c.eq(
            f"{label}: -32022 on HTTP 400",
            (out.http_status, out.reply["id"], out.reply["error"]),
            (
                400,
                3,
                {
                    "code": -32022,
                    "message": f"Unsupported protocol version: {version}",
                    "data": {"supported": SUPPORTED, "requested": version},
                },
            ),
        )
    out = _rpc(mod, runners, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": 7}})
    c.eq("a version that is not text", out.reply["error"]["data"], {"supported": SUPPORTED, "requested": "unknown"})

    def mismatch(header: str, body: str) -> dict[str, Any]:
        return {
            "code": -32020,
            "message": f"Bad Request: the request headers and body disagree: {body}",
            "data": {"mismatch": {"header": header, "body": body}},
        }

    call = {"name": "address_flag_check", "arguments": ARGS, "_meta": META}
    for label, headers, want in (
        (
            "no version header",
            {"Mcp-Method": "tools/call", "Mcp-Name": "address_flag_check"},
            mismatch(
                "(missing)", "the body envelope names protocol version 2026-07-28 but the required MCP-Protocol-Version header is absent"
            ),
        ),
        (
            "another version header",
            _headers("tools/call", "address_flag_check", version="2025-11-25"),
            mismatch(
                "2025-11-25", "the body envelope names protocol version 2026-07-28 but the MCP-Protocol-Version header names 2025-11-25"
            ),
        ),
        (
            "no method header",
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Name": "address_flag_check"},
            mismatch("(missing)", "the body names method tools/call but the required Mcp-Method header is absent"),
        ),
        (
            "another method header",
            _headers("tools/list", "address_flag_check"),
            mismatch("tools/list", "the body names method tools/call but the Mcp-Method header names tools/list"),
        ),
        (
            "no name header",
            _headers("tools/call"),
            mismatch("(missing)", 'the body carries params.name="address_flag_check" but the required Mcp-Name header is absent'),
        ),
        (
            "another name header",
            _headers("tools/call", "address_flag_sources"),
            mismatch(
                "address_flag_sources",
                'the body carries params.name="address_flag_check" but the Mcp-Name header names "address_flag_sources"',
            ),
        ),
        (
            "a broken sentinel",
            _headers("tools/call", "=?base64?###?="),
            mismatch("=?base64?###?=", "the Mcp-Name header carries an invalid Base64 sentinel value"),
        ),
        (
            "a sentinel without padding",
            _headers("tools/call", "=?base64?YWJj?=".replace("YWJj", "YWI")),
            mismatch("=?base64?YWI?=", "the Mcp-Name header carries an invalid Base64 sentinel value"),
        ),
    ):
        out = _rpc(mod, runners, "tools/call", call, mid=9, headers=headers)
        c.eq(f"{label}: -32020 on HTTP 400", (out.http_status, out.reply.get("id"), out.reply.get("error")), (400, 9, want))
    out = _rpc(
        mod,
        runners,
        "tools/call",
        call,
        headers={"mcp-protocol-version": " 2026-07-28 ", "mcp-method": "tools/call", "mcp-name": "address_flag_check"},
    )
    c.eq("header names in any case, values with spaces around them", out.http_status, 200)
    c.eq(
        "a stateless notification needs no headers",
        mod.handle_rpc({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"_meta": META}}, runners, {}).reply,
        None,
    )
    c.eq(
        "the header value decoder",
        (
            mod.decode_header_value("plain"),
            mod.decode_header_value("=?base64?" + base64.b64encode("é".encode()).decode() + "?="),
            mod.decode_header_value("=?base64?/w==?="),
        ),
        ("plain", "é", None),
    )
    return c.bad


def check_stdio(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    c = Case(mod)
    stdin = io.StringIO(
        'not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n[{"jsonrpc":"2.0","id":2,"method":"ping"}]\n'
    )
    out = io.StringIO()
    mod.serve_stdio(runners, stdin=stdin, stdout=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    c.eq(
        "stdio replies",
        [(x.get("id"), x.get("error", {}).get("code"), "result" in x) for x in lines],
        [(None, -32700, False), (1, None, True), (None, -32600, False)],
    )
    return c.bad


def _http(
    base: str, path: str, body: Any = None, raw: bytes | None = None, method: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], Any]:
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})} if data is not None else dict(headers or {}),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read()
            return r.status, dict(r.headers), json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read()
        return e.code, dict(e.headers), json.loads(text) if text else None


def check_http(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    c = Case(mod)
    import jsonschema

    srv = mod.serve_http("127.0.0.1", 0, runners)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        c.eq("bound to this machine only", srv.server_address[0], "127.0.0.1")
        status, _, body = _http(base, "/health")
        c.eq(
            "health",
            (status, body),
            (
                200,
                {
                    "status": "ok",
                    "protocol_version": "2024-11-05",
                    "supported_versions": SUPPORTED,
                    "server": "address-flag-check",
                    "tools": 2,
                },
            ),
        )
        status, _, body = _http(base, "/tools")
        c.eq("the catalogue is a list, as RYO's", (status, body), (200, TOOL_DEFINITIONS))
        # the platform's skill shape: list, one definition, invoke
        status, _, body = _http(base, "/api/skills/")
        c.eq(
            "the platform list names both tools", (status, [d["name"] for d in body]), (200, ["address_flag_check", "address_flag_sources"])
        )
        c.eq("the list without the closing slash", _http(base, "/api/skills")[0], 200)
        status, _, body = _http(base, "/api/skills/address_flag_check")
        c.eq(
            "one definition has the platform's members",
            (status, sorted(body), body["name"], body["requires_guard"], body["xp"]),
            (200, ["args", "description", "name", "requires_guard", "xp"], "address_flag_check", False, 0),
        )
        status, _, body = _http(base, "/api/skills/nope")
        c.eq("an unknown skill is 404, named", (status, body["code"], body["message"]), (404, "NOT_FOUND", "no skill nope"))
        status, _, body = _http(base, "/api/skills/address_flag_check/extra")
        c.eq("a deeper path is no route", (status, body["message"]), (404, "no route /api/skills/address_flag_check/extra"))
        status, _, body = _http(base, "/x/y/address_flag_check")
        c.eq("a path outside /api/skills/ is no route", (status, body["message"]), (404, "no route /x/y/address_flag_check"))
        status, _, body = _http(base, "/api/skills/address_flag_check/invoke", {"name": "address_flag_check", "args": ARGS})
        c.eq(
            "an invocation succeeds",
            (status, body["status"], body["name"], body["guard_decision"]),
            (200, "success", "address_flag_check", None),
        )
        c.eq("and carries the envelope", body["result"]["tool"], "address_flag_check")
        status, _, body = _http(base, "/api/skills/address_flag_check/invoke", {"name": "address_flag_check", "args": {}})
        c.eq(
            "a refused call is an error status with the typed error",
            (status, body["status"], body["result"]["code"]),
            (200, "error", "INVALID_ARGUMENTS"),
        )
        c.eq("a body without args is 422", _http(base, "/api/skills/address_flag_check/invoke", {"name": "address_flag_check"})[0], 422)
        c.eq(
            "a name that differs from the path is 422",
            _http(base, "/api/skills/address_flag_check/invoke", {"name": "x", "args": {}})[0],
            422,
        )
        c.eq("an unknown skill cannot be invoked", _http(base, "/api/skills/nope/invoke", {"name": "nope", "args": {}})[0], 422)
        status, _, body = _http(base, "/tools/address_flag_check/call", ARGS)
        c.eq("a REST call in RYO's wrapper", (status, sorted(body)), (200, ["latency_ms", "result", "tool"]))
        c.eq("the wrapper names the tool", body["tool"], "address_flag_check")
        try:
            jsonschema.validate(body["result"], OUTPUT_SCHEMA)
        except jsonschema.ValidationError as exc:
            c.bad.append(f"REST result does not match the output schema: {exc.message}")
        status, _, body = _http(base, "/tools/address_flag_sources/call", {"chain": "ethereum"})
        c.eq("the sources tool over REST", (status, body["result"]["tool"]), (200, "address_flag_sources"))
        status, _, body = _http(base, "/tools/address_flag_check/call", {"address": DEAD, "chain": "solana"})
        c.eq(
            "invalid arguments over REST",
            (status, body["code"], body["message"]),
            (
                400,
                "INVALID_ARGUMENTS",
                "chain: must be one of ['ethereum', 'arbitrum', 'base', 'bsc', 'optimism', 'polygon', 'tron', 'bitcoin']",
            ),
        )
        status, _, body = _http(base, "/tools/does_not_exist/call", {})
        c.eq(
            "an unknown tool, in RYO's words",
            (status, body["code"], body["message"], body["details"]),
            (404, "NOT_FOUND", "Unknown tool 'does_not_exist'.", None),
        )
        status, _, body = _http(base, "/nope")
        c.eq("an unknown route", (status, body["code"]), (404, "NOT_FOUND"))
        status, _, body = _http(base, "/tools/address_flag_check/call", raw=b"{not json")
        c.eq("a body that is not JSON", (status, body["code"]), (400, "INVALID_JSON"))
        status, _, body = _http(base, "/tools/address_flag_check/call", raw=b"x" * (70 * 1024))
        c.eq("an oversized body", (status, body["code"]), (413, "INVALID_BODY"))
        status, _, body = _http(base, "/", {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        c.eq("MCP over HTTP", (status, body["id"], len(body["result"]["tools"])), (200, 5, 2))
        status, _, body = _http(base, "/", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        c.eq("a notification over HTTP", (status, body), (202, None))
        status, headers, body = _http(base, "/", method="GET")
        c.eq(
            "GET on the MCP endpoint",
            (status, headers.get("Allow"), body),
            (405, "POST", {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Method not allowed."}, "id": None}),
        )
        status, headers, _ = _http(base, "/", method="DELETE")
        c.eq("DELETE on the MCP endpoint", (status, headers.get("Allow")), (405, "POST"))
        status, _, body = _http(base, "/", [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        c.eq("a batch over HTTP", (status, body["error"]["code"]), (400, -32600))
        bad = {"io.modelcontextprotocol/protocolVersion": "2031-01-01"}
        status, _, body = _http(
            base,
            "/",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": bad}},
            headers=_headers("tools/list", version="2031-01-01"),
        )
        c.eq(
            "an unsupported version over HTTP is a 400",
            (status, body["error"]["code"], body["error"]["data"]["requested"]),
            (400, -32022, "2031-01-01"),
        )
        msg = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "address_flag_check", "arguments": ARGS, "_meta": META},
        }
        status, _, body = _http(base, "/", msg)
        c.eq("a stateless call over HTTP without its headers", (status, body["error"]["code"]), (400, -32020))
        status, _, body = _http(base, "/", msg, headers=_headers("tools/call", "address_flag_check"))
        c.eq(
            "a stateless call over HTTP with its headers",
            (status, body["result"]["resultType"], body["result"]["isError"]),
            (200, "complete", False),
        )
        status, _, body = _http(base, "/health", headers={"Origin": "https://evil.example"})
        c.eq("a foreign browser origin", (status, body["code"]), (403, "FORBIDDEN_ORIGIN"))
        status, _, _ = _http(base, "/health", headers={"Origin": f"http://127.0.0.1:{srv.server_address[1]}"})
        c.eq("a local origin", status, 200)
    finally:
        srv.shutdown()
        srv.server_close()
    limited = mod.serve_http("127.0.0.1", 0, runners, rate_limit_per_minute=2)
    threading.Thread(target=limited.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{limited.server_address[1]}"
    try:
        codes = [_http(base, "/tools/address_flag_sources/call", {})[0] for _ in range(3)]
        c.eq("tool calls are rate limited per client", codes, [200, 200, 429])
        status, headers, body = _http(base, "/tools/address_flag_sources/call", {})
        c.eq("the rate-limit answer", (status, body["code"], headers.get("Retry-After")), (429, "RATE_LIMITED", "10"))
        c.eq("the rate-limit message", body["message"], "too many requests; slow down")
        c.eq("reads are not limited", _http(base, "/health")[0], 200)
        c.eq("the MCP endpoint is a call, and is limited", _http(base, "/", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 429)
        c.eq("a path under /tools/ that is not a call is not limited", _http(base, "/tools/address_flag_sources", {})[0], 404)
        c.eq("a path ending in /call outside /tools/ is not limited", _http(base, "/other/call", {})[0], 404)
    finally:
        limited.shutdown()
        limited.server_close()
    skilled = mod.serve_http("127.0.0.1", 0, runners, rate_limit_per_minute=2)
    threading.Thread(target=skilled.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{skilled.server_address[1]}"
    try:
        c.eq(
            "paths under /api/skills/ that are not an invocation are not limited",
            [_http(base, "/api/skills/address_flag_sources", {})[0] for _ in range(3)],
            [404, 404, 404],
        )
        c.eq(
            "a path ending in /invoke outside /api/skills/ is not limited",
            [_http(base, "/other/invoke", {})[0] for _ in range(3)],
            [404, 404, 404],
        )
        body = {"name": "address_flag_sources", "args": {}}
        codes = [_http(base, "/api/skills/address_flag_sources/invoke", body)[0] for _ in range(3)]
        c.eq("skill invocations are rate limited per client", codes, [200, 200, 429])
    finally:
        skilled.shutdown()
        skilled.server_close()
    return c.bad


def _respond(raw: bytes) -> tuple[str, dict[str, str], Any]:
    """The status line, the headers and the JSON body of one raw HTTP response."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return lines[0], headers, json.loads(body) if body else None


def _raw(port: int, request: bytes) -> bytes:
    """Send one raw request and read until the server closes the connection (it speaks HTTP/1.0, one request per connection)."""
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(request)
        chunks = []
        while data := s.recv(65536):
            chunks.append(data)
    return b"".join(chunks)


class _Deque(collections.deque):  # type: ignore[type-arg]
    """A window that refuses to be read more than 50 times, so a loop that never removes an old hit fails at once instead of hanging."""

    reads = 0

    def __getitem__(self, i: Any) -> Any:
        self.reads += 1
        if self.reads > 50:
            raise RuntimeError("the window loop does not move on")
        return super().__getitem__(i)


def check_units(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    """The pieces below the HTTP server: the rate-limit window, the origin rule, the header decoder and the replies' exact shapes."""
    c = Case(mod)
    out = mod.RpcOutcome(None, 202)
    c.raises("an outcome cannot be changed", lambda: setattr(out, "http_status", 200), dataclasses.FrozenInstanceError)
    now = [0.0]
    limiter = mod._RateLimiter(1, clock=lambda: now[0])
    limiter.hits["a"] = _Deque()
    c.eq("the first call in a window", limiter.allow("a"), True)
    now[0] = 60.0
    c.eq("a call exactly 60 s later is still in the window", limiter.allow("a"), False)
    now[0] = 60.5
    c.eq("a call 60.5 s later is in a new window", limiter.allow("a"), True)
    limiter = mod._RateLimiter(5, clock=lambda: 100.0)
    limiter.hits["b"] = _Deque([0.0, 1.0])
    c.eq(
        "hits older than the window are dropped", c.run("old hits", lambda: (limiter.allow("b"), list(limiter.hits["b"]))), (True, [100.0])
    )
    o = mod._origin_allowed
    for origin, host, want in (
        ("http://localhost:8766", "localhost:8766", True),
        ("http://127.0.0.1:8766", "127.0.0.1:8766", True),
        ("http://[::1]:8766", "[::1]:8766", True),
        ("http://localhost:8766", "evil.example", False),
        ("https://evil.example", "localhost:8766", False),
        ("http://[bad", "localhost:8766", False),
        (None, "anything", True),
    ):
        c.eq(f"origin {origin} with host {host}", c.run(f"origin {origin}", lambda origin=origin, host=host: o(origin, host)), want)
    c.eq("a plain value that only ends like the sentinel", mod.decode_header_value("plain?="), "plain?=")
    out = _rpc(mod, runners, "server/discover")
    c.eq(
        "the whole server/discover reply",
        (out.http_status, {k: v for k, v in out.reply.items() if k != "result"}),
        (200, {"jsonrpc": "2.0", "id": 1}),
    )
    c.eq(
        "a notification is answered 202",
        mod.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, runners, None).http_status,
        202,
    )
    out = _rpc(mod, runners, "tools/list", {"_meta": {}}, headers={"MCP-Protocol-Version": "2026-07-28"})
    c.eq(
        "the missing envelope key is named in data",
        out.reply["error"].get("data"),
        {"envelope": {"missing": ["io.modelcontextprotocol/protocolVersion"]}},
    )
    out = _rpc(mod, runners, "tools/list", {"name": "address_flag_check", "_meta": META}, headers=_headers("tools/list"))
    c.eq("only tools/call needs an Mcp-Name header", out.http_status, 200)
    out = _rpc(
        mod,
        runners,
        "tools/call",
        {"name": "address_flag_check", "arguments": ARGS, "_meta": META},
        headers=_headers("tools/call", "address_flag_check~"),
    )
    c.eq("an Mcp-Name with a character more is another name", (out.http_status, out.reply.get("error", {}).get("code")), (400, -32020))
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_check", "arguments": ARGS}).reply["result"]
    c.eq("a result's content is one text item", res["content"], [{"type": "text", "text": json.dumps(res["structuredContent"])}])
    res = _rpc(mod, runners, "tools/call", {"name": "address_flag_check", "arguments": {"chain": "ethereum"}}).reply["result"]
    c.eq("a refusal's content is one text item", [(sorted(x), x.get("type")) for x in res["content"]], [(["text", "type"], "text")])

    def boom(args: object) -> dict[str, Any]:
        raise RuntimeError("x")

    res = _rpc(mod, {**runners, "address_flag_check": boom}, "tools/call", {"name": "address_flag_check", "arguments": ARGS}).reply[
        "result"
    ]
    c.eq("an internal error's content is one text item", [(sorted(x), x.get("type")) for x in res["content"]], [(["text", "type"], "text")])

    class Sink(io.StringIO):
        flushed: list[int]

        def flush(self) -> None:
            self.flushed.append(len(self.getvalue().splitlines()))

    sink = Sink()
    sink.flushed = []
    mod.serve_stdio(runners, stdin=io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), stdout=sink)
    c.eq("each stdio reply is flushed as soon as it is written", sink.flushed, [1, 2])
    c.eq("the parse error's message", json.loads(sink.getvalue().splitlines()[0])["error"]["message"], "Parse error")
    c.eq(
        "the HTTP defaults: this machine only, port 8766, 60 calls a minute",
        (
            inspect.signature(mod.serve_http).parameters["host"].default,
            inspect.signature(mod.serve_http).parameters["port"].default,
            inspect.signature(mod.make_http_handler).parameters["rate_limit_per_minute"].default,
        ),
        ("127.0.0.1", 8766, 60),
    )
    return c.bad


class _Body:
    """A request body for the reader alone: it records every read of more than 0 bytes, and refuses to keep answering empty reads."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos, self.sizes, self.empty = data, 0, [], 0

    def read(self, n: int = -1) -> bytes:
        n = len(self.data) - self.pos if n < 0 else n
        if n > 0:
            self.sizes.append(n)
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        if n > 0 and not chunk:
            self.empty += 1
            if self.empty > 5:
                raise RuntimeError("the drain loop does not stop at the end of the body")
        return chunk


def check_body_reader(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    """The request body reader of the HTTP handler, on a handler made without a socket: the size limit at its exact edge, the bounded
    drain of an oversized body (exactly 1 MiB, 64 KiB at a time, stopping when the client stops), and every refusal's exact answer."""
    c = Case(mod)
    handler_class = mod.make_http_handler(runners)

    def read(headers: dict[str, str], data: bytes) -> tuple[Any, Any, _Body, Any]:
        h = handler_class.__new__(handler_class)
        h.headers, h.rfile, h.wfile = headers, _Body(data), io.BytesIO()
        h.request_version, h.requestline, h.command, h.path, h.close_connection = "HTTP/1.1", "POST / HTTP/1.1", "POST", "/", False
        got = c.run(f"read {headers}", h._read_json)
        written = h.wfile.getvalue()
        return got, (_respond(written) if written else None), h.rfile, h.close_connection

    got, sent, _, _ = read({}, b"")
    c.eq("no Content-Length is an empty object", (got, sent), ((True, {}), None))
    got, sent, _, _ = read({"Content-Length": "0"}, b"")
    c.eq("a Content-Length of 0 is an empty object", (got, sent), ((True, {}), None))
    exact = json.dumps(ARGS).encode()
    exact += b" " * (65536 - len(exact))
    got, sent, _, _ = read({"Content-Length": "65536"}, exact)
    c.eq("a body of exactly 64 KiB is read", (got, sent), ((True, ARGS), None))
    got, sent, body, closed = read({"Content-Length": "65537"}, b"x" * 65537)
    c.eq(
        "one byte over 64 KiB: drained in full and refused, and the connection is closed",
        (got, None if sent is None else (sent[0].split(" ", 2)[1], sent[2]["code"], sent[2]["message"]), body.pos, body.sizes, closed),
        ((False, None), ("413", "INVALID_BODY", "body must be at most 65536 bytes"), 65537, [65536, 1], True),
    )
    got, sent, body, _ = read({"Content-Length": "2000000"}, b"x" * 2_000_000)
    c.eq("a large body is drained up to 1 MiB, 64 KiB at a time", (got, body.pos, body.sizes), ((False, None), 1_048_576, [65536] * 16))
    got, sent, body, _ = read({"Content-Length": "100000"}, b"x" * 70_000)
    c.eq(
        "a client that stops sending ends the drain",
        (got, body.pos, None if sent is None else sent[2]["code"]),
        ((False, None), 70_000, "INVALID_BODY"),
    )
    got, sent, _, _ = read({"Content-Length": "abc"}, b"{}")
    c.eq(
        "a Content-Length that is not a number",
        (got, None if sent is None else (sent[0].split(" ", 2)[1], sent[2]["code"], sent[2]["message"])),
        ((False, None), ("400", "INVALID_BODY", "Content-Length is not a number")),
    )
    got, sent, _, _ = read({"Content-Length": "9"}, b"{not json")
    c.eq(
        "a body that is not JSON",
        (got, None if sent is None else (sent[0].split(" ", 2)[1], sent[2]["code"], sent[2]["message"])),
        ((False, None), ("400", "INVALID_JSON", "body is not valid JSON")),
    )
    return c.bad


def check_http_edges(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    """Over a real server on 127.0.0.1: the response headers, a refused origin answered once, the routes no other check reaches, an
    internal error over REST, the measured latency, and the eight-request limit (BUSY)."""
    c = Case(mod)

    def boom(args: object) -> dict[str, Any]:
        raise RuntimeError("secret internal detail")

    entered = threading.Semaphore(0)
    release = threading.Event()

    def wait(args: object) -> dict[str, Any]:
        entered.release()
        release.wait(10)
        return {"waited": True}

    srv = mod.serve_http("127.0.0.1", 0, {**runners, "boom": boom, "wait": wait})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threads: list[threading.Thread] = []
    try:
        line, headers, body = _respond(_raw(port, b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"))
        raw_body = json.dumps(body).encode()
        c.eq(
            "a JSON answer's headers",
            (line.split(" ", 2)[1], headers.get("Content-Type"), headers.get("Content-Length"), headers.get("Server", "").split(" ")[0]),
            ("200", "application/json", str(len(raw_body)), "address-flag-check"),
        )
        raw = _raw(port, b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nOrigin: https://evil.example\r\n\r\n")
        line, _, body = _respond(raw)
        c.eq(
            "a foreign origin is answered once, with 403 and the fixed message",
            (raw.count(b"HTTP/1.0 "), line.split(" ", 2)[1], body["message"] if body else None),
            (1, "403", "requests from this origin are not allowed"),
        )
        status, headers, body = _http(base, "/", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        c.eq("a notification over HTTP has an empty body of length 0", (status, headers.get("Content-Length"), body), (202, "0", None))
        status, _, body = _http(base, "/nope", {})
        c.eq(
            "POST to an unknown route",
            (status, body["code"], body["message"], sorted(body)),
            (404, "NOT_FOUND", "no route /nope", ["code", "details", "message", "trace_id"]),
        )
        c.true(
            "the trace id is 32 hex digits",
            isinstance(body.get("trace_id"), str)
            and len(body["trace_id"]) == 32
            and all(ch in "0123456789abcdef" for ch in body["trace_id"]),
        )
        status, _, body = _http(base, "/nope", method="DELETE")
        c.eq("DELETE to an unknown route", (status, body["code"], body["message"]), (404, "NOT_FOUND", "no route /nope"))
        status, _, body = _http(base, "/tools/boom/call", {})
        c.eq("an internal error over REST", (status, body["code"], body["message"]), (500, "INTERNAL_ERROR", "RuntimeError"))
        ticks = iter([10.0, 12.5])
        real_time = mod.time
        mod.time = types.SimpleNamespace(monotonic=lambda: next(ticks))
        try:
            status, _, body = _http(base, "/tools/address_flag_sources/call", {})
        finally:
            mod.time = real_time
        c.eq("the latency of a REST call, in whole milliseconds", (status, body.get("latency_ms")), (200, 2500))
        for _ in range(8):
            t = threading.Thread(target=lambda: _http(base, "/tools/wait/call", {}), daemon=True)
            t.start()
            threads.append(t)
        deadline = time.monotonic() + 5.0
        c.eq(
            "eight requests are being served",
            all(entered.acquire(timeout=max(0.0, deadline - time.monotonic())) for _ in range(8)),
            True,
        )
        status, headers, body = c.run("a ninth request", lambda: _http_timeout(base, "/tools/address_flag_sources/call", {}, 3.0)) or (
            None,
            {},
            None,
        )
        c.eq(
            "a ninth request at the same time is refused as busy",
            (status, (body or {}).get("code"), (body or {}).get("message"), headers.get("Retry-After")),
            (503, "BUSY", "too many concurrent requests", "2"),
        )
    finally:
        release.set()
        for t in threads:
            t.join(10)
        srv.shutdown()
        srv.server_close()
    return c.bad


def _http_timeout(base: str, path: str, body: Any, timeout: float) -> tuple[int, dict[str, str], Any]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read()
            return r.status, dict(r.headers), json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read()
        return e.code, dict(e.headers), json.loads(text) if text else None


def check_handler_timeout(mod: types.ModuleType, runners: dict[str, Any]) -> list[str]:
    """A connection that goes quiet is dropped after ten seconds, so a few open sockets cannot keep every other caller out."""
    got = mod.make_http_handler().timeout
    return [] if got == 10 else [f"the handler's idle timeout is {got!r}, expected 10 seconds"]


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        runners = _runners(pathlib.Path(tmp))
        for check in (
            check_legacy_era,
            check_stateless_era,
            check_stdio,
            check_http,
            check_units,
            check_body_reader,
            check_http_edges,
            check_handler_timeout,
        ):
            try:
                out.extend(check(mod, runners))
            except Exception as exc:
                out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
