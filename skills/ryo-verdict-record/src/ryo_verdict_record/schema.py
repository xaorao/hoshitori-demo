"""The tool definitions (MCP form, also served at ``GET /tools``), the output envelope schemas and the limits of a call.

The description follows the four-part template of RYO's catalogue entries: Delivery, Use when, Core inputs, Limits.
"""

from __future__ import annotations

from typing import Any, Final

TOOL_NAME: Final = "ryo_verdict_record"
RULES_TOOL_NAME: Final = "ryo_verdict_record_rules"
SOURCES_TOOL_NAME: Final = RULES_TOOL_NAME  # the server's second tool: the rules of the reading
SCHEMA_VERSION: Final = "1.0"
READINGS: Final = ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD")
SCORED_READINGS: Final = ("UP", "DOWN", "NOT_LONG")
DIRECTIONS: Final = ("ROSE", "FELL", "TIE")
PLANS: Final = ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS", "ALREADY_PAST", "NOT_SETTLED", "WAITING", "NO_PLAN")
MAX_OUTCOMES: Final = 5000
DEFAULT_MIN_SCORED: Final = 10
EXAMPLE_CALL: Final[dict[str, Any]] = {
    "record": {
        "outcomes": [
            {"symbol": "SOL", "reading": "UP", "state": "SETTLED", "direction": "ROSE", "hit": True, "plan": "TARGET"},
            {"symbol": "SOL", "reading": "NOT_LONG", "state": "SETTLED", "direction": "ROSE", "hit": False, "plan": "STOP"},
            {"symbol": "ETH", "reading": "UP", "state": "OPEN", "direction": None, "hit": None, "plan": "WAITING"},
        ]
    },
    "min_scored": 2,
}

DESCRIPTION = (
    "Delivery: for the outcomes of RYO's own verdicts in a published record (each verdict word read as up, down or against buying, and "
    "how the 24 hours after it settled), how many verdicts were read, how many could be scored and why the rest could not, the hit rate of "
    "each reading with an interval and against the rate of the market itself, and which of RYO's own plan levels came first. "
    "Use when: an agent or person asks whether RYO's verdicts have been right, and must not count an unscored read as a miss. "
    "Core inputs: record (the outcomes), optional symbol, min_scored. "
    f"Limits: reads only what it is given and sends nothing; at most {MAX_OUTCOMES} outcomes; the hit rate describes the settled "
    "windows in the record and is not a forecast; no verdict is given before min_scored scored reads."
)
RULES_DESCRIPTION = (
    "Delivery: the rules ryo_verdict_record applies, in words: which reading is right for which direction, what is scored, what the "
    "comparison is made against and when it says not distinguishable. Use when: an agent needs to know what a hit rate covers. "
    "Core inputs: none. Limits: describes the rules only; scores nothing."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "record": {
            "type": "object",
            "description": "The record: an object with an outcomes list (as a Hoshitori site's ryo.json holds), each outcome with symbol, "
            "reading, state, direction, hit and plan; or a bare list of such outcomes.",
        },
        "symbol": {"type": "string", "maxLength": 20, "description": "Read only the verdicts on this token (capital letters and digits)."},
        "min_scored": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": DEFAULT_MIN_SCORED,
            "description": "The scored reads a reading needs before a comparison with the market is made. Default 10.",
        },
    },
    "required": ["record"],
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
