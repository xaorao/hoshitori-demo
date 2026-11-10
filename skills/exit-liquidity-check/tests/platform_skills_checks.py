"""Checks of ``exit_liquidity_check.platform_skills``, runnable against a mutant: the platform's skill shapes and every refusal of an invocation."""

from __future__ import annotations

import types
from typing import Any

from exit_liquidity_check.errors import InputError
from tests.checks_support import Case

TOOL = {
    "name": "t",
    "description": "d",
    "inputSchema": {
        "required": ["a", "e"],
        "properties": {
            "a": {"type": "string", "description": "first", "enum": ["x", "y"]},
            "b": {"type": "integer"},
            "c": {"type": "number", "description": "num"},
            "d": {"type": "array", "items": {"type": "string"}},
            "e": {"type": "boolean"},
            "f": {"type": "object"},
            "g": {"type": "array"},
            "h": {"type": "array", "items": "bad"},
            "i": {"type": "null"},
            "j": {},
            "k": {"type": ["string", "null"]},
            "l": {"type": "string", "items": {"type": "string"}},
        },
    },
}


def _invalid(loc: list[Any], msg: str) -> tuple[int, dict[str, Any]]:
    return 422, {"detail": [{"loc": loc, "msg": msg, "type": "value_error"}]}


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "type table",
        mod.TYPES,
        {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "object": "object", "array": "array"},
    )
    for schema, want in (
        ({"type": "string"}, "string"),
        ({"type": "integer"}, "number"),
        ({"type": "number"}, "number"),
        ({"type": "boolean"}, "boolean"),
        ({"type": "object"}, "object"),
        ({"type": "array"}, "array"),
        ({"type": "null"}, "string"),
        ({}, "string"),
        ({"type": ["string"]}, "string"),
        ({"type": 5}, "string"),
    ):
        c.eq(f"arg_type({schema})", mod.arg_type(schema), want)
    d = mod.definition(TOOL)
    c.eq(
        "name, description, guard, xp",
        (d["name"], d["description"], d["requires_guard"], d["xp"], sorted(d)),
        ("t", "d", False, 0, ["args", "description", "name", "requires_guard", "xp"]),
    )
    arg = lambda name: next(a for a in d["args"] if a["name"] == name)  # noqa: E731
    c.eq("argument order is the schema's", [a["name"] for a in d["args"]], list("abcdefghijkl"))
    c.eq(
        "a string with an enum",
        arg("a"),
        {"name": "a", "type": "string", "required": True, "description": "first", "enum": ["x", "y"], "items": None},
    )
    c.eq(
        "an integer is a number and optional",
        arg("b"),
        {"name": "b", "type": "number", "required": False, "description": "", "enum": None, "items": None},
    )
    c.eq("a number with a description", (arg("c")["type"], arg("c")["description"]), ("number", "num"))
    c.eq("an array keeps its items", arg("d")["items"], {"type": "string"})
    c.eq("a required boolean", (arg("e")["type"], arg("e")["required"]), ("boolean", True))
    c.eq("an object", arg("f")["type"], "object")
    c.eq("an array without items", arg("g")["items"], None)
    c.eq("an array whose items are not a schema", arg("h")["items"], None)
    c.eq("a type outside the platform's list is a string", arg("i")["type"], "string")
    c.eq("no type is a string", arg("j")["type"], "string")
    c.eq("a union is a string", arg("k")["type"], "string")
    c.eq("items on a non-array are dropped", arg("l")["items"], None)
    c.eq("a tool with no schema has no arguments", mod.definition({"name": "n", "description": "x"})["args"], [])
    c.eq(
        "a schema with no properties has no arguments",
        mod.definition({"name": "n", "description": "x", "inputSchema": {"required": []}})["args"],
        [],
    )
    d2 = mod.definition(TOOL, xp=7, requires_guard=True)
    c.eq("guard and xp are passed through", (d2["requires_guard"], d2["xp"]), (True, 7))
    c.eq(
        "listing",
        mod.listing([TOOL, {"name": "u", "description": "v"}]),
        [mod.definition(TOOL), mod.definition({"name": "u", "description": "v"})],
    )
    c.eq("an empty listing", mod.listing([]), [])

    # invoke
    seen: list[Any] = []

    def ok(args: Any) -> dict[str, Any]:
        seen.append(args)
        return {"fine": True}

    def refuse(args: Any) -> dict[str, Any]:
        raise InputError("token", "is required", {"token": "SOL"})

    def boom(args: Any) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    runners = {"ok": ok, "refuse": refuse, "boom": boom}
    inv = lambda name, body: mod.invoke(name, body, runners, clock=iter([10.0, 10.0]).__next__)  # noqa: E731
    c.eq("an unknown skill", inv("nope", {"name": "nope", "args": {}}), _invalid(["path", "name"], "unknown skill 'nope'"))
    for label, body in (("a list", []), ("none", None), ("text", "x"), ("a number", 5)):
        c.eq(f"body {label}", inv("ok", body), _invalid(["body"], "the body must be an object"))
    c.eq("a name that differs", inv("ok", {"name": "x", "args": {}}), _invalid(["body", "name"], "must equal the name in the path"))
    c.eq("no name", inv("ok", {"args": {}}), _invalid(["body", "name"], "must equal the name in the path"))
    for label, args in (("missing", None), ("a list", []), ("text", "x")):
        body = {"name": "ok"} if args is None else {"name": "ok", "args": args}
        c.eq(f"args {label}", inv("ok", body), _invalid(["body", "args"], "must be an object"))
    for key in ("conversation_id", "message_id"):
        c.eq(f"{key} must be text", inv("ok", {"name": "ok", "args": {}, key: 5}), _invalid(["body", key], "must be a string or null"))
        for fine in ("c1", None):
            c.eq(f"{key}={fine!r} is accepted", inv("ok", {"name": "ok", "args": {}, key: fine})[0], 200)
    c.eq(
        "conversation_id is judged before message_id",
        inv("ok", {"name": "ok", "args": {}, "conversation_id": 1, "message_id": 2})[1]["detail"][0]["loc"],
        ["body", "conversation_id"],
    )
    seen.clear()
    c.eq(
        "a success",
        inv("ok", {"name": "ok", "args": {"q": 1}}),
        (200, {"name": "ok", "status": "success", "result": {"fine": True}, "latency_ms": 0, "xp": 0, "guard_decision": None}),
    )
    c.eq("the runner gets the args object", seen, [{"q": 1}])
    c.eq("an empty args object is valid", inv("ok", {"name": "ok", "args": {}})[0], 200)
    status, payload = inv("refuse", {"name": "refuse", "args": {}})
    c.eq(
        "a refusal is a 200 with status error and the typed error",
        (
            status,
            payload["status"],
            payload["name"],
            payload["result"]["code"],
            payload["result"]["message"],
            payload["result"]["details"],
            len(payload["result"]["trace_id"]),
        ),
        (200, "error", "refuse", "INVALID_ARGUMENTS", "token: is required", {"field": "token", "example": {"token": "SOL"}}, 32),
    )
    status, payload = inv("boom", {"name": "boom", "args": {}})
    c.eq(
        "an internal error names its class only",
        (
            status,
            payload["status"],
            payload["result"]["code"],
            payload["result"]["message"],
            payload["result"]["details"],
            "secret" in str(payload),
            len(payload["result"]["trace_id"]),
        ),
        (200, "error", "INTERNAL_ERROR", "RuntimeError", None, False, 32),
    )
    c.eq("... and still carries xp and guard", (payload["xp"], payload["guard_decision"]), (0, None))
    ticks = iter([10.0, 12.5])
    c.eq(
        "latency in whole milliseconds", mod.invoke("ok", {"name": "ok", "args": {}}, runners, clock=ticks.__next__)[1]["latency_ms"], 2500
    )
    ticks = iter([10.0, 10.0009])
    c.eq("latency truncates", mod.invoke("ok", {"name": "ok", "args": {}}, runners, clock=ticks.__next__)[1]["latency_ms"], 0)
    return c.failed
