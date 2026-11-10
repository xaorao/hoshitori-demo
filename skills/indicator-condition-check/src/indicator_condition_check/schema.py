"""Tool definition, input schema, input validation and the output envelope schema."""
from __future__ import annotations

import json
import re
from typing import Any

from .condition import (
    EXAMPLE_CONDITION,
    INDICATOR_NAMES,
    MAX_CLAUSES,
    MAX_DEPTH,
    OPS,
    parse_condition,
    required_bars,
)
from .errors import InputError

TOOL_NAME = "indicator_condition_check"
TIMEFRAMES = ("1h", "4h", "1d")
VENUES = ("auto", "binance", "coinbase", "kraken", "bybit")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9]{2,12}$")

DESCRIPTION = (
    "Delivery: whether a user-defined technical condition is true on the latest closed candle, with every "
    "clause's measured value, how many bars ago it last held, and how often it held in the lookback window. "
    "Use when: an agent or user wants to test a rule such as RSI(14) < 30 AND close crossing above EMA(50), "
    "or to turn a trade idea into a monitorable trigger or invalidation level. "
    "Core inputs: symbol, condition; optional timeframe (1h, 4h, 1d), lookback_bars, venue, cross_venue_check. "
    "Limits: closed candles only (the forming candle is excluded and reported); spot USD/USDT pairs on "
    "Binance, Coinbase, Kraken or Bybit; research context, not a trade signal; insufficient history yields "
    "null, never false."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "pattern": "^[A-Za-z0-9]{2,12}$", "description": "Token symbol such as SOL or BTC. Symbols only, never addresses."},
        "timeframe": {"type": "string", "enum": list(TIMEFRAMES), "default": "1d", "description": "Candle size. Coinbase has no 4h candles, so 4h uses another venue."},
        "condition": {
            "type": "object",
            "description": (
                "Expression tree. Group: {all:[...]} | {any:[...]} | {not:{...}}. Clause: {left, op, right} with op in "
                f"{list(OPS)}. Operand: {{indicator, ...params}} or {{value: number}}. Indicators: {list(INDICATOR_NAMES)}. "
                f"At most {MAX_CLAUSES} clauses and {MAX_DEPTH} levels."
            ),
            "examples": [EXAMPLE_CONDITION],
        },
        "lookback_bars": {"type": "integer", "minimum": 50, "maximum": 1000, "default": 300, "description": "Number of closed candles evaluated."},
        "venue": {"type": "string", "enum": list(VENUES), "default": "auto", "description": "auto tries Binance, Coinbase, Kraken, Bybit in order."},
        "cross_venue_check": {"type": "boolean", "default": False, "description": "Also compare the last closed price with a second venue."},
    },
    "required": ["symbol", "condition"],
    "additionalProperties": False,
}

_STATUS = ["ok", "partial", "unavailable"]
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "tool": {"const": TOOL_NAME},
        "status": {"enum": _STATUS},
        "data_mode": {"enum": ["live", "mixed", "simulated", "unknown"]},
        "as_of": {"type": "string"},
        "request": {"type": "object"},
        "data": {
            "type": "object",
            "properties": {
                "pair": {
                    "type": "object",
                    "properties": {
                        "resolved": {"type": ["string", "null"]},
                        "venue_used": {"type": ["string", "null"]},
                        "alternatives_tried": {"type": "array", "items": {"type": "object", "required": ["venue", "outcome"]}},
                    },
                    "required": ["resolved", "venue_used", "alternatives_tried"],
                },
                "candles": {
                    "type": ["object", "null"],
                    "properties": {
                        "timeframe": {"enum": list(TIMEFRAMES)},
                        "count": {"type": "integer", "minimum": 1},
                        "last_closed_ts": {"type": "string"},
                        "forming_bar_excluded": {"type": "boolean"},
                        "gaps": {"type": "array"},
                        "gap_count": {"type": "integer", "minimum": 0},
                    },
                },
                "result": {
                    "type": ["object", "null"],
                    "properties": {
                        "satisfied": {"type": ["boolean", "null"]},
                        "clauses": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}, "expr": {"type": "string"}, "satisfied": {"type": ["boolean", "null"]}, "null_reason": {"type": ["string", "null"]}},
                                "required": ["id", "expr", "left", "right", "satisfied", "null_reason", "bars_since_last_true", "true_count", "defined_bars"],
                            },
                        },
                    },
                    "required": ["satisfied", "clauses"],
                },
                "history": {"type": ["object", "null"]},
                "warmup": {"type": ["object", "null"]},
                "methods": {"type": "object"},
                "source_calls": {"type": "array", "items": {"type": "object", "required": ["venue", "url", "attempt", "http_status", "latency_ms", "bytes", "sha256", "fetched_at"]}},
            },
            "required": ["pair", "candles", "result", "history", "warmup", "methods", "source_calls"],
        },
        "summary": {
            "type": "object",
            "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
            "required": ["headline", "key_points"],
        },
        "availability": {"type": "object", "additionalProperties": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
    "additionalProperties": False,
}

EXAMPLE_CALL = {"symbol": "SOL", "timeframe": "1d", "condition": EXAMPLE_CONDITION}

TOOL_DEFINITION = {
    "name": TOOL_NAME,
    "title": "Indicator condition check",
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True, "destructiveHint": False},
}


def validate_input(args: Any) -> dict[str, Any]:
    """Return normalized arguments (defaults applied, symbol upper-cased, condition parsed) or raise InputError."""
    if not isinstance(args, dict):
        raise InputError("arguments", "must be an object", EXAMPLE_CALL)
    extra = set(args) - set(INPUT_SCHEMA["properties"])
    if extra:
        raise InputError("arguments", f"unknown fields {sorted(extra)}; allowed {sorted(INPUT_SCHEMA['properties'])}", EXAMPLE_CALL)
    for req in INPUT_SCHEMA["required"]:
        if req not in args:
            raise InputError(req, "is required", EXAMPLE_CALL)
    sym = args["symbol"]
    if not isinstance(sym, str) or not SYMBOL_RE.match(sym):
        raise InputError("symbol", "must be a 2 to 12 character token symbol such as SOL (letters and digits only; wallet addresses are not accepted)", EXAMPLE_CALL)
    out: dict[str, Any] = {"symbol": sym.upper()}
    defaults: list[str] = []

    def pick(name: str, default, check, message: str):
        if name in args:
            v = args[name]
            if not check(v):
                raise InputError(name, message, EXAMPLE_CALL)
            return v
        defaults.append(name)
        return default

    out["timeframe"] = pick("timeframe", "1d", lambda v: v in TIMEFRAMES, f"must be one of {list(TIMEFRAMES)}")
    out["lookback_bars"] = pick("lookback_bars", 300, lambda v: isinstance(v, int) and not isinstance(v, bool) and 50 <= v <= 1000, "must be an integer between 50 and 1000")
    out["venue"] = pick("venue", "auto", lambda v: v in VENUES, f"must be one of {list(VENUES)}")
    out["cross_venue_check"] = pick("cross_venue_check", False, lambda v: isinstance(v, bool), "must be true or false")
    cond = args["condition"]
    notes: list[str] = []
    if isinstance(cond, str):  # some clients send nested objects as JSON text; accepted and disclosed
        try:
            cond = json.loads(cond)
        except ValueError:
            raise InputError("condition", "is a string but not valid JSON; pass the expression tree as an object", EXAMPLE_CALL) from None
        notes.append("condition was received as a JSON string and parsed")
    tree = parse_condition(cond)
    need = required_bars(tree)
    if out["lookback_bars"] < need:
        raise InputError("lookback_bars", f"the condition needs at least {need} bars of history but lookback_bars is {out['lookback_bars']}", EXAMPLE_CALL)
    out["_tree"] = tree
    out["_need"] = need
    out["_defaults"] = defaults
    out["_notes"] = notes
    return out
