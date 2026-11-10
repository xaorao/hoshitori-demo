"""The values of ``schema.py`` as published, written out so that a change to any word, limit or member of them is seen. Read against the
brief (four-part descriptions: Delivery, Use when, Core inputs, Limits) and the output envelope when written."""

from __future__ import annotations

from typing import Any

EXPECTED: dict[str, Any] = {
    "TOOL_NAME": "ryo_verdict_record",
    "RULES_TOOL_NAME": "ryo_verdict_record_rules",
    "SOURCES_TOOL_NAME": "ryo_verdict_record_rules",
    "SCHEMA_VERSION": "1.0",
    "READINGS": ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD"),
    "SCORED_READINGS": ("UP", "DOWN", "NOT_LONG"),
    "DIRECTIONS": ("ROSE", "FELL", "TIE"),
    "PLANS": ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS", "ALREADY_PAST", "NOT_SETTLED", "WAITING", "NO_PLAN"),
    "MAX_OUTCOMES": 5000,
    "DEFAULT_MIN_SCORED": 10,
    "EXAMPLE_CALL": {
        "record": {
            "outcomes": [
                {"symbol": "SOL", "reading": "UP", "state": "SETTLED", "direction": "ROSE", "hit": True, "plan": "TARGET"},
                {"symbol": "SOL", "reading": "NOT_LONG", "state": "SETTLED", "direction": "ROSE", "hit": False, "plan": "STOP"},
                {"symbol": "ETH", "reading": "UP", "state": "OPEN", "direction": None, "hit": None, "plan": "WAITING"},
            ]
        },
        "min_scored": 2,
    },
    "DESCRIPTION": (
        "Delivery: for the outcomes of RYO's own verdicts in a published record (each verdict word read as up, down or against buying, "
        "and how the 24 hours after it settled), how many verdicts were read, how many could be scored and why the rest could not, the "
        "hit rate of each reading with an interval and against the rate of the market itself, and which of RYO's own plan levels came "
        "first. Use when: an agent or person asks whether RYO's verdicts have been right, and must not count an unscored read as a "
        "miss. Core inputs: record (the outcomes), optional symbol, min_scored. Limits: reads only what it is given and sends nothing; "
        "at most 5000 outcomes; the hit rate describes the settled windows in the record and is not a forecast; no verdict is given "
        "before min_scored scored reads."
    ),
    "RULES_DESCRIPTION": (
        "Delivery: the rules ryo_verdict_record applies, in words: which reading is right for which direction, what is scored, what the "
        "comparison is made against and when it says not distinguishable. Use when: an agent needs to know what a hit rate covers. Core "
        "inputs: none. Limits: describes the rules only; scores nothing."
    ),
    "INPUT_SCHEMA": {
        "type": "object",
        "properties": {
            "record": {
                "type": "object",
                "description": "The record: an object with an outcomes list (as a Hoshitori site's ryo.json holds), "
                "each outcome with symbol, reading, state, direction, hit and plan; or a bare list of "
                "such outcomes.",
            },
            "symbol": {
                "type": "string",
                "maxLength": 20,
                "description": "Read only the verdicts on this token (capital letters and digits).",
            },
            "min_scored": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 10,
                "description": "The scored reads a reading needs before a comparison with the market is made. Default 10.",
            },
        },
        "required": ["record"],
        "additionalProperties": False,
    },
    "RULES_INPUT_SCHEMA": {"type": "object", "properties": {}, "additionalProperties": False},
    "OUTPUT_SCHEMA": {
        "type": "object",
        "required": ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "1.0"},
            "tool": {"const": "ryo_verdict_record"},
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
    },
    "RULES_OUTPUT_SCHEMA": {
        "type": "object",
        "required": ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "1.0"},
            "tool": {"const": "ryo_verdict_record_rules"},
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
    },
    "TOOL_DEFINITION": {
        "name": "ryo_verdict_record",
        "description": "Delivery: for the outcomes of RYO's own verdicts in a published record (each verdict word read as up, down or "
        "against buying, and how the 24 hours after it settled), how many verdicts were read, how many could be scored "
        "and why the rest could not, the hit rate of each reading with an interval and against the rate of the market "
        "itself, and which of RYO's own plan levels came first. Use when: an agent or person asks whether RYO's verdicts "
        "have been right, and must not count an unscored read as a miss. Core inputs: record (the outcomes), optional "
        "symbol, min_scored. Limits: reads only what it is given and sends nothing; at most 5000 outcomes; the hit rate "
        "describes the settled windows in the record and is not a forecast; no verdict is given before min_scored scored "
        "reads.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "The record: an object with an outcomes list (as a Hoshitori site's "
                    "ryo.json holds), each outcome with symbol, reading, state, direction, "
                    "hit and plan; or a bare list of such outcomes.",
                },
                "symbol": {
                    "type": "string",
                    "maxLength": 20,
                    "description": "Read only the verdicts on this token (capital letters and digits).",
                },
                "min_scored": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 10,
                    "description": "The scored reads a reading needs before a comparison with the market is made. Default 10.",
                },
            },
            "required": ["record"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "schema_version",
                "tool",
                "status",
                "data_mode",
                "as_of",
                "request",
                "data",
                "summary",
                "availability",
                "warnings",
            ],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": "1.0"},
                "tool": {"const": "ryo_verdict_record"},
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
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    "RULES_TOOL_DEFINITION": {
        "name": "ryo_verdict_record_rules",
        "description": "Delivery: the rules ryo_verdict_record applies, in words: which reading is right for which direction, what is "
        "scored, what the comparison is made against and when it says not distinguishable. Use when: an agent needs to "
        "know what a hit rate covers. Core inputs: none. Limits: describes the rules only; scores nothing.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "required": [
                "schema_version",
                "tool",
                "status",
                "data_mode",
                "as_of",
                "request",
                "data",
                "summary",
                "availability",
                "warnings",
            ],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": "1.0"},
                "tool": {"const": "ryo_verdict_record_rules"},
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
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    "TOOL_DEFINITIONS": [
        {
            "name": "ryo_verdict_record",
            "description": "Delivery: for the outcomes of RYO's own verdicts in a published record (each verdict word read as up, down or "
            "against buying, and how the 24 hours after it settled), how many verdicts were read, how many could be scored "
            "and why the rest could not, the hit rate of each reading with an interval and against the rate of the market "
            "itself, and which of RYO's own plan levels came first. Use when: an agent or person asks whether RYO's "
            "verdicts have been right, and must not count an unscored read as a miss. Core inputs: record (the outcomes), "
            "optional symbol, min_scored. Limits: reads only what it is given and sends nothing; at most 5000 outcomes; the "
            "hit rate describes the settled windows in the record and is not a forecast; no verdict is given before "
            "min_scored scored reads.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "record": {
                        "type": "object",
                        "description": "The record: an object with an outcomes list (as a Hoshitori site's "
                        "ryo.json holds), each outcome with symbol, reading, state, "
                        "direction, hit and plan; or a bare list of such outcomes.",
                    },
                    "symbol": {
                        "type": "string",
                        "maxLength": 20,
                        "description": "Read only the verdicts on this token (capital letters and digits).",
                    },
                    "min_scored": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 10,
                        "description": "The scored reads a reading needs before a comparison with the market is made. Default 10.",
                    },
                },
                "required": ["record"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "required": [
                    "schema_version",
                    "tool",
                    "status",
                    "data_mode",
                    "as_of",
                    "request",
                    "data",
                    "summary",
                    "availability",
                    "warnings",
                ],
                "additionalProperties": False,
                "properties": {
                    "schema_version": {"const": "1.0"},
                    "tool": {"const": "ryo_verdict_record"},
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
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "ryo_verdict_record_rules",
            "description": "Delivery: the rules ryo_verdict_record applies, in words: which reading is right for which direction, what is "
            "scored, what the comparison is made against and when it says not distinguishable. Use when: an agent needs to "
            "know what a hit rate covers. Core inputs: none. Limits: describes the rules only; scores nothing.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "required": [
                    "schema_version",
                    "tool",
                    "status",
                    "data_mode",
                    "as_of",
                    "request",
                    "data",
                    "summary",
                    "availability",
                    "warnings",
                ],
                "additionalProperties": False,
                "properties": {
                    "schema_version": {"const": "1.0"},
                    "tool": {"const": "ryo_verdict_record_rules"},
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
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
    ],
}
