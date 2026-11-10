"""Checks of ``ryo_verdict_record.schema``, runnable against a mutant: every published constant, description, input schema, output envelope
schema and tool definition against its written-out value, the envelope builder, and that the definitions do not share mutable parts."""

from __future__ import annotations

import types
from typing import Any

from tests.schema_expected import EXPECTED

ENVELOPE_KEYS = ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"]


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for name, want in EXPECTED.items():
        got = getattr(mod, name, None)
        if got != want or type(got) is not type(want):
            out.append(f"{name} differs from its published value")
        elif isinstance(want, dict) and repr(got) != repr(want):
            out.append(f"{name} has its members in another order")
    # the envelope builder, on a tool name nobody uses
    built = mod._envelope_schema("other_tool")
    want_built: dict[str, Any] = {
        **EXPECTED["OUTPUT_SCHEMA"],
        "properties": {**EXPECTED["OUTPUT_SCHEMA"]["properties"], "tool": {"const": "other_tool"}},
    }
    if built != want_built or repr(built) != repr(want_built):
        out.append("_envelope_schema does not build the envelope around the tool name it is given")
    if list(built["required"]) != ENVELOPE_KEYS or list(built["properties"]) != ENVELOPE_KEYS:
        out.append("the envelope's keys are not the ten of the house envelope")
    if tuple(ENVELOPE_KEYS) != mod._KEYS:
        out.append("_KEYS differs")
    # built fresh each time: no two schemas share a part that one caller could change under the other
    if (
        mod.OUTPUT_SCHEMA["properties"] is mod.RULES_OUTPUT_SCHEMA["properties"]
        or mod._envelope_schema("x")["required"] is mod._envelope_schema("x")["required"]
    ):
        out.append("two envelope schemas share their members")
    first, second = mod.TOOL_DEFINITION, mod.RULES_TOOL_DEFINITION
    if first["annotations"] is second["annotations"] or first["annotations"] is mod._ANNOTATIONS:
        out.append("tool definitions share one annotations dictionary")
    if mod._ANNOTATIONS != {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}:
        out.append("the annotations differ")
    if [first, second] != mod.TOOL_DEFINITIONS or mod.TOOL_DEFINITIONS[0] is not first or mod.TOOL_DEFINITIONS[1] is not second:
        out.append("TOOL_DEFINITIONS is not the two definitions in order")
    if first["inputSchema"] is not mod.INPUT_SCHEMA or first["outputSchema"] is not mod.OUTPUT_SCHEMA:
        out.append("the first definition does not carry the module's schemas")
    if second["inputSchema"] is not mod.RULES_INPUT_SCHEMA or second["outputSchema"] is not mod.RULES_OUTPUT_SCHEMA:
        out.append("the second definition does not carry the module's schemas")
    if mod.SOURCES_TOOL_NAME != mod.RULES_TOOL_NAME:
        out.append("the sources name is not the rules name")
    if not (
        mod.DESCRIPTION.count("Delivery:")
        == mod.DESCRIPTION.count("Use when:")
        == mod.DESCRIPTION.count("Core inputs:")
        == mod.DESCRIPTION.count("Limits:")
        == 1
    ):
        out.append("the description does not have the four parts once each")
    return out
