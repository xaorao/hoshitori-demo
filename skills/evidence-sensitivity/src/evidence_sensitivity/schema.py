"""The tool definition (MCP form, also served at ``GET /tools``), input validation and the output envelope schema.

Every refusal names its field and carries a working example; it never repeats the input. The house style of RYO's own catalogue is
followed: a four-part description, ``additionalProperties: false``, explicit ``required`` and defaults.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any

from .errors import InputError

TOOL_NAME = "evidence_sensitivity"
MAX_ITEMS = 30
ID = re.compile(r"[A-Za-z0-9_.:-]{1,40}")
EXAMPLE_CALL: dict[str, Any] = {
    "evidence": [
        {"id": "momentum_7d", "signal": 0.6, "weight": 2, "group": "market"},
        {"id": "funding", "signal": -0.2, "weight": 1, "group": "derivatives"},
        {"id": "news_tone", "signal": 0.5, "weight": 1, "group": "news"},
    ],
    "rule": {"threshold": 0.25},
}

DESCRIPTION = (
    "Delivery: for a rule-based call over weighted evidence, which items the call rests on: what the call becomes with each item "
    "withheld, neutralised or its whole source group removed, how far each signal would have to move to change the call, and the "
    "smallest set of items that alone gives the same call. Use when: an agent or reviewer wants to know whether a decision survives "
    "the loss or error of one source. Core inputs: evidence (id, signal from -1 to 1 or null, weight, optional group), rule.threshold. "
    "Limits: measures how this rule responds to these inputs; says nothing about whether the evidence is true, why a model decided, "
    "or what the market will do; computed from the evidence supplied, nothing is fetched."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": ID.pattern,
                        "description": "A distinct name, 1 to 40 of letters, digits and _ . : -",
                    },
                    "signal": {
                        "type": ["number", "null"],
                        "minimum": -1,
                        "maximum": 1,
                        "description": "-1 fully against, 1 fully for. null: unavailable; it counts for nothing and is listed.",
                    },
                    "weight": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1000,
                        "default": 1,
                        "description": "How much the rule counts it.",
                    },
                    "group": {"type": "string", "pattern": ID.pattern, "description": "Where it came from (one origin family); optional."},
                },
                "required": ["id", "signal"],
                "additionalProperties": False,
            },
            "description": "The evidence the call was made from.",
        },
        "rule": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1,
                    "description": "LONG when the weighted mean signal is at least this, SHORT when at most minus this, otherwise NO_CALL.",
                },
                "min_evidence": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_ITEMS,
                    "default": 1,
                    "description": "Fewest available items that may decide.",
                },
            },
            "required": ["threshold"],
            "additionalProperties": False,
        },
    },
    "required": ["evidence", "rule"],
    "additionalProperties": False,
}

_AVAILABILITY = {"type": "object", "additionalProperties": {"enum": ["available", "partial", "unavailable"]}}
_SUMMARY = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
    "required": ["headline", "key_points"],
}
_ENVELOPE_FIELDS = ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"]
_CALL = {"type": "object", "required": ["score", "action", "available"]}
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
                "baseline": _CALL,
                "items": {"type": "array", "items": {"type": "object"}},
                "groups": {"type": "array", "items": {"type": "object"}},
                "required_items": {"type": "array", "items": {"type": "string"}},
                "sufficient_set": {"type": ["object", "null"]},
                "missing": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["baseline", "items", "groups", "required_items", "sufficient_set", "missing"],
        },
        "summary": _SUMMARY,
        "availability": _AVAILABILITY,
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": _ENVELOPE_FIELDS,
    "additionalProperties": False,
}
TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "title": "Evidence sensitivity",
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False, "destructiveHint": False},
}
TOOL_DEFINITIONS = [TOOL_DEFINITION]


def _number(value: object, field: str, low: Fraction | None, high: Fraction | None, *, low_open: bool = False) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, int | float) or (isinstance(value, float) and not math.isfinite(value)):
        raise InputError(field, "must be a finite number", EXAMPLE_CALL)
    n = Fraction(str(value))
    if (low is not None and (n <= low if low_open else n < low)) or (high is not None and n > high):
        raise InputError(field, f"is out of range ({'above' if low_open else 'from'} {low} to {high})", EXAMPLE_CALL)
    return n


def validate_input(args: object) -> dict[str, Any]:
    """The normalised request: items as (id, signal or None, weight, group or None) as exact fractions, and the rule."""
    if not isinstance(args, dict):
        raise InputError("arguments", "must be an object", EXAMPLE_CALL)
    extra = set(args) - set(INPUT_SCHEMA["properties"])
    if extra:
        raise InputError("arguments", f"unknown fields {sorted(extra)}; allowed {sorted(INPUT_SCHEMA['properties'])}", EXAMPLE_CALL)
    for name in ("evidence", "rule"):
        if name not in args:
            raise InputError(name, "is required", EXAMPLE_CALL)
    raw = args["evidence"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
        raise InputError("evidence", f"must be a list of 1 to {MAX_ITEMS} items", EXAMPLE_CALL)
    items: list[tuple[str, Fraction | None, Fraction, str | None]] = []
    seen: set[str] = set()
    for n, entry in enumerate(raw):
        where = f"evidence[{n}]"
        if not isinstance(entry, dict) or set(entry) - {"id", "signal", "weight", "group"} or not {"id", "signal"} <= set(entry):
            raise InputError(where, "must be an object with id, signal and optionally weight and group", EXAMPLE_CALL)
        ident = entry["id"]
        if not isinstance(ident, str) or ID.fullmatch(ident) is None:
            raise InputError(f"{where}.id", "must be 1 to 40 of letters, digits and _ . : -", EXAMPLE_CALL)
        if ident in seen:
            raise InputError(f"{where}.id", "is used twice; ids are distinct", EXAMPLE_CALL)
        seen.add(ident)
        signal = None if entry["signal"] is None else _number(entry["signal"], f"{where}.signal", Fraction(-1), Fraction(1))
        weight = _number(entry.get("weight", 1), f"{where}.weight", Fraction(0), Fraction(1000))
        group = entry.get("group")
        if group is not None and (not isinstance(group, str) or ID.fullmatch(group) is None):
            raise InputError(f"{where}.group", "must be 1 to 40 of letters, digits and _ . : -", EXAMPLE_CALL)
        items.append((ident, signal, weight, group))
    rule = args["rule"]
    if not isinstance(rule, dict) or set(rule) - {"threshold", "min_evidence"} or "threshold" not in rule:
        raise InputError("rule", "must be an object with threshold and optionally min_evidence", EXAMPLE_CALL)
    threshold = _number(rule["threshold"], "rule.threshold", Fraction(0), Fraction(1), low_open=True)
    minimum = rule.get("min_evidence", 1)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= MAX_ITEMS:
        raise InputError("rule.min_evidence", f"must be a whole number from 1 to {MAX_ITEMS}", EXAMPLE_CALL)
    return {"items": items, "threshold": threshold, "min_evidence": minimum, "defaults": {"min_evidence": "min_evidence" not in rule}}
