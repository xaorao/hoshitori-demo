"""The two tool definitions (MCP form, also served at ``GET /tools``), input validation, and the output envelope schemas (D3 7.1).

Every refusal names its field and carries a working example; it never repeats the input. Validation happens before any network call.
"""

from __future__ import annotations

from typing import Any

from .errors import REASON_TEXT, InputError
from .normalize import CHAINS, EVM_CHAINS, Address, parse_address

TOOL_NAME = "address_flag_check"
SOURCES_TOOL_NAME = "address_flag_sources"
CHECKS = ("ofac_sdn", "usdt_freeze", "usdc_freeze", "chain_facts")
PRIMARY = ("ofac_sdn", "usdt_freeze", "usdc_freeze")
COVERAGE: dict[str, tuple[str, ...]] = {
    "ofac_sdn": CHAINS,
    "usdt_freeze": ("ethereum", "tron"),
    "usdc_freeze": ("ethereum",),
    "chain_facts": (*EVM_CHAINS, "bitcoin"),
}
MAX_AGE_DEFAULT = 24
MAX_AGE_RANGE = (1, 168)
EXAMPLE_CALL: dict[str, Any] = {"address": "0x000000000000000000000000000000000000dEaD", "chain": "ethereum"}
EXAMPLE_SOURCES_CALL: dict[str, Any] = {"chain": "ethereum"}

DESCRIPTION = (
    "Delivery: for one blockchain address, whether OFAC's SDN list names it (with the entry) and whether the USDT and USDC issuers have "
    "frozen it, each with the list's issue age and last confirmation or the block read, plus basic chain facts. Use when: an agent or user "
    "wants to check a counterparty, deposit or contract address before acting. Core inputs: address, chain; optional checks, "
    "max_list_age_hours. Limits: published lists and issuer freezes only; not being listed is never reported as safe; no transaction, "
    "counterparty or behaviour analysis; EVM chains, Tron and Bitcoin."
)
SOURCES_DESCRIPTION = (
    "Delivery: the sources address_flag_check uses, with each list's issue age, last confirmation, size and chains. Use when: an agent "
    "needs to know what an absence result covers. Core inputs: none; optional chain. Limits: describes sources only; does not check an "
    "address."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "address": {
            "type": "string",
            "maxLength": 100,
            "description": "One address: EVM 0x plus 40 hex (mixed case must pass EIP-55), Tron base58 starting with T, Bitcoin "
            "starting with 1, 3 or bc1. Never a name, URL, transaction hash, key or recovery phrase.",
        },
        "chain": {
            "type": "string",
            "enum": list(CHAINS),
            "description": "The chain the address is on, e.g. ethereum, tron or bitcoin. Never guessed from the address.",
        },
        "checks": {
            "type": "array",
            "uniqueItems": True,
            "minItems": 1,
            "items": {"enum": list(CHECKS)},
            "description": "Subset to run. Default: every check that covers the chain. ofac_sdn alone keeps the address on this machine.",
        },
        "max_list_age_hours": {
            "type": "integer",
            "minimum": MAX_AGE_RANGE[0],
            "maximum": MAX_AGE_RANGE[1],
            "default": MAX_AGE_DEFAULT,
            "description": "A list copy last confirmed current longer ago than this is labelled stale and cannot support an absence "
            "result.",
        },
    },
    "required": ["address", "chain"],
    "additionalProperties": False,
}
SOURCES_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"chain": {"type": "string", "enum": list(CHAINS), "description": "Only the sources that cover this chain (optional)."}},
    "additionalProperties": False,
}

_REASON = {"enum": [*REASON_TEXT, None]}
_ENTRY: dict[str, Any] = {
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
    "required": ["entry_id", "party_type", "programs", "label", "published_string", "family", "format_ok", "reliability"],
}
_CHECK: dict[str, Any] = {
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
        "entry": {"anyOf": [_ENTRY, {"type": "null"}]},
        "entries": {"type": "array", "items": _ENTRY},
        "raw_value": {"type": ["boolean", "null"]},
        "reason": _REASON,
        "source_url": {"type": "string"},
    },
    "required": ["source_id", "kind", "state", "stale", "list", "block", "entry", "entries", "raw_value", "reason", "source_url"],
    "additionalProperties": False,
}
_CALL: dict[str, Any] = {
    "type": "object",
    "required": ["source", "url", "method", "attempt", "http_status", "latency_ms", "bytes", "sha256", "fetched_at", "error_kind"],
}
_AVAILABILITY = {
    "type": "object",
    "additionalProperties": {"enum": ["available", "stale", "partial", "unavailable", "not_covered", "not_requested"]},
}
_SUMMARY = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
    "required": ["headline", "key_points"],
}
_ENVELOPE_FIELDS = ["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"]


def _envelope(tool: str, data: dict[str, Any]) -> dict[str, Any]:
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
            "summary": _SUMMARY,
            "availability": _AVAILABILITY,
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(_ENVELOPE_FIELDS),
        "additionalProperties": False,
    }


OUTPUT_SCHEMA = _envelope(
    TOOL_NAME,
    {
        "type": "object",
        "properties": {
            "address": {"type": "object", "required": ["normalized", "display", "chain", "format", "checksum"]},
            "verdict": {"enum": ["flagged", "no_flag_found", "incomplete", None]},
            "counts": {"type": "object", "required": ["in_scope", "listed", "not_listed", "stale", "unavailable"]},
            "checks": {"type": "array", "items": _CHECK},
            "not_covered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"check": {"enum": list(CHECKS)}, "reason": {"enum": ["not_available_on_chain", "not_requested"]}},
                    "required": ["check", "reason"],
                },
            },
            "chain_facts": {
                "type": "object",
                "properties": {"state": {"enum": ["observed", "unavailable", "not_requested", "not_covered"]}, "reason": _REASON},
                "required": ["state", "chain"],
            },
            "sent_to": {"type": "array", "items": {"type": "string"}},
            "source_calls": {"type": "array", "items": _CALL},
        },
        "required": ["address", "verdict", "counts", "checks", "not_covered", "chain_facts", "sent_to", "source_calls"],
    },
)
SOURCES_OUTPUT_SCHEMA = _envelope(
    SOURCES_TOOL_NAME,
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
)

_ANNOTATIONS = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True, "destructiveHint": False}
TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "title": "Address flag check",
    "description": DESCRIPTION,
    "inputSchema": INPUT_SCHEMA,
    "outputSchema": OUTPUT_SCHEMA,
    "annotations": _ANNOTATIONS,
}
SOURCES_TOOL_DEFINITION: dict[str, Any] = {
    "name": SOURCES_TOOL_NAME,
    "title": "Address flag sources",
    "description": SOURCES_DESCRIPTION,
    "inputSchema": SOURCES_INPUT_SCHEMA,
    "outputSchema": SOURCES_OUTPUT_SCHEMA,
    "annotations": _ANNOTATIONS,
}
TOOL_DEFINITIONS = [TOOL_DEFINITION, SOURCES_TOOL_DEFINITION]


def _fields(args: object, allowed: dict[str, Any], example: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise InputError("arguments", "must be an object", example)
    extra = set(args) - set(allowed)
    if extra:
        raise InputError("arguments", f"unknown fields {sorted(extra)}; allowed {sorted(allowed)}", example)
    return args


def _chain(value: object, example: dict[str, Any]) -> str:
    if not isinstance(value, str) or value not in CHAINS:
        raise InputError("chain", f"must be one of {list(CHAINS)}", example)
    return value


def validate_input(args: object) -> dict[str, Any]:
    """Normalised arguments: the parsed address, the checks to run in canonical order, what is not covered or not requested, the list age
    limit, the defaults applied and any notes. Raises InputError."""
    a = _fields(args, INPUT_SCHEMA["properties"], EXAMPLE_CALL)
    for name in ("address", "chain"):
        if name not in a:
            raise InputError(name, "is required", EXAMPLE_CALL)
    chain = _chain(a["chain"], EXAMPLE_CALL)
    try:
        address: Address = parse_address(a["address"], chain)
    except InputError as exc:
        raise InputError(exc.field, exc.detail, EXAMPLE_CALL) from None
    defaults: list[str] = []
    covering = [c for c in CHECKS if chain in COVERAGE[c]]
    if "checks" in a:
        asked = a["checks"]
        if not isinstance(asked, list) or not asked or len(set(map(str, asked))) != len(asked) or any(x not in CHECKS for x in asked):
            raise InputError("checks", f"must be a non-empty list of distinct names from {list(CHECKS)}", EXAMPLE_CALL)
        if not any(x in PRIMARY for x in asked):
            raise InputError("checks", f"must include at least one of {', '.join(PRIMARY)}", EXAMPLE_CALL)
        for name in CHECKS:
            if name in asked and name not in covering:
                raise InputError("checks", f"{name} does not cover {chain}", EXAMPLE_CALL)
        checks = [c for c in CHECKS if c in asked]
    else:
        defaults.append("checks")
        checks = covering
    not_covered = [
        {"check": c, "reason": "not_available_on_chain" if c not in covering else "not_requested"} for c in CHECKS if c not in checks
    ]
    if "max_list_age_hours" in a:
        age = a["max_list_age_hours"]
        if not isinstance(age, int) or isinstance(age, bool) or not MAX_AGE_RANGE[0] <= age <= MAX_AGE_RANGE[1]:
            raise InputError("max_list_age_hours", f"must be an integer between {MAX_AGE_RANGE[0]} and {MAX_AGE_RANGE[1]}", EXAMPLE_CALL)
    else:
        defaults.append("max_list_age_hours")
        age = MAX_AGE_DEFAULT
    return {
        "address": address,
        "chain": chain,
        "checks": checks,
        "not_covered": not_covered,
        "max_list_age_hours": age,
        "defaults_applied": defaults,
        "notes": list(address.notes),
    }


def validate_sources_input(args: object) -> dict[str, Any]:
    a = _fields(args, SOURCES_INPUT_SCHEMA["properties"], EXAMPLE_SOURCES_CALL)
    return {"chain": _chain(a["chain"], EXAMPLE_SOURCES_CALL) if "chain" in a else None}
