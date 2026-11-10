"""The platform's skill shape (SkillDefinition, SkillCallRequest, SkillCallResponse) over the unit functions and over real HTTP."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from exit_liquidity_check import TOOL_DEFINITIONS, TOOL_NAME, platform_skills, run, run_sources, server
from tests.support import NOW, FakeTransport, caller_for


def offline_run(args: object, **_: Any) -> dict[str, Any]:
    return run(args, now=NOW, caller=caller_for(FakeTransport()))


RUNNERS = {TOOL_NAME: offline_run, "exit_liquidity_sources": run_sources}


def test_definition_has_the_platform_fields() -> None:
    d = platform_skills.definition(TOOL_DEFINITIONS[0])
    assert set(d) == {"name", "description", "args", "requires_guard", "xp"}
    by_name = {a["name"]: a for a in d["args"]}
    assert set(by_name) == {"token", "size_usd", "max_slippage_pct", "participation_pct"}
    assert (by_name["token"]["type"], by_name["token"]["required"]) == ("string", True)
    assert by_name["token"]["enum"] == ["SOL", "BONK", "JUP", "WIF", "ETH", "BTC"]
    assert (by_name["size_usd"]["type"], by_name["size_usd"]["required"]) == ("number", False)
    assert all(set(a) == {"name", "type", "required", "description", "enum", "items"} for a in d["args"])


def test_invoke_success_error_and_malformed() -> None:
    status, out = platform_skills.invoke(TOOL_NAME, {"name": TOOL_NAME, "args": {"token": "BONK", "size_usd": 1000}}, RUNNERS)
    assert status == 200 and out["status"] == "success" and out["result"]["tool"] == TOOL_NAME and out["xp"] == 0
    status, out = platform_skills.invoke(TOOL_NAME, {"name": TOOL_NAME, "args": {"token": "DOGE"}}, RUNNERS)
    assert status == 200 and out["status"] == "error" and out["result"]["code"] == "INVALID_ARGUMENTS"
    for path, body in (
        ("nope", {"name": "nope", "args": {}}),
        (TOOL_NAME, [1]),
        (TOOL_NAME, {"name": "other", "args": {}}),
        (TOOL_NAME, {"name": TOOL_NAME}),
        (TOOL_NAME, {"name": TOOL_NAME, "args": {}, "message_id": 3}),
    ):
        status, out = platform_skills.invoke(path, body, RUNNERS)
        assert status == 422 and out["detail"][0]["loc"], (path, body)


@pytest.fixture()
def base() -> Iterator[str]:
    srv = server.serve_http("127.0.0.1", 0, RUNNERS)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def http(url: str, body: Any = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        with err:
            return err.code, json.loads(err.read())


def test_http_list_get_and_invoke(base: str) -> None:
    status, listing = http(base + "/api/skills/")
    assert status == 200 and [s["name"] for s in listing] == [t["name"] for t in TOOL_DEFINITIONS]
    assert http(base + f"/api/skills/{TOOL_NAME}")[0] == 200 and http(base + "/api/skills/nope")[0] == 404
    status, out = http(base + f"/api/skills/{TOOL_NAME}/invoke", {"name": TOOL_NAME, "args": {"token": "BONK"}})
    assert status == 200 and out["status"] == "success"
    assert http(base + f"/api/skills/{TOOL_NAME}/invoke", {"name": TOOL_NAME})[0] == 422
    assert http(base + f"/tools/{TOOL_NAME}/call", {"token": "BONK"})[0] == 200  # the builder-style route is unchanged
