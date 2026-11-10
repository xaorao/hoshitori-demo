"""Checks of ``evidence_delta.platform_skills``, runnable against a mutant: the platform's skill shapes, every branch of ``invoke``."""

from __future__ import annotations

import types
from typing import Any

from evidence_delta.errors import InputError
from tests.checks_support import Case

TOOL = {
    "name": "t",
    "description": "d",
    "inputSchema": {
        "type": "object",
        "required": ["a", "e"],
        "properties": {
            "a": {"type": "object", "description": "first"},
            "b": {"type": "array", "items": {"type": "string"}, "description": "list"},
            "c": {"type": "integer"},
            "d": {"type": "string", "enum": ["x", "y"], "items": {"type": "string"}},
            "e": {"type": "boolean"},
            "f": {"type": "array", "items": "not a dict"},
            "g": {"type": ["string", "null"]},
            "h": {"type": "number"},
            "i": {"type": "weird"},
        },
    },
}


def check_definition(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    t = mod.TYPES
    c.eq(
        "types",
        t,
        {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "object": "object", "array": "array"},
    )
    for kind, want in (
        ("string", "string"),
        ("number", "number"),
        ("integer", "number"),
        ("boolean", "boolean"),
        ("object", "object"),
        ("array", "array"),
        ("null", "string"),
        ("weird", "string"),
        (["string"], "string"),
    ):
        c.eq(f"arg_type {kind!r}", mod.arg_type({"type": kind}), want)
    c.eq("arg_type without a type", mod.arg_type({}), "string")
    d = mod.definition(TOOL)
    c.eq("head", {k: v for k, v in d.items() if k != "args"}, {"name": "t", "description": "d", "requires_guard": False, "xp": 0})
    c.eq("argument names in order", [a["name"] for a in d["args"]], list("abcdefghi"))
    by = {a["name"]: a for a in d["args"]}
    c.eq("a", by["a"], {"name": "a", "type": "object", "required": True, "description": "first", "enum": None, "items": None})
    c.eq("b", by["b"], {"name": "b", "type": "array", "required": False, "description": "list", "enum": None, "items": {"type": "string"}})
    c.eq("c", by["c"], {"name": "c", "type": "number", "required": False, "description": "", "enum": None, "items": None})
    c.eq(
        "d keeps its enum, and no items unless an array",
        by["d"],
        {"name": "d", "type": "string", "required": False, "description": "", "enum": ["x", "y"], "items": None},
    )
    c.eq("e", (by["e"]["type"], by["e"]["required"]), ("boolean", True))
    c.eq("items must be an object", by["f"]["items"], None)
    c.eq("g", by["g"]["type"], "string")
    c.eq("h", by["h"]["type"], "number")
    c.eq("i", by["i"]["type"], "string")
    d = mod.definition({"name": "n", "description": "x"}, xp=3, requires_guard=True)
    c.eq("no schema, options given", d, {"name": "n", "description": "x", "args": [], "requires_guard": True, "xp": 3})
    d = mod.definition({"name": "n", "description": "x", "inputSchema": {"properties": {"p": {}}}})
    c.eq("no required list", (d["args"][0]["required"], d["args"][0]["type"]), (False, "string"))
    c.eq("listing", [x["name"] for x in mod.listing([TOOL, {**TOOL, "name": "u"}])], ["t", "u"])
    c.eq("listing is definitions", mod.listing([TOOL])[0], mod.definition(TOOL))
    c.eq("listing of nothing", mod.listing([]), [])
    return c.bad


def _invalid(loc: list[Any], msg: str) -> tuple[int, dict[str, Any]]:
    return 422, {"detail": [{"loc": loc, "msg": msg, "type": "value_error"}]}


def check_invoke(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    seen: list[Any] = []

    def ok(args: object) -> dict[str, Any]:
        seen.append(args)
        return {"fine": True}

    def bad_input(args: object) -> dict[str, Any]:
        raise InputError("f", "broken", {"ex": 1})

    def boom(args: object) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    runners = {"ok": ok, "bad": bad_input, "boom": boom}
    inv = mod.invoke
    c.eq("unknown skill", inv("nope", {"name": "nope", "args": {}}, runners), _invalid(["path", "name"], "unknown skill 'nope'"))
    c.eq("unknown skill is checked first", inv("nope", None, runners), _invalid(["path", "name"], "unknown skill 'nope'"))
    for body in (None, [], "x", 5):
        c.eq(f"body {body!r}", inv("ok", body, runners), _invalid(["body"], "the body must be an object"))
    c.eq("name missing", inv("ok", {"args": {}}, runners), _invalid(["body", "name"], "must equal the name in the path"))
    c.eq("name differs", inv("ok", {"name": "bad", "args": {}}, runners), _invalid(["body", "name"], "must equal the name in the path"))
    for args in (None, [], "x"):
        c.eq(f"args {args!r}", inv("ok", {"name": "ok", "args": args}, runners), _invalid(["body", "args"], "must be an object"))
    c.eq("args missing", inv("ok", {"name": "ok"}, runners), _invalid(["body", "args"], "must be an object"))
    for key in ("conversation_id", "message_id"):
        for value in (5, [], {}, True):
            c.eq(
                f"{key} {value!r}",
                inv("ok", {"name": "ok", "args": {}, key: value}, runners),
                _invalid(["body", key], "must be a string or null"),
            )
        for value in (None, "s", ""):
            c.eq(f"{key} {value!r} is fine", inv("ok", {"name": "ok", "args": {}, key: value}, runners)[0], 200)
    c.eq(
        "conversation_id is checked before message_id",
        inv("ok", {"name": "ok", "args": {}, "conversation_id": 1, "message_id": 2}, runners)[1]["detail"][0]["loc"],
        ["body", "conversation_id"],
    )
    ticks = iter([10.0, 10.2504])
    status, payload = inv("ok", {"name": "ok", "args": {"q": 1}}, runners, clock=lambda: next(ticks))
    c.eq(
        "success",
        (status, payload),
        (200, {"name": "ok", "status": "success", "result": {"fine": True}, "latency_ms": 250, "xp": 0, "guard_decision": None}),
    )
    c.eq("the args reach the runner", seen[-1], {"q": 1})
    ticks = iter([0.0, 1.0])
    status, payload = inv("bad", {"name": "bad", "args": {}}, runners, clock=lambda: next(ticks))
    result = payload["result"]
    c.eq(
        "input error",
        (status, payload["status"], payload["latency_ms"], payload["xp"], payload["guard_decision"], payload["name"]),
        (200, "error", 1000, 0, None, "bad"),
    )
    c.eq(
        "input error body",
        {k: v for k, v in result.items() if k != "trace_id"},
        {"code": "INVALID_ARGUMENTS", "message": "f: broken", "details": {"field": "f", "example": {"ex": 1}}},
    )
    c.eq("trace id", (isinstance(result["trace_id"], str), len(result["trace_id"])), (True, 32))
    status, payload = inv("boom", {"name": "boom", "args": {}}, runners)
    result = payload["result"]
    c.eq("internal error", (status, payload["status"], payload["name"]), (200, "error", "boom"))
    c.eq(
        "internal error body",
        {k: v for k, v in result.items() if k != "trace_id"},
        {"code": "INTERNAL_ERROR", "message": "RuntimeError", "details": None},
    )
    c.eq("internal error trace id", len(result["trace_id"]), 32)
    c.eq("no leak", "secret" in str(payload), False)
    c.eq("latency is an int", type(inv("ok", {"name": "ok", "args": {}}, runners)[1]["latency_ms"]).__name__, "int")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_definition, check_invoke):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
