"""Checks of ``exit_liquidity_check.schema``, runnable against a mutant: the published tool definitions word for word, the published output
schemas file for file, and request validation at every boundary with its exact refusal."""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

from exit_liquidity_check.errors import InputError
from tests.checks_support import Case

SCHEMAS = Path(__file__).parent.parent / "schema"
COVERED = "SOL, BONK, JUP, WIF, ETH, BTC"
EXAMPLE = {"token": "SOL", "size_usd": 50000}
DESCRIPTION = (
    "Delivery: for one token and an optional position size in USD, what closing the position costs: price impact and dollars, the largest "
    "exit at 1 percent, 3 percent and your own slippage ceiling, and the hours a patient exit takes at a share of daily volume, from "
    "on-chain pool depth and, for Solana tokens, an executable Jupiter quote of the exact size. Use when: an agent or person sizes a "
    "position and wants to know whether it can be closed. Core inputs: token; optional size_usd, max_slippage_pct, participation_pct. "
    "Limits: a registry of six tokens (SOL, BONK, JUP, WIF, ETH, BTC); pool figures are modelled (constant product), only a Jupiter quote "
    "is observed; no centralised-exchange depth; nothing is reported as zero when it is unknown."
)
SOURCES_DESCRIPTION = (
    "Delivery: the tokens exit_liquidity_check covers, the contract each is measured by and the two sources it reads. Use when: an agent "
    "needs to know what an answer covers. Core inputs: none. Limits: describes coverage only; reads nothing from the network."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "token": {
            "type": "string",
            "enum": ["SOL", "BONK", "JUP", "WIF", "ETH", "BTC"],
            "description": "The token symbol. Measured by its canonical contract, never by name.",
        },
        "size_usd": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 1e10,
            "description": "The position size to price an exit for, in US dollars. Omit for the ceilings and the cost curve alone.",
        },
        "max_slippage_pct": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 50,
            "default": 3.0,
            "description": "The price impact you accept when exiting, in percent. It sets the verdict bands and one of the ceilings.",
        },
        "participation_pct": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 100,
            "default": 10.0,
            "description": "The share of 24 hour volume you are willing to be while exiting patiently. It sets the hours to exit.",
        },
    },
    "required": ["token"],
    "additionalProperties": False,
}


def _refusal(mod: types.ModuleType, args: Any) -> tuple[str, str, Any] | None:
    try:
        mod.parse_request(args)
    except InputError as err:
        return err.field, err.detail, err.example
    return None


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("names", (mod.TOOL_NAME, mod.SOURCES_TOOL_NAME, mod.SCHEMA_VERSION), ("exit_liquidity_check", "exit_liquidity_sources", "1.0"))
    c.eq("example call", (mod.EXAMPLE_CALL, mod.EXAMPLE_SOURCES_CALL), (EXAMPLE, {}))
    c.eq("defaults and limit", (mod.DEFAULT_MAX_SLIPPAGE_PCT, mod.DEFAULT_PARTICIPATION_PCT, mod.MAX_SIZE_USD), (3.0, 10.0, 1e10))
    c.eq("description", mod.DESCRIPTION, DESCRIPTION)
    c.eq("sources description", mod.SOURCES_DESCRIPTION, SOURCES_DESCRIPTION)
    c.eq("input schema", mod.INPUT_SCHEMA, INPUT_SCHEMA)
    c.eq("sources input schema", mod.SOURCES_INPUT_SCHEMA, {"type": "object", "properties": {}, "additionalProperties": False})
    c.eq(
        "the output schema is the published file",
        mod.OUTPUT_SCHEMA,
        json.loads((SCHEMAS / "exit_liquidity_check.output.schema.json").read_text()),
    )
    c.eq(
        "the sources output schema is the published file",
        mod.SOURCES_OUTPUT_SCHEMA,
        json.loads((SCHEMAS / "exit_liquidity_sources.output.schema.json").read_text()),
    )
    c.eq(
        "definition",
        mod.TOOL_DEFINITION,
        {
            "name": "exit_liquidity_check",
            "description": DESCRIPTION,
            "inputSchema": INPUT_SCHEMA,
            "outputSchema": mod.OUTPUT_SCHEMA,
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        },
    )
    c.eq(
        "sources definition",
        mod.SOURCES_TOOL_DEFINITION,
        {
            "name": "exit_liquidity_sources",
            "description": SOURCES_DESCRIPTION,
            "inputSchema": mod.SOURCES_INPUT_SCHEMA,
            "outputSchema": mod.SOURCES_OUTPUT_SCHEMA,
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
    )
    c.eq("both definitions, check first", mod.TOOL_DEFINITIONS, [mod.TOOL_DEFINITION, mod.SOURCES_TOOL_DEFINITION])
    c.eq(
        "the two envelope schemas differ only in the tool name",
        (mod.OUTPUT_SCHEMA["properties"]["tool"], mod.SOURCES_OUTPUT_SCHEMA["properties"]["tool"]),
        ({"const": "exit_liquidity_check"}, {"const": "exit_liquidity_sources"}),
    )

    R = mod.Request
    # valid requests
    c.eq("token only", mod.parse_request({"token": "sol"}), R("SOL", None, 3.0 / 100.0, 10.0 / 100.0))
    c.eq("trimmed and upper-cased", mod.parse_request({"token": "  bonk\n"}).token, "BONK")
    c.eq(
        "every argument",
        mod.parse_request({"token": "ETH", "size_usd": 2500, "max_slippage_pct": 1.5, "participation_pct": 25}),
        R("ETH", 2500.0, 1.5 / 100.0, 25.0 / 100.0),
    )
    c.eq("size is a float even when given as an int", type(mod.parse_request({"token": "ETH", "size_usd": 5}).size_usd), float)
    c.eq(
        "null is omitted",
        mod.parse_request({"token": "SOL", "size_usd": None, "max_slippage_pct": None, "participation_pct": None}),
        R("SOL", None, 0.03, 0.1),
    )
    for token in ("SOL", "BONK", "JUP", "WIF", "ETH", "BTC"):
        c.eq(f"covered {token}", mod.parse_request({"token": token}).token, token)
    c.eq("a request is immutable", _frozen(R("SOL", None, 0.03, 0.1)), True)

    # numeric boundaries: exclusive below, inclusive above
    ok = lambda key, value: mod.parse_request({"token": "SOL", key: value})  # noqa: E731
    c.eq("size at the maximum", ok("size_usd", 1e10).size_usd, 1e10)
    c.eq("size just under the maximum", ok("size_usd", 9_999_999_999.0).size_usd, 9_999_999_999.0)
    c.eq("size just above zero", ok("size_usd", 1e-9).size_usd, 1e-9)
    c.eq("slippage at 50", ok("max_slippage_pct", 50).max_slippage, 0.5)
    c.eq("slippage just above zero", ok("max_slippage_pct", 0.0001).max_slippage, 0.0001 / 100.0)
    c.eq("participation at 100", ok("participation_pct", 100).participation, 1.0)
    c.eq("participation just above zero", ok("participation_pct", 0.0001).participation, 0.0001 / 100.0)
    for key, bad, text in (
        ("size_usd", 0, "must be above 0 and at most 1e+10"),
        ("size_usd", -1, "must be above 0 and at most 1e+10"),
        ("size_usd", 1e10 + 1e-5, "must be above 0 and at most 1e+10"),
        ("size_usd", 1e12, "must be above 0 and at most 1e+10"),
        ("max_slippage_pct", 0, "must be above 0 and at most 50"),
        ("max_slippage_pct", -0.5, "must be above 0 and at most 50"),
        ("max_slippage_pct", 50.0001, "must be above 0 and at most 50"),
        ("participation_pct", 0, "must be above 0 and at most 100"),
        ("participation_pct", -1, "must be above 0 and at most 100"),
        ("participation_pct", 100.0001, "must be above 0 and at most 100"),
        ("size_usd", True, "must be a number"),
        ("size_usd", False, "must be a number"),
        ("size_usd", "5", "must be a number"),
        ("size_usd", [5], "must be a number"),
        ("size_usd", {}, "must be a number"),
        ("size_usd", float("nan"), "must be a number"),
        ("size_usd", float("inf"), "must be a number"),
        ("size_usd", float("-inf"), "must be a number"),
        ("max_slippage_pct", "3", "must be a number"),
        ("participation_pct", True, "must be a number"),
    ):
        c.eq(f"{key}={bad!r}", _refusal(mod, {"token": "SOL", key: bad}), (key, text, EXAMPLE))

    # token and shape
    token_missing = ("token", f"is required: one of {COVERED}", EXAMPLE)
    for label, args in (
        ("none given", {}),
        ("null", {"token": None}),
        ("empty", {"token": ""}),
        ("blank", {"token": "   "}),
        ("a number", {"token": 5}),
        ("a list", {"token": ["SOL"]}),
        ("a bool", {"token": True}),
    ):
        c.eq(f"token {label}", _refusal(mod, args), token_missing)
    c.eq("token not covered", _refusal(mod, {"token": "doge"}), ("token", f"is not covered; the covered tokens are {COVERED}", EXAMPLE))
    c.eq("a token with an inner space", _refusal(mod, {"token": "S OL"})[0], "token")  # type: ignore[index]
    for label, args in (("a list", ["SOL"]), ("text", "SOL"), ("none", None), ("a number", 5)):
        c.eq(f"arguments {label}", _refusal(mod, args), ("arguments", "must be an object", EXAMPLE))
    c.eq("an unknown argument", _refusal(mod, {"token": "SOL", "colour": "red"}), ("colour", "is not an argument of this tool", EXAMPLE))
    c.eq("the first unknown argument by name", _refusal(mod, {"token": "SOL", "zeta": 1, "alpha": 2})[0], "alpha")  # type: ignore[index]
    c.eq("an unknown argument is judged before the token", _refusal(mod, {"colour": 1, "token": "DOGE"})[0], "colour")  # type: ignore[index]
    c.eq("the token is judged before the numbers", _refusal(mod, {"token": "DOGE", "size_usd": -1})[0], "token")  # type: ignore[index]
    c.eq("size is judged before slippage", _refusal(mod, {"token": "SOL", "size_usd": -1, "max_slippage_pct": -1})[0], "size_usd")  # type: ignore[index]
    c.eq(
        "slippage is judged before participation",
        _refusal(mod, {"token": "SOL", "max_slippage_pct": -1, "participation_pct": -1})[0],
        "max_slippage_pct",
    )  # type: ignore[index]
    refused = _refusal(mod, {"token": "zzzz"})
    c.eq("a refusal never repeats the input", refused is not None and "zzzz" not in refused[1].lower(), True)
    return c.failed


def _frozen(request: Any) -> bool:
    try:
        request.token = "x"
    except (AttributeError, TypeError):
        return True
    return False
