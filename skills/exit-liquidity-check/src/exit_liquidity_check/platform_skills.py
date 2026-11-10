"""The platform's own skill shape, served beside the builder-style routes.

RYO's public OpenAPI (``/api/openapi.json``) describes skills as a registry: ``GET /api/skills/`` lists ``SkillDefinition`` objects,
``GET /api/skills/{name}`` returns one, and ``POST /api/skills/{name}/invoke`` takes a ``SkillCallRequest`` (``name``, ``args``, optional
``conversation_id`` and ``message_id``) and answers a ``SkillCallResponse`` (``name``, ``status`` of pending, running, success or error,
``result``, ``latency_ms``, ``xp``, ``guard_decision``). A ``SkillDefinition`` is ``name``, ``description``, ``args`` (each ``name``,
``type`` of string, number, boolean, object or array, ``required``, ``description``, ``enum``, ``items``), ``requires_guard`` and ``xp``.

This module renders the tool definitions of this package in those shapes, so the skill can be listed and invoked the way the platform lists
and invokes its own. What is NOT VERIFIED: how the platform registers an external skill (its ``name`` is a closed enumeration of the
platform's own skills), what a failed invocation looks like on the wire (here: status ``error`` with the error body as the result, HTTP
200), and whether any ``xp`` is expected (none is claimed: 0). These are read from the schema in the OpenAPI, not from a running service.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .errors import InputError

TYPES = {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "object": "object", "array": "array"}


def arg_type(schema: Mapping[str, Any]) -> str:
    kind = schema.get("type")
    return TYPES.get(kind, "string") if isinstance(kind, str) else "string"


def definition(tool: Mapping[str, Any], *, xp: int = 0, requires_guard: bool = False) -> dict[str, Any]:
    """A ``SkillDefinition`` from an MCP tool definition (name, description, inputSchema)."""
    schema = tool.get("inputSchema", {})
    required = set(schema.get("required", []))
    args: list[dict[str, Any]] = []
    for name, spec in schema.get("properties", {}).items():
        arg: dict[str, Any] = {
            "name": name,
            "type": arg_type(spec),
            "required": name in required,
            "description": spec.get("description", ""),
            "enum": spec.get("enum"),
            "items": spec.get("items") if arg_type(spec) == "array" and isinstance(spec.get("items"), dict) else None,
        }
        args.append(arg)
    return {"name": tool["name"], "description": tool["description"], "args": args, "requires_guard": requires_guard, "xp": xp}


def listing(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [definition(t) for t in tools]


def _invalid(loc: list[str | int], message: str) -> tuple[int, dict[str, Any]]:
    return 422, {"detail": [{"loc": loc, "msg": message, "type": "value_error"}]}


def invoke(
    path_name: str,
    body: object,
    runners: Mapping[str, Callable[..., dict[str, Any]]],
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any]]:
    """``POST /api/skills/{name}/invoke``: the HTTP status and the ``SkillCallResponse`` (or a 422 body for a malformed request)."""
    if path_name not in runners:
        return _invalid(["path", "name"], f"unknown skill '{path_name}'")
    if not isinstance(body, dict):
        return _invalid(["body"], "the body must be an object")
    if body.get("name") != path_name:
        return _invalid(["body", "name"], "must equal the name in the path")
    args = body.get("args")
    if not isinstance(args, dict):
        return _invalid(["body", "args"], "must be an object")
    for key in ("conversation_id", "message_id"):
        if body.get(key) is not None and not isinstance(body[key], str):
            return _invalid(["body", key], "must be a string or null")
    started = clock()
    try:
        result = runners[path_name](args)
        status = "success"
    except InputError as exc:
        result, status = exc.to_envelope(uuid.uuid4().hex), "error"
    except Exception as exc:  # the class name only
        result, status = {"code": "INTERNAL_ERROR", "message": type(exc).__name__, "details": None, "trace_id": uuid.uuid4().hex}, "error"
    return 200, {
        "name": path_name,
        "status": status,
        "result": result,
        "latency_ms": int((clock() - started) * 1000),
        "xp": 0,
        "guard_decision": None,
    }
