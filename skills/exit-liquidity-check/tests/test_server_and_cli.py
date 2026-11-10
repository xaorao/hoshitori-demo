"""The MCP and REST surfaces and the command line, with the network replaced by recorded replies."""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from exit_liquidity_check import OUTPUT_SCHEMA, TOOL_DEFINITIONS, TOOL_NAME, run, server
from exit_liquidity_check.__main__ import main
from tests.support import NOW, FakeTransport, caller_for


def runner(args: object, **_: Any) -> dict[str, Any]:
    return run(args, now=NOW, caller=caller_for(FakeTransport()))


def test_tool_definitions_follow_the_catalogue_template() -> None:
    assert [d["name"] for d in TOOL_DEFINITIONS] == [TOOL_NAME, "exit_liquidity_sources"]
    for d in TOOL_DEFINITIONS:
        text = d["description"]
        positions = [text.index(k) for k in ("Delivery:", "Use when:", "Core inputs:", "Limits:")]
        assert positions == sorted(positions)
        assert d["inputSchema"]["additionalProperties"] is False


def test_the_example_call_is_valid() -> None:
    jsonschema.validate({"token": "SOL", "size_usd": 50000}, TOOL_DEFINITIONS[0]["inputSchema"])


def test_mcp_tools_call_returns_the_envelope_as_text() -> None:
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL_NAME, "arguments": {"token": "BONK", "size_usd": 1000}},
    }
    out = server.handle_rpc(msg, {TOOL_NAME: runner})
    body = out.reply
    assert body is not None and body["result"]["isError"] is False
    envelope = json.loads(body["result"]["content"][0]["text"])
    jsonschema.validate(envelope, OUTPUT_SCHEMA)


def test_mcp_refusal_is_a_tool_error_with_an_example() -> None:
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": TOOL_NAME, "arguments": {"token": "DOGE"}}}
    body = server.handle_rpc(msg, {TOOL_NAME: runner}).reply
    assert body is not None
    text = json.dumps(body)
    assert "INVALID_ARGUMENTS" in text and "50000" in text


def test_cli_tool_example_sources_and_refusal(capsys: Any) -> None:
    assert main(["example"]) == 0
    assert json.loads(capsys.readouterr().out) == {"token": "SOL", "size_usd": 50000}
    assert main(["tool"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(["sources"]) == 0
    assert json.loads(capsys.readouterr().out)["tool"] == "exit_liquidity_sources"
    assert main(["check", "--token", "DOGE"]) == 2
    assert "INVALID_ARGUMENTS" in capsys.readouterr().err
