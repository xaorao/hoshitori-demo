"""Checks of ``evidence_delta.schema``, runnable against a mutant: the constants, the published description text and the exact shape of
every schema and tool definition."""

from __future__ import annotations

import types
from typing import Any

from tests.checks_support import Case

DESCRIPTION = (
    "Delivery: for two reads of the same tool (RYO envelopes, or just their data), every field that changed, with both values and the size "
    "of each numeric move, every field that could not be compared and why, both as_of times, and one verdict: changed, unchanged, "
    "unchanged_where_known or insufficient_evidence. Use when: an agent or person asks what is different since the last read and must not "
    "mistake a missing value for no change. Core inputs: before, after; optional fields, min_change_pct. Limits: compares what it is given, "
    "reads nothing from the network, does not say why a field changed or whether the change matters; depth 12, at most 2000 fields per read."
)
RULES_DESCRIPTION = (
    "Delivery: the rules evidence_delta applies, in words: what counts as changed, unchanged and unknown, and the limits of a call. "
    "Use when: an agent needs to know what an unchanged verdict covers. Core inputs: none. Limits: describes the rules only; compares nothing."
)
READ = "One read: an envelope with a data member (tool, as_of, status, data_mode and data are used), or a bare data object."
INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "before": {"type": "object", "description": "The earlier read. " + READ},
        "after": {"type": "object", "description": "The later read. " + READ},
        "fields": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "minItems": 1,
            "maxItems": 200,
            "uniqueItems": True,
            "description": "Dotted paths to compare (a.b.0.c; a list index is a number). Default: every field of either read.",
        },
        "min_change_pct": {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
            "default": 0,
            "description": "A number whose relative move is below this many percent counts as unchanged. Default 0: any move is a change.",
        },
    },
    "required": ["before", "after"],
    "additionalProperties": False,
}


def envelope(tool: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "1.0"},
            "tool": {"const": tool},
            "status": {"enum": ["ok", "partial", "unavailable"]},
            "data_mode": {"enum": ["live", "mixed", "simulated", "unknown"]},
            "as_of": {"type": "string"},
            "request": {"type": "object"},
            "data": {"type": "object"},
            "summary": {
                "type": "object",
                "required": ["headline", "key_points"],
                "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
            },
            "availability": {"type": "object", "additionalProperties": {"enum": ["available", "unavailable", "not_requested"]}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "names",
        (mod.TOOL_NAME, mod.RULES_TOOL_NAME, mod.SOURCES_TOOL_NAME, mod.SCHEMA_VERSION),
        ("evidence_delta", "evidence_delta_rules", "evidence_delta_rules", "1.0"),
    )
    c.eq("limits", (mod.MAX_DEPTH, mod.MAX_LEAVES, mod.MAX_CHANGES_SHOWN), (12, 2000, 200))
    c.eq(
        "example call",
        mod.EXAMPLE_CALL,
        {
            "before": {
                "tool": "analyze_token",
                "as_of": "2026-11-09T10:00:00Z",
                "data": {"price_usd": 100.0, "rsi_14": 55.2, "verdict": "hold"},
            },
            "after": {
                "tool": "analyze_token",
                "as_of": "2026-11-09T11:00:00Z",
                "data": {"price_usd": 104.0, "rsi_14": None, "verdict": "hold"},
            },
        },
    )
    c.eq("description", mod.DESCRIPTION, DESCRIPTION)
    c.eq("rules description", mod.RULES_DESCRIPTION, RULES_DESCRIPTION)
    return c.bad


def check_schemas(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("input schema", mod.INPUT_SCHEMA, INPUT)
    c.eq("rules input schema", mod.RULES_INPUT_SCHEMA, {"type": "object", "properties": {}, "additionalProperties": False})
    c.eq("output schema", mod.OUTPUT_SCHEMA, envelope("evidence_delta"))
    c.eq("rules output schema", mod.RULES_OUTPUT_SCHEMA, envelope("evidence_delta_rules"))
    c.eq("the envelope builder", mod._envelope_schema("x"), envelope("x"))
    c.eq("the keys", mod._KEYS, tuple(envelope("x")["required"]))
    annotations = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    c.eq(
        "tool definition",
        mod.TOOL_DEFINITION,
        {
            "name": "evidence_delta",
            "description": DESCRIPTION,
            "inputSchema": INPUT,
            "outputSchema": envelope("evidence_delta"),
            "annotations": annotations,
        },
    )
    c.eq(
        "rules tool definition",
        mod.RULES_TOOL_DEFINITION,
        {
            "name": "evidence_delta_rules",
            "description": RULES_DESCRIPTION,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": envelope("evidence_delta_rules"),
            "annotations": annotations,
        },
    )
    c.eq("definitions in order", [d["name"] for d in mod.TOOL_DEFINITIONS], ["evidence_delta", "evidence_delta_rules"])
    c.eq(
        "definitions are the two",
        (mod.TOOL_DEFINITIONS[0] is mod.TOOL_DEFINITION, mod.TOOL_DEFINITIONS[1] is mod.RULES_TOOL_DEFINITION),
        (True, True),
    )
    c.eq(
        "annotations are copies",
        (mod.TOOL_DEFINITION["annotations"] is mod.RULES_TOOL_DEFINITION["annotations"], annotations == mod._ANNOTATIONS),
        (False, True),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_constants, check_schemas):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
