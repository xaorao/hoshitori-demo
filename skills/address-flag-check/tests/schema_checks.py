"""Checks of ``address_flag_check.schema``, runnable against a mutant: the two tool definitions (name, title, the four-part description,
input and output JSON Schema, annotations), and input validation with its exact refusals, defaults and coverage (D3 6 and 7.1).
"""

from __future__ import annotations

import types
from typing import Any

import jsonschema

from tests.checks_support import Case

DEAD = "0x000000000000000000000000000000000000dEaD"
CHAINS = ["ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin"]
CHECKS = ["ofac_sdn", "usdt_freeze", "usdc_freeze", "chain_facts"]
EXAMPLE = {"address": DEAD, "chain": "ethereum"}


def _got(c: Case, label: str, call: Any) -> Any:
    """``c.run`` that also counts a result of None as a failure: validate_input always returns the normalised arguments."""
    got = c.run(label, call)
    if got is None:
        c.bad.append(f"{label}: gave nothing")
    return got


# The published tool definitions, written out in full: clients read every word of them (MCP tools/list, GET /tools). The input schema
# and both descriptions are D3 7.1's text; the output schema is D3 7.1's data record with the changes B5A recorded in its hand-off
# (entries beside entry, family, format_ok and reliability on an entry, list.copy, block.pinned, the closed reason set with tls_error,
# chain_facts not_covered, the sources rows with age fields in days and seconds).
REASONS = [
    "timeout",
    "rate_limited",
    "server_error",
    "network_error",
    "bad_response",
    "list_not_loaded",
    "cooling_down",
    "budget_exhausted",
    "tls_error",
    None,
]
ENTRY_FIELDS = ["entry_id", "party_type", "programs", "label", "published_string", "family", "format_ok", "reliability"]
ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "entry_id": {"type": "string"},
        "party_type": {"enum": ["individual", "entity", "transport", "other"]},
        "programs": {"type": "array", "items": {"type": "string"}},
        "label": {"type": "string"},
        "published_string": {"type": "string"},
        "family": {"type": "string"},
        "format_ok": {"type": ["boolean", "null"]},
        "reliability": {"type": ["string", "null"]},
    },
    "required": ENTRY_FIELDS,
}
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "source_id": {"enum": ["ofac_sdn", "usdt_freeze_ethereum", "usdc_freeze_ethereum", "usdt_freeze_tron"]},
        "kind": {"enum": ["sanctions_list", "issuer_freeze"]},
        "state": {"enum": ["listed", "not_listed", "unavailable"]},
        "stale": {"type": "boolean"},
        "list": {
            "type": ["object", "null"],
            "required": [
                "name",
                "issued",
                "issued_age_days",
                "confirmed_current_age_seconds",
                "max_age_hours",
                "sha256",
                "address_strings",
                "copy",
            ],
        },
        "block": {"type": ["object", "null"], "required": ["chain", "number", "age_seconds", "pinned"]},
        "entry": {"anyOf": [ENTRY_SCHEMA, {"type": "null"}]},
        "entries": {"type": "array", "items": ENTRY_SCHEMA},
        "raw_value": {"type": ["boolean", "null"]},
        "reason": {"enum": REASONS},
        "source_url": {"type": "string"},
    },
    "required": ["source_id", "kind", "state", "stale", "list", "block", "entry", "entries", "raw_value", "reason", "source_url"],
    "additionalProperties": False,
}
CALL_SCHEMA = {
    "type": "object",
    "required": ["source", "url", "method", "attempt", "http_status", "latency_ms", "bytes", "sha256", "fetched_at", "error_kind"],
}


def _envelope_schema(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "tool": {"const": tool},
            "status": {"enum": ["ok", "partial", "unavailable"]},
            "data_mode": {"enum": ["live", "mixed", "simulated", "unknown"]},
            "as_of": {"type": "string"},
            "request": {"type": "object"},
            "data": data,
            "summary": {
                "type": "object",
                "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
                "required": ["headline", "key_points"],
            },
            "availability": {
                "type": "object",
                "additionalProperties": {"enum": ["available", "stale", "partial", "unavailable", "not_covered", "not_requested"]},
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
        "additionalProperties": False,
    }


ANNOTATIONS = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True, "destructiveHint": False}
EXPECTED_DEFINITIONS = [
    {
        "name": "address_flag_check",
        "title": "Address flag check",
        "description": "Delivery: for one blockchain address, whether OFAC's SDN list names it (with the entry) and whether the USDT and USDC "
        "issuers have frozen it, each with the list's issue age and last confirmation or the block read, plus basic chain facts. Use when: an "
        "agent or user wants to check a counterparty, deposit or contract address before acting. Core inputs: address, chain; optional checks, "
        "max_list_age_hours. Limits: published lists and issuer freezes only; not being listed is never reported as safe; no transaction, "
        "counterparty or behaviour analysis; EVM chains, Tron and Bitcoin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "One address: EVM 0x plus 40 hex (mixed case must pass EIP-55), Tron base58 starting with T, Bitcoin starting "
                    "with 1, 3 or bc1. Never a name, URL, transaction hash, key or recovery phrase.",
                },
                "chain": {
                    "type": "string",
                    "enum": CHAINS,
                    "description": "The chain the address is on, e.g. ethereum, tron or bitcoin. Never guessed from the address.",
                },
                "checks": {
                    "type": "array",
                    "uniqueItems": True,
                    "minItems": 1,
                    "items": {"enum": CHECKS},
                    "description": "Subset to run. Default: every check that covers the chain. ofac_sdn alone keeps the address on this machine.",
                },
                "max_list_age_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 168,
                    "default": 24,
                    "description": "A list copy last confirmed current longer ago than this is labelled stale and cannot support an absence result.",
                },
            },
            "required": ["address", "chain"],
            "additionalProperties": False,
        },
        "outputSchema": _envelope_schema(
            "address_flag_check",
            {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "required": ["normalized", "display", "chain", "format", "checksum"]},
                    "verdict": {"enum": ["flagged", "no_flag_found", "incomplete", None]},
                    "counts": {"type": "object", "required": ["in_scope", "listed", "not_listed", "stale", "unavailable"]},
                    "checks": {"type": "array", "items": CHECK_SCHEMA},
                    "not_covered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"check": {"enum": CHECKS}, "reason": {"enum": ["not_available_on_chain", "not_requested"]}},
                            "required": ["check", "reason"],
                        },
                    },
                    "chain_facts": {
                        "type": "object",
                        "properties": {
                            "state": {"enum": ["observed", "unavailable", "not_requested", "not_covered"]},
                            "reason": {"enum": REASONS},
                        },
                        "required": ["state", "chain"],
                    },
                    "sent_to": {"type": "array", "items": {"type": "string"}},
                    "source_calls": {"type": "array", "items": CALL_SCHEMA},
                },
                "required": ["address", "verdict", "counts", "checks", "not_covered", "chain_facts", "sent_to", "source_calls"],
            },
        ),
        "annotations": ANNOTATIONS,
    },
    {
        "name": "address_flag_sources",
        "title": "Address flag sources",
        "description": "Delivery: the sources address_flag_check uses, with each list's issue age, last confirmation, size and chains. Use when: "
        "an agent needs to know what an absence result covers. Core inputs: none; optional chain. Limits: describes sources only; does not "
        "check an address.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "enum": CHAINS, "description": "Only the sources that cover this chain (optional)."}
            },
            "additionalProperties": False,
        },
        "outputSchema": _envelope_schema(
            "address_flag_sources",
            {
                "type": "object",
                "properties": {
                    "chain": {"type": ["string", "null"]},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "source_id",
                                "name",
                                "kind",
                                "chains",
                                "maintained",
                                "maintained_basis",
                                "used",
                                "not_used_reason",
                                "endpoint",
                                "terms_url",
                                "terms_note",
                                "list_issued",
                                "list_issued_age_days",
                                "confirmed_current_age_seconds",
                                "address_strings",
                                "families",
                            ],
                        },
                    },
                },
                "required": ["chain", "sources"],
            },
        ),
        "annotations": ANNOTATIONS,
    },
]


def check_published_definitions(mod: types.ModuleType) -> list[str]:
    """Every word of both published definitions, part by part so that a difference is easy to find."""
    c = Case(mod)
    got = mod.TOOL_DEFINITIONS
    c.eq("two definitions", len(got), 2)
    for have, want in zip(got, EXPECTED_DEFINITIONS, strict=False):
        c.eq(f"{want['name']}: the parts", sorted(have), sorted(want))
        for part in want:
            if part == "outputSchema":
                for key in want[part]:
                    if key == "properties":
                        for prop in want[part][key]:
                            c.eq(f"{want['name']} outputSchema.properties.{prop}", have[part][key].get(prop), want[part][key][prop])
                        c.eq(f"{want['name']} outputSchema property names", sorted(have[part][key]), sorted(want[part][key]))
                    else:
                        c.eq(f"{want['name']} outputSchema.{key}", have[part].get(key), want[part][key])
                c.eq(f"{want['name']} outputSchema keys", sorted(have[part]), sorted(want[part]))
            else:
                c.eq(f"{want['name']}.{part}", have.get(part), want[part])
    c.eq("the main definition is TOOL_DEFINITION", got[0] is mod.TOOL_DEFINITION and got[1] is mod.SOURCES_TOOL_DEFINITION, True)
    c.eq("OUTPUT_SCHEMA is the one published", mod.OUTPUT_SCHEMA, EXPECTED_DEFINITIONS[0]["outputSchema"])
    c.eq("SOURCES_OUTPUT_SCHEMA is the one published", mod.SOURCES_OUTPUT_SCHEMA, EXPECTED_DEFINITIONS[1]["outputSchema"])
    return c.bad


def check_definitions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    main, side = mod.TOOL_DEFINITION, mod.SOURCES_TOOL_DEFINITION
    c.eq("tool names", (main["name"], side["name"]), ("address_flag_check", "address_flag_sources"))
    c.eq("titles", (main["title"], side["title"]), ("Address flag check", "Address flag sources"))
    c.eq("both tools listed", [t["name"] for t in mod.TOOL_DEFINITIONS], ["address_flag_check", "address_flag_sources"])
    c.eq(
        "the description of the check (D3 7.1)",
        main["description"],
        "Delivery: for one blockchain address, whether OFAC's SDN list names it (with the entry) and whether the USDT and USDC issuers have frozen it, "
        "each with the list's issue age and last confirmation or the block read, plus basic chain facts. Use when: an agent or user wants to check a "
        "counterparty, deposit or contract address before acting. Core inputs: address, chain; optional checks, max_list_age_hours. Limits: published "
        "lists and issuer freezes only; not being listed is never reported as safe; no transaction, counterparty or behaviour analysis; EVM chains, "
        "Tron and Bitcoin.",
    )
    c.eq(
        "the description of the sources tool (D3 7.1)",
        side["description"],
        "Delivery: the sources address_flag_check uses, with each list's issue age, last confirmation, size and chains. Use when: an agent needs to "
        "know what an absence result covers. Core inputs: none; optional chain. Limits: describes sources only; does not check an address.",
    )
    for tool in (main, side):
        c.eq(
            f"{tool['name']} annotations",
            tool["annotations"],
            {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True, "destructiveHint": False},
        )
        for part in ("inputSchema", "outputSchema"):
            try:
                jsonschema.Draft202012Validator.check_schema(tool[part])
            except jsonschema.SchemaError as exc:
                c.bad.append(f"{tool['name']} {part} is not a valid schema: {exc.message}")
    props = main["inputSchema"]["properties"]
    c.eq("input fields", sorted(props), ["address", "chain", "checks", "max_list_age_hours"])
    c.eq("required", main["inputSchema"]["required"], ["address", "chain"])
    c.eq("no other fields", main["inputSchema"]["additionalProperties"], False)
    c.eq("address length", props["address"]["maxLength"], 100)
    c.eq("chains", props["chain"]["enum"], CHAINS)
    c.eq("checks", props["checks"]["items"]["enum"], CHECKS)
    c.eq("checks are distinct and not empty", (props["checks"]["uniqueItems"], props["checks"]["minItems"]), (True, 1))
    c.eq(
        "list age",
        (props["max_list_age_hours"]["minimum"], props["max_list_age_hours"]["maximum"], props["max_list_age_hours"]["default"]),
        (1, 168, 24),
    )
    c.eq(
        "sources input",
        side["inputSchema"],
        {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "enum": CHAINS, "description": "Only the sources that cover this chain (optional)."}
            },
            "additionalProperties": False,
        },
    )
    jsonschema.validate(EXAMPLE, main["inputSchema"])
    c.eq("the working example", mod.EXAMPLE_CALL, EXAMPLE)
    out = main["outputSchema"]
    c.eq(
        "envelope fields",
        out["required"],
        ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"],
    )
    c.eq("the tool constant", out["properties"]["tool"], {"const": "address_flag_check"})
    c.eq("verdicts", out["properties"]["data"]["properties"]["verdict"]["enum"], ["flagged", "no_flag_found", "incomplete", None])
    c.eq("status values", out["properties"]["status"]["enum"], ["ok", "partial", "unavailable"])
    c.eq(
        "data fields",
        out["properties"]["data"]["required"],
        ["address", "verdict", "counts", "checks", "not_covered", "chain_facts", "sent_to", "source_calls"],
    )
    c.eq(
        "check states",
        out["properties"]["data"]["properties"]["checks"]["items"]["properties"]["state"]["enum"],
        ["listed", "not_listed", "unavailable"],
    )
    c.eq(
        "coverage",
        mod.COVERAGE,
        {
            "ofac_sdn": tuple(CHAINS),
            "usdt_freeze": ("ethereum", "tron"),
            "usdc_freeze": ("ethereum",),
            "chain_facts": ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "bitcoin"),
        },
    )
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = mod.validate_input
    a = _got(c, "the example", lambda: v(EXAMPLE))
    if a is not None:
        c.eq(
            "defaults",
            (a["checks"], a["max_list_age_hours"], a["defaults_applied"], a["not_covered"]),
            (["ofac_sdn", "usdt_freeze", "usdc_freeze", "chain_facts"], 24, ["checks", "max_list_age_hours"], []),
        )
        c.eq(
            "address",
            (a["address"].normalized, a["address"].display, a["chain"]),
            ("0x000000000000000000000000000000000000dead", DEAD, "ethereum"),
        )
    a = _got(c, "bitcoin", lambda: v({"address": "1BitcoinEaterAddressDontSendf59kuE", "chain": "bitcoin"}))
    if a is not None:
        c.eq(
            "bitcoin checks and coverage",
            (a["checks"], a["not_covered"]),
            (
                ["ofac_sdn", "chain_facts"],
                [
                    {"check": "usdt_freeze", "reason": "not_available_on_chain"},
                    {"check": "usdc_freeze", "reason": "not_available_on_chain"},
                ],
            ),
        )
    a = _got(c, "tron", lambda: v({"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "chain": "tron"}))
    if a is not None:
        c.eq(
            "tron checks and coverage",
            (a["checks"], a["not_covered"]),
            (
                ["ofac_sdn", "usdt_freeze"],
                [
                    {"check": "usdc_freeze", "reason": "not_available_on_chain"},
                    {"check": "chain_facts", "reason": "not_available_on_chain"},
                ],
            ),
        )
    a = _got(c, "base", lambda: v({"address": DEAD, "chain": "base"}))
    if a is not None:
        c.eq(
            "an EVM chain other than Ethereum",
            (a["checks"], a["not_covered"]),
            (
                ["ofac_sdn", "chain_facts"],
                [
                    {"check": "usdt_freeze", "reason": "not_available_on_chain"},
                    {"check": "usdc_freeze", "reason": "not_available_on_chain"},
                ],
            ),
        )
    a = _got(
        c, "a subset", lambda: v({"address": DEAD, "chain": "ethereum", "checks": ["usdc_freeze", "ofac_sdn"], "max_list_age_hours": 168})
    )
    if a is not None:
        c.eq(
            "a subset keeps the canonical order",
            (a["checks"], a["max_list_age_hours"], a["defaults_applied"]),
            (["ofac_sdn", "usdc_freeze"], 168, []),
        )
        c.eq(
            "what was not requested",
            a["not_covered"],
            [{"check": "usdt_freeze", "reason": "not_requested"}, {"check": "chain_facts", "reason": "not_requested"}],
        )
    a = _got(
        c, "a subset on bitcoin", lambda: v({"address": "1BitcoinEaterAddressDontSendf59kuE", "chain": "bitcoin", "checks": ["ofac_sdn"]})
    )
    if a is not None:
        c.eq(
            "not covered and not requested together",
            a["not_covered"],
            [
                {"check": "usdt_freeze", "reason": "not_available_on_chain"},
                {"check": "usdc_freeze", "reason": "not_available_on_chain"},
                {"check": "chain_facts", "reason": "not_requested"},
            ],
        )
    a = _got(c, "spaces around the address", lambda: v({"address": f" {DEAD} ", "chain": "ethereum", "max_list_age_hours": 1}))
    if a is not None:
        c.eq("notes", a["notes"], ["leading or trailing spaces were removed from the address"])
    for label, args, says in (
        ("not an object", [EXAMPLE], "arguments: must be an object"),
        (
            "an unknown field",
            {**EXAMPLE, "symbol": "SOL"},
            "arguments: unknown fields ['symbol']; allowed ['address', 'chain', 'checks', 'max_list_age_hours']",
        ),
        ("no address", {"chain": "ethereum"}, "address: is required"),
        ("no chain", {"address": DEAD}, "chain: is required"),
        ("nothing", {}, "address: is required"),
        (
            "an unknown chain",
            {**EXAMPLE, "chain": "solana"},
            "chain: must be one of ['ethereum', 'arbitrum', 'base', 'bsc', 'optimism', 'polygon', 'tron', 'bitcoin']",
        ),
        (
            "a chain in capitals",
            {**EXAMPLE, "chain": "Ethereum"},
            "chain: must be one of ['ethereum', 'arbitrum', 'base', 'bsc', 'optimism', 'polygon', 'tron', 'bitcoin']",
        ),
        (
            "a chain that is not text",
            {**EXAMPLE, "chain": 1},
            "chain: must be one of ['ethereum', 'arbitrum', 'base', 'bsc', 'optimism', 'polygon', 'tron', 'bitcoin']",
        ),
        (
            "a wrong-chain address",
            {"address": DEAD, "chain": "tron"},
            "address: is not a Tron address (base58check, version byte 0x41, starts with T)",
        ),
        (
            "a malformed address",
            {"address": "0x123", "chain": "ethereum"},
            "address: is not an EVM address (0x followed by 40 hex characters)",
        ),
        (
            "checks that are not a list",
            {**EXAMPLE, "checks": "ofac_sdn"},
            "checks: must be a non-empty list of distinct names from ['ofac_sdn', 'usdt_freeze', 'usdc_freeze', 'chain_facts']",
        ),
        (
            "empty checks",
            {**EXAMPLE, "checks": []},
            "checks: must be a non-empty list of distinct names from ['ofac_sdn', 'usdt_freeze', 'usdc_freeze', 'chain_facts']",
        ),
        (
            "a repeated check",
            {**EXAMPLE, "checks": ["ofac_sdn", "ofac_sdn"]},
            "checks: must be a non-empty list of distinct names from ['ofac_sdn', 'usdt_freeze', 'usdc_freeze', 'chain_facts']",
        ),
        (
            "an unknown check",
            {**EXAMPLE, "checks": ["ofac_sdn", "goplus"]},
            "checks: must be a non-empty list of distinct names from ['ofac_sdn', 'usdt_freeze', 'usdc_freeze', 'chain_facts']",
        ),
        ("facts only", {**EXAMPLE, "checks": ["chain_facts"]}, "checks: must include at least one of ofac_sdn, usdt_freeze, usdc_freeze"),
        (
            "a check the chain does not have",
            {"address": "1BitcoinEaterAddressDontSendf59kuE", "chain": "bitcoin", "checks": ["usdt_freeze"]},
            "checks: usdt_freeze does not cover bitcoin",
        ),
        (
            "facts on tron",
            {"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "chain": "tron", "checks": ["ofac_sdn", "chain_facts"]},
            "checks: chain_facts does not cover tron",
        ),
        (
            "USDC on arbitrum",
            {"address": DEAD, "chain": "arbitrum", "checks": ["usdc_freeze"]},
            "checks: usdc_freeze does not cover arbitrum",
        ),
        ("an age of zero", {**EXAMPLE, "max_list_age_hours": 0}, "max_list_age_hours: must be an integer between 1 and 168"),
        ("an age of 169", {**EXAMPLE, "max_list_age_hours": 169}, "max_list_age_hours: must be an integer between 1 and 168"),
        ("an age that is a fraction", {**EXAMPLE, "max_list_age_hours": 1.5}, "max_list_age_hours: must be an integer between 1 and 168"),
        ("an age that is true", {**EXAMPLE, "max_list_age_hours": True}, "max_list_age_hours: must be an integer between 1 and 168"),
        ("an age as text", {**EXAMPLE, "max_list_age_hours": "24"}, "max_list_age_hours: must be an integer between 1 and 168"),
    ):
        exc = c.raises(label, lambda args=args: v(args), mod.InputError, says)
        if exc is not None:
            c.eq(f"{label}: the refusal carries the working example", getattr(exc, "example", None), EXAMPLE)
    s = mod.validate_sources_input
    c.eq("sources: no chain", c.run("sources none", lambda: s({})), {"chain": None})
    c.eq("sources: a chain", c.run("sources tron", lambda: s({"chain": "tron"})), {"chain": "tron"})
    c.raises(
        "sources: an unknown chain",
        lambda: s({"chain": "sol"}),
        mod.InputError,
        "chain: must be one of ['ethereum', 'arbitrum', 'base', 'bsc', 'optimism', 'polygon', 'tron', 'bitcoin']",
    )
    exc = c.raises("sources: an unknown chain, the example", lambda: s({"chain": "sol"}), mod.InputError)
    c.eq("a sources refusal carries the working example", getattr(exc, "example", None), {"chain": "ethereum"})
    c.raises(
        "sources: an unknown field",
        lambda: s({"address": DEAD}),
        mod.InputError,
        "arguments: unknown fields ['address']; allowed ['chain']",
    )
    c.raises("sources: not an object", lambda: s(None), mod.InputError, "arguments: must be an object")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_definitions, check_validation, check_published_definitions):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
