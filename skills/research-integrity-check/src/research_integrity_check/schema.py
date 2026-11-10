"""The tool definition (MCP form, also served at ``GET /tools``), input validation and the output envelope schema."""

from __future__ import annotations

import unicodedata
from typing import Any

from .errors import InputError

TOOL_NAME = "research_integrity_check"
MAX_CHARS = 2000
EXAMPLE_CALL: dict[str, Any] = {
    "claim": "Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."
}

DESCRIPTION = (
    "Delivery: for one market claim, what it is made of: its clauses with the exact words of each, the causal links, conditions, "
    "forecasts, numbers with their units, assets and time words it contains, and the structural problems that can be seen from the "
    "words alone (a forecast with no horizon or no level, a number with no unit, a link that says one thing causes another). Use when: "
    "an agent or a person meets a claim and needs to know what would have to be checked before it could be believed. Core inputs: claim "
    "(text, at most 2000 characters). Limits: reads structure only; nothing is checked against any source, so no part is called true or "
    "false and no stance, probability or call is given; digits are read, numbers written in words are only flagged; no network, no model."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "minLength": 1, "maxLength": MAX_CHARS, "description": "The claim as written, in one piece."}
    },
    "required": ["claim"],
    "additionalProperties": False,
}
_SUMMARY = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
    "required": ["headline", "key_points"],
}
_ENVELOPE_FIELDS = ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"]
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "tool": {"const": TOOL_NAME},
        "status": {"enum": ["ok", "partial", "unavailable"]},
        "data_mode": {"enum": ["live", "mixed", "simulated", "unknown"]},
        "as_of": {"type": "string"},
        "request": {"type": "object"},
        "data": {
            "type": "object",
            "properties": {
                "triage": {"enum": ["INVESTIGATE", "NOT_MARKET_TESTABLE"]},
                "clauses": {"type": "array", "items": {"type": "object"}},
                "marks": {"type": "array", "items": {"type": "object"}},
                "findings": {"type": "array", "items": {"type": "object"}},
                "assets": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["triage", "clauses", "marks", "findings", "assets"],
        },
        "summary": _SUMMARY,
        "availability": {"type": "object", "additionalProperties": {"enum": ["available", "partial", "unavailable"]}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": _ENVELOPE_FIELDS,
    "additionalProperties": False,
}
TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "title": "Research integrity check",
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False, "destructiveHint": False},
}
TOOL_DEFINITIONS = [TOOL_DEFINITION]


def validate_input(args: object) -> str:
    """The claim, normalised (NFC, trimmed). Raises InputError naming the field, with a working example."""
    if not isinstance(args, dict):
        raise InputError("arguments", "must be an object", EXAMPLE_CALL)
    extra = set(args) - {"claim"}
    if extra:
        raise InputError("arguments", f"unknown fields {sorted(extra)}; allowed ['claim']", EXAMPLE_CALL)
    claim = args.get("claim")
    if not isinstance(claim, str):
        raise InputError("claim", "is required and must be text", EXAMPLE_CALL)
    text = unicodedata.normalize("NFC", claim).strip()
    if not text:
        raise InputError("claim", "is empty", EXAMPLE_CALL)
    if len(text) > MAX_CHARS:
        raise InputError("claim", f"is longer than {MAX_CHARS} characters; send the one claim to audit", EXAMPLE_CALL)
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text):
        raise InputError("claim", "holds control characters", EXAMPLE_CALL)
    return text
