"""The tool definitions (MCP form, also served at ``GET /tools``), the output envelope schemas and the limits of a call.

The description follows the four-part template of RYO's catalogue entries: Delivery, Use when, Core inputs, Limits.
"""

from __future__ import annotations

from typing import Any, Final

TOOL_NAME: Final = "evidence_delta"
RULES_TOOL_NAME: Final = "evidence_delta_rules"
SOURCES_TOOL_NAME: Final = RULES_TOOL_NAME  # the server's second tool: the rules of the comparison
SCHEMA_VERSION: Final = "1.0"
EXAMPLE_CALL: Final[dict[str, Any]] = {
    "before": {"tool": "analyze_token", "as_of": "2026-11-09T10:00:00Z", "data": {"price_usd": 100.0, "rsi_14": 55.2, "verdict": "hold"}},
    "after": {"tool": "analyze_token", "as_of": "2026-11-09T11:00:00Z", "data": {"price_usd": 104.0, "rsi_14": None, "verdict": "hold"}},
}
MAX_DEPTH: Final = 12
MAX_LEAVES: Final = 2000
MAX_CHANGES_SHOWN: Final = 200

DESCRIPTION = (
    "Delivery: for two reads of the same tool (RYO envelopes, or just their data), every field that changed, with both values and "
    "the size of each numeric move, every field that could not be compared and why, both as_of times, and one verdict: changed, unchanged, "
    "unchanged_where_known or insufficient_evidence. Use when: an agent or person asks what is different since the last read and must not "
    "mistake a missing value for no change. Core inputs: before, after; optional fields, min_change_pct. Limits: compares what it is "
    f"given, reads nothing from the network, does not say why a field changed or whether the change matters; depth {MAX_DEPTH}, "
    f"at most {MAX_LEAVES} fields per read."
)
RULES_DESCRIPTION = (
    "Delivery: the rules evidence_delta applies, in words: what counts as changed, unchanged and unknown, and the limits of a call. "
    "Use when: "
    "an agent needs to know what an unchanged verdict covers. Core inputs: none. Limits: describes the rules only; compares nothing."
)

_READ: dict[str, Any] = {
    "type": "object",
    "description": "One read: an envelope with a data member (tool, as_of, status, data_mode and data are used), or a bare data object.",
}
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "before": {**_READ, "description": "The earlier read. " + _READ["description"]},
        "after": {**_READ, "description": "The later read. " + _READ["description"]},
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
RULES_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_KEYS = ("schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings")


def _envelope_schema(tool: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(_KEYS),
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
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


OUTPUT_SCHEMA: dict[str, Any] = _envelope_schema(TOOL_NAME)
RULES_OUTPUT_SCHEMA: dict[str, Any] = _envelope_schema(RULES_TOOL_NAME)
_ANNOTATIONS: Final[dict[str, bool]] = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": dict(_ANNOTATIONS),
}
RULES_TOOL_DEFINITION: dict[str, Any] = {
    "name": RULES_TOOL_NAME,
    "description": RULES_DESCRIPTION,
    "inputSchema": RULES_INPUT_SCHEMA,
    "outputSchema": RULES_OUTPUT_SCHEMA,
    "annotations": dict(_ANNOTATIONS),
}
TOOL_DEFINITIONS: list[dict[str, Any]] = [TOOL_DEFINITION, RULES_TOOL_DEFINITION]
