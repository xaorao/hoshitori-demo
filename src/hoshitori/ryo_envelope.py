"""Reading what RYO sends back: the public result envelope, the REST wrapper, the MCP wrapper and the error bodies.

``RYO_API_COMPATIBILITY`` section 4 is the contract. Every read goes through ``jsonnum`` so numbers keep the spelling in the raw bytes
and a hostile body (duplicate keys, ``NaN``, deep nesting, a lone surrogate) is refused before it is looked at. Two kinds of refusal:

* ``MALFORMED``       the bytes are not a usable JSON document of the expected outer shape;
* ``CONTRACT_DRIFT``  the document is JSON but is not what the contract says (a missing member, a member of the wrong type, a status or
                      data mode that is not in the contract, a different ``schema_version``). The tool is then unavailable until a
                      person has looked; the other tools continue (the breaker scopes both classes to the tool).

An addition is not a break: members the contract does not list are kept in ``extra_keys`` (and surfaced by the compatibility suite),
never used and never an error. ``summary`` and ``warnings`` are text for people; nothing here reads meaning into them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, TypeGuard

from hoshitori import jsonnum
from hoshitori.connector import ConnectorError, ErrorClass, parse_retry_after
from hoshitori.timeutil import TimeError, parse_rfc3339

SCHEMA_VERSION: Final = "1.0"
STATUSES: Final = frozenset({"ok", "partial", "unavailable"})
DATA_MODES: Final = frozenset({"live", "mixed", "simulated", "unknown"})
ENVELOPE_KEYS: Final = ("schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings")
OPTIONAL_ENVELOPE_KEYS: Final = frozenset({"trace_id"})
MAX_TEXT: Final = 2000  # a message kept from an error body is cut here
TRACE_ID: Final = re.compile(r"[0-9a-fA-F]{8,64}")


@dataclass(frozen=True)
class Envelope:
    tool: str
    schema_version: str
    status: str
    data_mode: str
    as_of: datetime
    request: Mapping[str, Any]
    data: Mapping[str, Any]
    summary: Mapping[str, Any]
    availability: Mapping[str, str]
    warnings: tuple[str, ...]
    latency_ms: jsonnum.Num | None
    extra_keys: tuple[str, ...]


@dataclass(frozen=True)
class ErrorBody:
    code: str | None
    message: str
    details: Any
    trace_id: str | None


def _drift(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.CONTRACT_DRIFT, f"RYO's response {message}")


def load(raw: bytes) -> Any:
    """The JSON value in ``raw``, or ``MALFORMED``."""
    try:
        return jsonnum.parse(raw)
    except jsonnum.JsonError as exc:
        raise ConnectorError(ErrorClass.MALFORMED, f"RYO's response is not readable JSON ({exc.code})") from exc


def _text_list(value: Any) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def read_envelope(obj: Any, tool: str) -> Envelope:
    """The public result envelope of a successful call to ``tool`` (``obj`` is the parsed ``result``)."""
    if not isinstance(obj, dict):
        raise _drift("is not an object")
    missing = [key for key in ENVELOPE_KEYS if key not in obj]
    if missing:
        raise _drift(f"lacks {missing}")
    if obj["schema_version"] != SCHEMA_VERSION:
        raise _drift(f"has schema_version {obj['schema_version']!r}, not {SCHEMA_VERSION!r}")
    if obj["tool"] != tool:
        raise _drift(f"answers for tool {obj['tool']!r}, not {tool!r}")
    if not (isinstance(obj["status"], str) and obj["status"] in STATUSES):
        raise _drift(f"has a status outside the contract: {obj['status']!r}")
    if not (isinstance(obj["data_mode"], str) and obj["data_mode"] in DATA_MODES):
        raise _drift(f"has a data mode outside the contract: {obj['data_mode']!r}")
    as_of = obj["as_of"]
    if not isinstance(as_of, str):
        raise _drift("has an as_of that is not text")
    try:
        moment = parse_rfc3339(as_of)
    except TimeError as exc:
        raise _drift(f"has an as_of that is not an RFC 3339 time: {as_of!r}") from exc
    if not isinstance(obj["request"], dict) or not isinstance(obj["data"], dict):
        raise _drift("has a request or data that is not an object")
    summary, availability, warnings = obj["summary"], obj["availability"], obj["warnings"]
    if not (isinstance(summary, dict) and isinstance(summary.get("headline"), str) and _text_list(summary.get("key_points"))):
        raise _drift("has a summary without a headline and key points")
    if not (isinstance(availability, dict) and all(isinstance(v, str) for v in availability.values())):
        raise _drift("has an availability that is not a mapping to words")
    if not _text_list(warnings):
        raise _drift("has warnings that are not a list of text")
    extra = tuple(sorted(set(obj) - set(ENVELOPE_KEYS) - OPTIONAL_ENVELOPE_KEYS))
    return Envelope(
        tool=tool,
        schema_version=obj["schema_version"],
        status=obj["status"],
        data_mode=obj["data_mode"],
        as_of=moment,
        request=obj["request"],
        data=obj["data"],
        summary=summary,
        availability=availability,
        warnings=tuple(warnings),
        latency_ms=None,
        extra_keys=extra,
    )


def read_rest_success(raw: bytes, tool: str) -> Envelope:
    """The body of a 2xx answer to ``POST /tools/<tool>/call``: ``{tool, result, latency_ms}``."""
    body = load(raw)
    if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
        raise _drift("has no result object")
    if body.get("tool") != tool:
        raise _drift(f"answers for tool {body.get('tool')!r}, not {tool!r}")
    latency = body.get("latency_ms")
    if latency is not None and not isinstance(latency, jsonnum.Num):
        raise _drift("has a latency that is not a number")
    envelope = read_envelope(body["result"], tool)
    extra = tuple(sorted(set(envelope.extra_keys) | {f"body.{key}" for key in body if key not in ("tool", "result", "latency_ms")}))
    return Envelope(**{**envelope.__dict__, "latency_ms": latency, "extra_keys": extra})


# --- MCP ------------------------------------------------------------------------------------------------------------------------------
# Tool failures over MCP are HTTP 200 with ``result.isError`` and a plain text message with no code, so they are told apart by anchored
# patterns. The set is observed (third-party recordings of 2026-09), not complete: anything else is UNCLASSIFIED, which is treated as an
# upstream failure and logged for review.
_MCP_PATTERNS: Final[tuple[tuple[re.Pattern[str], ErrorClass], ...]] = (
    (re.compile(r"Rate limit exceeded\b"), ErrorClass.RATE_LIMIT),
    (re.compile(r"[a-z_]+ requires an? '[a-z_]+'"), ErrorClass.INVALID_ARGS),
    (re.compile(r"Unknown tool\b"), ErrorClass.CONTRACT_DRIFT),
    (re.compile(r"couldn't analyze\b"), ErrorClass.NOT_FOUND),
)


def classify_mcp_text(text: str) -> ErrorClass:
    for pattern, error_class in _MCP_PATTERNS:
        if pattern.match(text):
            return error_class
    return ErrorClass.UNCLASSIFIED


def _trace_in(text: str) -> str | None:
    match = re.search(r"trace_id: ([0-9a-fA-F]{8,64})", text)
    return match.group(1) if match else None


def read_mcp_call(raw: bytes, tool: str, *, retry_after: str | None = None, now: datetime | None = None) -> Envelope:
    """The answer to a JSON-RPC ``tools/call``: the envelope inside ``result.content[0].text``, or the ``ConnectorError`` it describes."""
    body = load(raw)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        raise _drift("is not a JSON-RPC 2.0 response")
    error = body.get("error")
    if error is not None:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise _drift("has a JSON-RPC error without a message")
        data = error.get("data")
        trace = data.get("trace_id") if isinstance(data, dict) and isinstance(data.get("trace_id"), str) else _trace_in(error["message"])
        code = error.get("code")
        if isinstance(code, jsonnum.Num) and code.text == "-32602" and error["message"].startswith("Unknown tool"):
            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, error["message"][:MAX_TEXT], trace_id=trace)
        raise ConnectorError(ErrorClass.UPSTREAM, error["message"][:MAX_TEXT], trace_id=trace)
    result = body.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("isError"), bool):
        raise _drift("has no result with an isError flag")
    content = result.get("content")
    first = content[0] if isinstance(content, list) and content else None
    if not (isinstance(first, dict) and first.get("type") == "text" and isinstance(first.get("text"), str)):
        raise _drift("has no text content")
    text: str = first["text"]
    if result["isError"]:
        error_class = classify_mcp_text(text)
        wait = parse_retry_after(retry_after, now) if now is not None else None
        raise ConnectorError(error_class, text[:MAX_TEXT], retry_after_s=wait, trace_id=_trace_in(text))
    return read_envelope(load(text.encode()), tool)


# --- REST error bodies ------------------------------------------------------------------------------------------------------------------
def read_error_body(raw: bytes) -> ErrorBody | None:
    """The ``{code, message, details, trace_id}`` body of a REST error, or None if the body is not that (a proxy's HTML page, empty)."""
    try:
        body = jsonnum.parse(raw)
    except jsonnum.JsonError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return None
    code = body.get("code")
    trace = body.get("trace_id")
    return ErrorBody(
        code=code if isinstance(code, str) else None,
        message=body["message"][:MAX_TEXT],
        details=body.get("details"),
        trace_id=trace if isinstance(trace, str) and TRACE_ID.fullmatch(trace) else None,
    )


# --- identity and health ------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Whoami:
    key_id: str
    label: str | None
    scopes: tuple[str, ...]
    expires_at: datetime
    rate_limit_per_min: jsonnum.Num
    rate_limit_remaining: jsonnum.Num | None
    call_allowed: bool | None  # None when the response does not say
    tools: tuple[str, ...]
    call_remaining: int | None = None  # calls left in the allowance's period; None when the response does not say (no limit, or unread)


def read_whoami(raw: bytes) -> Whoami:
    """``GET /whoami``: which key this is, what it may read, when it expires and how many calls it may make."""
    body = load(raw)
    if not isinstance(body, dict):
        raise _drift("is not an object")
    key_id, scopes, tools = body.get("key_id"), body.get("scopes"), body.get("tools")
    if not isinstance(key_id, str) or not key_id:
        raise _drift("has no key id")
    if not (_text_list(scopes) and _text_list(tools)):
        raise _drift("has scopes or tools that are not lists of text")
    expires = body.get("expires_at")
    if not isinstance(expires, str):
        raise _drift("has no expiry time")
    try:
        expires_at = parse_rfc3339(expires)
    except TimeError as exc:
        raise _drift(f"has an expiry that is not an RFC 3339 time: {expires!r}") from exc
    per_minute, remaining = body.get("rate_limit_per_min"), body.get("rate_limit_remaining")
    if not isinstance(per_minute, jsonnum.Num):
        raise _drift("has a per-minute limit that is not a number")
    if remaining is not None and not isinstance(remaining, jsonnum.Num):
        raise _drift("has a remaining count that is not a number")
    label = body.get("label")
    allowance = body.get("call_allowance")
    allowed = allowance.get("allowed") if isinstance(allowance, dict) else None
    left = allowance.get("remaining") if isinstance(allowance, dict) else None
    remaining_calls: int | None = None
    if isinstance(left, jsonnum.Num):
        whole = Decimal(left.text)  # a Num is a JSON number, so it is always a Decimal
        if whole != whole.to_integral_value() or whole < 0:
            raise _drift(f"has a remaining call count that is not a whole number: {left.text}")
        remaining_calls = int(whole)
    return Whoami(
        key_id=key_id,
        label=label if isinstance(label, str) else None,
        scopes=tuple(scopes),
        expires_at=expires_at,
        rate_limit_per_min=per_minute,
        rate_limit_remaining=remaining,
        call_allowed=allowed if isinstance(allowed, bool) else None,
        tools=tuple(tools),
        call_remaining=remaining_calls,
    )


def read_health(raw: bytes) -> Mapping[str, Any]:
    """``GET /health``: ``status`` must be ``ok``. A health answer says nothing about the authenticated path (it answers in a second while
    authenticated reads take a minute or more)."""
    body = load(raw)
    if not isinstance(body, dict) or not isinstance(body.get("status"), str):
        raise _drift("has no status")
    if body["status"] != "ok":
        raise ConnectorError(ErrorClass.UPSTREAM, f"RYO's health reports {body['status'][:40]!r}")
    return body
