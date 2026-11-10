"""MCP (JSON-RPC over stdio and HTTP) and REST front ends, exercised against a real local server."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import CAPTURED

from indicator_condition_check import OUTPUT_SCHEMA, TOOL_DEFINITION, run
from indicator_condition_check.server import LEGACY_PROTOCOLS, handle_rpc, make_http_handler, serve_http, serve_stdio

jsonschema = pytest.importorskip("jsonschema")
ARGS = {"symbol": "SOL", "lookback_bars": 250, "condition": {"left": {"indicator": "rsi"}, "op": "<", "right": {"value": 70}}}


@pytest.fixture
def runner(all_venues_recorded):
    return lambda args: run(args, http_get=all_venues_recorded, now=CAPTURED)


def rpc(method, params=None, mid=1, runner_=None):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return handle_rpc(msg, runner_) if runner_ else handle_rpc(msg)


def test_initialize_negotiates_a_supported_protocol_version():
    for v in LEGACY_PROTOCOLS:
        assert rpc("initialize", {"protocolVersion": v})["result"]["protocolVersion"] == v
    assert rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == LEGACY_PROTOCOLS[0] == "2025-11-25"  # the latest
    r = rpc("initialize", {})["result"]
    assert r["capabilities"] == {"tools": {"listChanged": False}} and r["serverInfo"]["name"] == "indicator-condition-check"


def test_tools_list_has_name_description_schema_and_annotations():
    (tool,) = rpc("tools/list")["result"]["tools"]
    assert tool == TOOL_DEFINITION
    assert tool["name"] == "indicator_condition_check"
    assert tool["annotations"]["readOnlyHint"] is True and tool["annotations"]["idempotentHint"] is True
    jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
    jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
    for part in ("Delivery:", "Use when:", "Core inputs:", "Limits:"):
        assert part in tool["description"]


def test_tools_call_success_returns_text_and_structured_content(runner):
    res = rpc("tools/call", {"name": "indicator_condition_check", "arguments": ARGS}, runner_=runner)["result"]
    assert res["isError"] is False
    envelope = json.loads(res["content"][0]["text"])
    assert envelope == res["structuredContent"]
    jsonschema.validate(envelope, OUTPUT_SCHEMA)


def test_tools_call_validation_error_is_a_tool_error_with_an_example(runner):
    res = rpc("tools/call", {"name": "indicator_condition_check", "arguments": {"symbol": "SOL"}}, runner_=runner)["result"]
    assert res["isError"] is True
    body = json.loads(res["content"][0]["text"])
    assert body["code"] == "INVALID_ARGUMENTS" and body["details"]["field"] == "condition" and "example" in body["details"] and body["trace_id"]


def test_unexpected_exception_does_not_crash_the_server():
    def boom(args):
        raise RuntimeError("secret internal detail")

    res = rpc("tools/call", {"name": "indicator_condition_check", "arguments": ARGS}, runner_=boom)["result"]
    assert res["isError"] is True
    text = res["content"][0]["text"]
    assert "INTERNAL_ERROR" in text and "secret internal detail" not in text  # class name only, no leakage


def test_protocol_errors():
    assert rpc("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    assert rpc("no/such/method")["error"]["code"] == -32601
    assert handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle_rpc("garbage")["error"]["code"] == -32600
    assert rpc("ping")["result"] == {}


def test_stdio_loop_handles_parse_errors_and_continues(runner):
    stdin = io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n')
    out = io.StringIO()
    serve_stdio(runner, stdin=stdin, stdout=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700 and lines[1]["result"] == {}


@pytest.fixture
def server(runner):
    srv = serve_http("127.0.0.1", 0, runner)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def http(base, path, body=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_rest_health_tools_and_call(server):
    assert http(server, "/health")[1]["status"] == "ok"
    assert http(server, "/tools")[1]["tools"][0]["name"] == "indicator_condition_check"
    status, body = http(server, "/tools/indicator_condition_check/call", ARGS)
    assert status == 200
    jsonschema.validate(body["result"], OUTPUT_SCHEMA)


def test_rest_error_envelopes(server):
    status, body = http(server, "/tools/indicator_condition_check/call", {"symbol": "SOL"})
    assert status == 400 and body["code"] == "INVALID_ARGUMENTS" and body["trace_id"]
    assert http(server, "/tools/other_tool/call", {})[0] == 404
    assert http(server, "/nope")[0] == 404
    status, body = http(server, "/tools/indicator_condition_check/call", raw=b"{not json")
    assert status == 400 and body["code"] == "INVALID_JSON"
    status, body = http(server, "/tools/indicator_condition_check/call", raw=b"x" * (70 * 1024))
    assert status in (400, 413)


def test_mcp_over_http_post_root(server):
    status, body = http(server, "/", {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert status == 200 and body["id"] == 5 and body["result"]["tools"][0]["name"] == "indicator_condition_check"
    req = urllib.request.Request(server + "/", data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 202


def test_handler_factory_is_independent_per_runner(runner):
    assert make_http_handler(runner) is not make_http_handler(runner)


# ----- MCP 2026-07-28 (stateless) -------------------------------------------------------------------------------------

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}, "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"}}
IDENTITY = {"io.modelcontextprotocol/serverInfo": {"name": "indicator-condition-check", "version": "0.2.0"}}


def test_server_discover_lists_the_stateless_versions_and_carries_identity_in_meta():
    r = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": META}})["result"]
    assert r["resultType"] == "complete" and r["supportedVersions"] == ["2026-07-28"] and r["cacheScope"] == "public"
    assert r["_meta"] == IDENTITY and "serverInfo" not in r  # the TypeScript client reads identity only from _meta
    assert handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "server/discover"})["error"]["code"] == -32601  # no claim: a handshake-era request


def test_stateless_requests_get_resulttype_and_cache_hints_and_legacy_requests_do_not():
    new = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": META}})["result"]
    assert new["resultType"] == "complete" and new["ttlMs"] == 300000 and new["cacheScope"] == "public" and new["tools"][0]["name"] == "indicator_condition_check"
    old = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]
    assert "resultType" not in old and "ttlMs" not in old


def test_stateless_tool_call_works_without_any_handshake(runner):
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "indicator_condition_check", "arguments": ARGS, "_meta": META}}
    res = handle_rpc(msg, runner)["result"]
    assert res["resultType"] == "complete" and res["isError"] is False and res["structuredContent"]["tool"] == "indicator_condition_check"


def test_unsupported_protocol_version_is_32022_with_the_supported_list():
    bad = {"io.modelcontextprotocol/protocolVersion": "2031-01-01", "io.modelcontextprotocol/clientCapabilities": {}}
    r = handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": bad}})
    assert r["error"]["code"] == -32022 and r["error"]["data"] == {"supported": ["2026-07-28"], "requested": "2031-01-01"}


def test_tool_definition_snapshot_and_name_lint():
    """A change to the published tool contract must be deliberate: update this snapshot with it."""
    import re

    tool = TOOL_DEFINITION
    assert re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tool["name"]) and tool["title"]
    props = tool["inputSchema"]["properties"]
    assert sorted(props) == ["condition", "cross_venue_check", "lookback_bars", "symbol", "timeframe", "venue"]
    assert tool["inputSchema"]["required"] == ["symbol", "condition"] and tool["inputSchema"]["additionalProperties"] is False
    assert {k: props[k].get("default") for k in ("timeframe", "lookback_bars", "venue", "cross_venue_check")} == {"timeframe": "1d", "lookback_bars": 300, "venue": "auto", "cross_venue_check": False}
    assert tool["annotations"]["readOnlyHint"] is True and tool["annotations"]["destructiveHint"] is False


def test_stdio_subprocess_speaks_both_protocol_eras():
    import subprocess
    import sys

    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {"_meta": META}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "indicator_condition_check", "arguments": {"symbol": "SOL"}}},
    ]
    p = subprocess.run([sys.executable, "-m", "indicator_condition_check", "mcp"], input="\n".join(json.dumps(x) for x in lines) + "\n", capture_output=True, text=True, timeout=30)
    replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
    assert replies[1]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[2]["result"]["tools"][0]["name"] == "indicator_condition_check"
    assert replies[3]["result"]["supportedVersions"] == ["2026-07-28"] and replies[3]["result"]["_meta"] == IDENTITY
    assert replies[4]["result"]["isError"] is True  # invalid arguments are a tool error, answered without any network call
    assert p.returncode == 0 and len(replies) == 4  # the notification produced no reply


# ----- HTTP front-door hygiene -------------------------------------------------------------------------------------------

def _raw(base, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


def test_foreign_browser_origins_are_refused_but_local_and_headerless_clients_work(server):
    status, _, body = _raw(server, "/health", headers={"Origin": "https://evil.example"})
    assert status == 403 and body["code"] == "FORBIDDEN_ORIGIN"
    assert _raw(server, "/health")[0] == 200  # curl, SDKs and servers send no Origin
    host = server.split("//")[1]
    assert _raw(server, "/health", headers={"Origin": f"http://{host}"})[0] == 200


def test_tool_calls_are_rate_limited_per_client(runner):
    srv = serve_http("127.0.0.1", 0, runner, rate_limit_per_minute=3)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        codes = [_raw(base, "/tools/indicator_condition_check/call", ARGS)[0] for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        status, headers, body = _raw(base, "/tools/indicator_condition_check/call", ARGS)
        assert status == 429 and body["code"] == "RATE_LIMITED" and headers.get("Retry-After") == "10"
        assert _raw(base, "/health")[0] == 200  # reads are not limited
    finally:
        srv.shutdown()
        srv.server_close()
