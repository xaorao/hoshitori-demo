"""Checks of ``ryo_verdict_record.platform_skills``, runnable against a mutant: the type words, the definition built from a tool definition
(every member, in order), the list, and every answer of an invocation (the 422 bodies word for word, the status, the latency)."""

from __future__ import annotations

import types
from typing import Any

from ryo_verdict_record.errors import InputError

TOOL = {
    "name": "t",
    "description": "d",
    "inputSchema": {
        "type": "object",
        "required": ["a", "e"],
        "properties": {
            "a": {"type": "string", "description": "da"},
            "b": {"type": "integer"},
            "c": {"type": "number", "description": "dc"},
            "d": {"type": "boolean"},
            "e": {"type": "object", "description": "de", "items": {"type": "string"}},
            "f": {"type": "array", "items": {"type": "string"}},
            "g": {"type": "array", "items": "not a schema"},
            "h": {"type": "array"},
            "i": {"type": "string", "enum": ["x", "y"]},
            "j": {"type": ["string", "null"]},
            "k": {},
            "l": {"type": "mystery"},
        },
    },
}


def _arg(name: str, kind: str, required: bool, description: str = "", enum: Any = None, items: Any = None) -> dict[str, Any]:
    return {"name": name, "type": kind, "required": required, "description": description, "enum": enum, "items": items}


ARGS = [
    _arg("a", "string", True, "da"),
    _arg("b", "number", False),
    _arg("c", "number", False, "dc"),
    _arg("d", "boolean", False),
    _arg("e", "object", True, "de"),
    _arg("f", "array", False, items={"type": "string"}),
    _arg("g", "array", False),
    _arg("h", "array", False),
    _arg("i", "string", False, enum=["x", "y"]),
    _arg("j", "string", False),
    _arg("k", "string", False),
    _arg("l", "string", False),
]


def _unprocessable(loc: list[Any], msg: str) -> tuple[int, dict[str, Any]]:
    return 422, {"detail": [{"loc": loc, "msg": msg, "type": "value_error"}]}


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []

    def eq(label: str, got: Any, want: Any) -> None:
        if got != want or repr(sorted(got) if isinstance(got, dict) else got) != repr(sorted(want) if isinstance(want, dict) else want):
            out.append(f"{label}: got {got!r}, expected {want!r}")

    for kind, want in (
        ("string", "string"),
        ("number", "number"),
        ("integer", "number"),
        ("boolean", "boolean"),
        ("object", "object"),
        ("array", "array"),
    ):
        eq(f"arg_type {kind}", mod.arg_type({"type": kind}), want)
    for label, schema in (
        ("unknown word", {"type": "mystery"}),
        ("none", {}),
        ("list", {"type": ["string", "null"]}),
        ("null", {"type": None}),
    ):
        eq(f"arg_type {label}", mod.arg_type(schema), "string")
    eq(
        "TYPES",
        mod.TYPES,
        {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "object": "object", "array": "array"},
    )

    got = mod.definition(TOOL)
    eq("definition", got, {"name": "t", "description": "d", "args": ARGS, "requires_guard": False, "xp": 0})
    eq("definition order of members", list(got), ["name", "description", "args", "requires_guard", "xp"])
    eq("definition order of an argument", list(got["args"][0]), ["name", "type", "required", "description", "enum", "items"])
    eq("definition, guarded and worth xp", mod.definition(TOOL, xp=5, requires_guard=True)["xp"], 5)
    eq("definition, guarded", mod.definition(TOOL, xp=5, requires_guard=True)["requires_guard"], True)
    eq(
        "definition of a tool with no schema",
        mod.definition({"name": "n", "description": "x"}),
        {"name": "n", "description": "x", "args": [], "requires_guard": False, "xp": 0},
    )
    eq(
        "definition of a schema with no required",
        mod.definition({"name": "n", "description": "x", "inputSchema": {"properties": {"p": {}}}})["args"],
        [_arg("p", "string", False)],
    )
    eq(
        "listing",
        mod.listing([TOOL, {"name": "n", "description": "x"}]),
        [mod.definition(TOOL), mod.definition({"name": "n", "description": "x"})],
    )
    eq("listing of none", mod.listing([]), [])

    seen: list[Any] = []

    def ok(args: object) -> dict[str, Any]:
        seen.append(args)
        return {"fine": True}

    def refuse(args: object) -> dict[str, Any]:
        raise InputError("f", "bad", {"e": 1})

    def boom(args: object) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    runners = {"ok": ok, "refuse": refuse, "boom": boom}
    eq("unknown skill", mod.invoke("nope", {"name": "nope", "args": {}}, runners), _unprocessable(["path", "name"], "unknown skill 'nope'"))
    for label, body in (("none", None), ("list", []), ("text", "x"), ("number", 3)):
        eq(f"body {label}", mod.invoke("ok", body, runners), _unprocessable(["body"], "the body must be an object"))
    for label, body in (
        ("missing", {"args": {}}),
        ("other", {"name": "x", "args": {}}),
        ("null", {"name": None, "args": {}}),
        ("case", {"name": "OK", "args": {}}),
    ):
        eq(f"name {label}", mod.invoke("ok", body, runners), _unprocessable(["body", "name"], "must equal the name in the path"))
    for label, args in (("missing", None), ("list", []), ("text", "x"), ("null", None)):
        body = {"name": "ok"} if label == "missing" else {"name": "ok", "args": args}
        eq(f"args {label}", mod.invoke("ok", body, runners), _unprocessable(["body", "args"], "must be an object"))
    for key in ("conversation_id", "message_id"):
        for value in (5, True, ["x"], {}, 1.5):
            eq(
                f"{key} {value!r}",
                mod.invoke("ok", {"name": "ok", "args": {}, key: value}, runners),
                _unprocessable(["body", key], "must be a string or null"),
            )
        for value in ("c1", "", None):
            status, payload = mod.invoke("ok", {"name": "ok", "args": {}, key: value}, runners)
            eq(f"{key} {value!r} accepted", (status, payload["status"]), (200, "success"))
    eq("order of the checks", mod.invoke("nope", None, runners)[1]["detail"][0]["loc"], ["path", "name"])
    eq("body before name", mod.invoke("ok", [], runners)[1]["detail"][0]["loc"], ["body"])
    eq("name before args", mod.invoke("ok", {"name": "x"}, runners)[1]["detail"][0]["loc"], ["body", "name"])
    eq("args before ids", mod.invoke("ok", {"name": "ok", "conversation_id": 1}, runners)[1]["detail"][0]["loc"], ["body", "args"])

    ticks = iter([10.0, 12.5])
    seen.clear()
    status, payload = mod.invoke("ok", {"name": "ok", "args": {"q": 1}}, runners, lambda: next(ticks))
    eq(
        "a success",
        (status, payload),
        (200, {"name": "ok", "status": "success", "result": {"fine": True}, "latency_ms": 2500, "xp": 0, "guard_decision": None}),
    )
    eq("a success, order of members", list(payload), ["name", "status", "result", "latency_ms", "xp", "guard_decision"])
    eq("the runner got the args", seen, [{"q": 1}])
    ticks = iter([1.0, 1.0009])
    eq("latency is whole milliseconds, cut", mod.invoke("ok", {"name": "ok", "args": {}}, runners, lambda: next(ticks))[1]["latency_ms"], 0)
    status, payload = mod.invoke("refuse", {"name": "refuse", "args": {}}, runners)
    result = payload["result"]
    eq(
        "a refusal",
        (status, payload["status"], result["code"], result["message"], result["details"]),
        (200, "error", "INVALID_ARGUMENTS", "f: bad", {"field": "f", "example": {"e": 1}}),
    )
    eq("a refusal carries a trace id", (isinstance(result["trace_id"], str), len(result["trace_id"])), (True, 32))
    status, payload = mod.invoke("boom", {"name": "boom", "args": {}}, runners)
    result = payload["result"]
    eq(
        "an internal error",
        (status, payload["status"], result["code"], result["message"], result["details"], "secret" in str(payload)),
        (200, "error", "INTERNAL_ERROR", "RuntimeError", None, False),
    )
    eq("an internal error carries a trace id", (isinstance(result["trace_id"], str), len(result["trace_id"])), (True, 32))
    eq(
        "an error answer has the same members",
        (list(payload), payload["xp"], payload["guard_decision"], payload["name"]),
        (["name", "status", "result", "latency_ms", "xp", "guard_decision"], 0, None, "boom"),
    )
    eq("an error answer's latency is a count", isinstance(payload["latency_ms"], int), True)
    return out
