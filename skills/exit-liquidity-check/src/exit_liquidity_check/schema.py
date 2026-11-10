"""The tool definitions (MCP form, also served at ``GET /tools``), input validation and the output envelope schemas.

Every refusal names its field and carries a working example; it never repeats the input. Validation happens before any network call.
The description follows the four-part template of RYO's own catalogue entries: Delivery, Use when, Core inputs, Limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .errors import InputError
from .registry import SUPPORTED

TOOL_NAME = "exit_liquidity_check"
SOURCES_TOOL_NAME = "exit_liquidity_sources"
SCHEMA_VERSION = "1.0"
EXAMPLE_CALL: dict[str, Any] = {"token": "SOL", "size_usd": 50000}
EXAMPLE_SOURCES_CALL: dict[str, Any] = {}
DEFAULT_MAX_SLIPPAGE_PCT = 3.0
DEFAULT_PARTICIPATION_PCT = 10.0
MAX_SIZE_USD = 1e10

DESCRIPTION = (
    "Delivery: for one token and an optional position size in USD, what closing the position costs: price impact and dollars, the largest "
    "exit at 1 percent, 3 percent and your own slippage ceiling, and the hours a patient exit takes at a share of daily volume, from "
    "on-chain pool depth and, for Solana tokens, an executable Jupiter quote of the exact size. Use when: an agent or person sizes a "
    "position and wants to know whether it can be closed. Core inputs: token; optional size_usd, max_slippage_pct, participation_pct. "
    "Limits: a registry of six tokens (" + ", ".join(SUPPORTED) + "); pool figures are modelled (constant product), only a Jupiter quote "
    "is observed; no centralised-exchange depth; nothing is reported as zero when it is unknown."
)
SOURCES_DESCRIPTION = (
    "Delivery: the tokens exit_liquidity_check covers, the contract each is measured by and the two sources it reads. Use when: an agent "
    "needs to know what an answer covers. Core inputs: none. Limits: describes coverage only; reads nothing from the network."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "token": {
            "type": "string",
            "enum": list(SUPPORTED),
            "description": "The token symbol. Measured by its canonical contract, never by name.",
        },
        "size_usd": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": MAX_SIZE_USD,
            "description": "The position size to price an exit for, in US dollars. Omit for the ceilings and the cost curve alone.",
        },
        "max_slippage_pct": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 50,
            "default": DEFAULT_MAX_SLIPPAGE_PCT,
            "description": "The price impact you accept when exiting, in percent. It sets the verdict bands and one of the ceilings.",
        },
        "participation_pct": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 100,
            "default": DEFAULT_PARTICIPATION_PCT,
            "description": "The share of 24 hour volume you are willing to be while exiting patiently. It sets the hours to exit.",
        },
    },
    "required": ["token"],
    "additionalProperties": False,
}
SOURCES_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


_ENVELOPE_KEYS = ("schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings")


def _envelope_schema(tool: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(_ENVELOPE_KEYS),
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
SOURCES_OUTPUT_SCHEMA: dict[str, Any] = _envelope_schema(SOURCES_TOOL_NAME)
# openWorldHint: the check reads DexScreener and Jupiter; the sources tool describes the coverage and reads nothing.
TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
}
SOURCES_TOOL_DEFINITION: dict[str, Any] = {
    "name": SOURCES_TOOL_NAME,
    "description": SOURCES_DESCRIPTION,
    "inputSchema": SOURCES_INPUT_SCHEMA,
    "outputSchema": SOURCES_OUTPUT_SCHEMA,
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}
TOOL_DEFINITIONS: list[dict[str, Any]] = [TOOL_DEFINITION, SOURCES_TOOL_DEFINITION]


@dataclass(frozen=True)
class Request:
    token: str
    size_usd: float | None
    max_slippage: float  # a share, 0 to 1
    participation: float  # a share, 0 to 1


def _number(args: dict[str, Any], key: str, low_exclusive: float, high: float) -> float | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(key, "must be a number", EXAMPLE_CALL)
    if not low_exclusive < value <= high:
        raise InputError(key, f"must be above {low_exclusive:g} and at most {high:g}", EXAMPLE_CALL)
    return float(value)


def parse_request(args: object) -> Request:
    if not isinstance(args, dict):
        raise InputError("arguments", "must be an object", EXAMPLE_CALL)
    unknown = sorted(set(args) - set(INPUT_SCHEMA["properties"]))
    if unknown:
        raise InputError(unknown[0], "is not an argument of this tool", EXAMPLE_CALL)
    token = args.get("token")
    if not isinstance(token, str) or not token.strip():
        raise InputError("token", f"is required: one of {', '.join(SUPPORTED)}", EXAMPLE_CALL)
    symbol = token.strip().upper()
    if symbol not in SUPPORTED:
        raise InputError("token", f"is not covered; the covered tokens are {', '.join(SUPPORTED)}", EXAMPLE_CALL)
    size = _number(args, "size_usd", 0.0, MAX_SIZE_USD)
    slip = _number(args, "max_slippage_pct", 0.0, 50.0)
    part = _number(args, "participation_pct", 0.0, 100.0)
    return Request(
        symbol,
        size,
        (DEFAULT_MAX_SLIPPAGE_PCT if slip is None else slip) / 100.0,
        (DEFAULT_PARTICIPATION_PCT if part is None else part) / 100.0,
    )
