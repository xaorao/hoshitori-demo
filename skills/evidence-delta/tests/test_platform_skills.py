"""The platform's skill shape (SkillDefinition, SkillCallRequest, SkillCallResponse) over the unit functions and over real HTTP."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from evidence_delta import TOOL_DEFINITIONS, TOOL_NAME, platform_skills, server

EXAMPLE = {"before": {"a": 1}, "after": {"a": 2}}


def test_definition_has_the_platform_fields() -> None:
    d = platform_skills.definition(TOOL_DEFINITIONS[0])
    assert set(d) == {"name", "description", "args", "requires_guard", "xp"}
    assert d["name"] == TOOL_NAME and d["requires_guard"] is False and d["xp"] == 0
    by_name = {a["name"]: a for a in d["args"]}
    assert set(by_name) == {"before", "after", "fields", "min_change_pct"}
    assert (by_name["before"]["type"], by_name["before"]["required"]) == ("object", True)
    assert (by_name["fields"]["type"], by_name["fields"]["required"], by_name["fields"]["items"]) == (
        "array",
        False,
        {"type": "string", "maxLength": 200},
    )
    assert by_name["min_change_pct"]["type"] == "number" and by_name["min_change_pct"]["enum"] is None
    assert all(set(a) == {"name", "type", "required", "description", "enum", "items"} for a in d["args"])
    assert all(a["type"] in ("string", "number", "boolean", "object", "array") for a in d["args"])


def test_integer_and_unknown_types_map_to_the_platform_types() -> None:
    assert platform_skills.arg_type({"type": "integer"}) == "number"
    assert platform_skills.arg_type({"type": "null"}) == "string"
    assert platform_skills.arg_type({}) == "string"


def test_invoke_success_and_error() -> None:
    status, out = platform_skills.invoke(TOOL_NAME, {"name": TOOL_NAME, "args": EXAMPLE}, server.RUNNERS)
    assert status == 200 and out["status"] == "success" and out["result"]["data"]["verdict"] == "changed"
    assert set(out) == {"name", "status", "result", "latency_ms", "xp", "guard_decision"} and out["guard_decision"] is None
    status, out = platform_skills.invoke(TOOL_NAME, {"name": TOOL_NAME, "args": {"before": {}}}, server.RUNNERS)
    assert status == 200 and out["status"] == "error" and out["result"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize(
    "path,body",
    [
        ("nope", {"name": "nope", "args": {}}),
        (TOOL_NAME, [1]),
        (TOOL_NAME, {"name": "other", "args": {}}),
        (TOOL_NAME, {"name": TOOL_NAME}),
        (TOOL_NAME, {"name": TOOL_NAME, "args": []}),
        (TOOL_NAME, {"name": TOOL_NAME, "args": {}, "conversation_id": 5}),
    ],
)
def test_malformed_requests_are_422_with_a_location(path: str, body: Any) -> None:
    status, out = platform_skills.invoke(path, body, server.RUNNERS)
    assert status == 422 and out["detail"][0]["loc"] and out["detail"][0]["msg"]


def test_a_crashing_skill_is_an_error_with_the_class_name_only() -> None:
    def boom(_args: Any) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    status, out = platform_skills.invoke("x", {"name": "x", "args": {}}, {"x": boom})
    assert status == 200 and out["status"] == "error" and out["result"]["message"] == "RuntimeError" and "secret" not in json.dumps(out)


@pytest.fixture()
def base() -> Iterator[str]:
    srv = server.serve_http("127.0.0.1", 0)
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
    assert http(base + "/api/skills")[0] == 200
    status, one = http(base + f"/api/skills/{TOOL_NAME}")
    assert status == 200 and one["name"] == TOOL_NAME
    assert http(base + "/api/skills/nope")[0] == 404
    status, out = http(base + f"/api/skills/{TOOL_NAME}/invoke", {"name": TOOL_NAME, "args": EXAMPLE})
    assert status == 200 and out["status"] == "success"
    assert http(base + f"/api/skills/{TOOL_NAME}/invoke", {"name": TOOL_NAME})[0] == 422
    assert http(base + "/api/skills/nope/invoke", {"name": "nope", "args": {}})[0] == 422
    status, out = http(base + f"/tools/{TOOL_NAME}/call", EXAMPLE)  # the builder-style route is unchanged
    assert status == 200 and out["tool"] == TOOL_NAME
