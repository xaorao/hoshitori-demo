"""A model call as a ledger record (``AGENT_PROTOCOL`` section 3: every call is a record).

The record says what was asked and what came back without holding either: the prompt version, the digest of the request and of the
arguments of this attempt, the digest of the stored answer, the model and parameters, the usage (the completion count already holds the
reasoning tokens), the latency, the finish reason and the outcome. The prompt and the answer stay in the private capture store; the
digests are what lets a reviewer holding both check that they are the ones this call was about.

The id is natural: a call that reached the model is identified by its capture, so a record written twice is one record. A call that
reached nothing (the gateway brought no answer, a replay had none, the budget was spent) has no capture and is identified by what it asked,
its attempt, its outcome and the moment it is recorded for. The reader is as strict as the writer. The text a call holds (the reported
model, the finish reason, the detail) is checked by the model client's own rule (``model_client.held``: valid canonical text of a bounded
length), which the client also reads an answer by, and the limit of a usage count is the client's (``model_client.MAX_COUNT``), so a call
the client made is one this record takes.
"""

from __future__ import annotations

import re
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.model_client import (
    FINISH_REASON,
    MAX_COUNT,
    MAX_DETAIL,
    MAX_FINISH_REASON,
    MAX_REPORTED_MODEL,
    REPORTED_MODEL,
    ModelCall,
    Outcome,
    Usage,
    held,
)
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339

TYPE: Final = "model_call"
TYPE_VERSION: Final = 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
_MEMBERS: Final = frozenset(
    {
        "stage", "prompt_version", "request_digest", "args_digest", "capture_id", "response_digest", "model", "reported_model",
        "max_tokens", "temperature", "seed", "response_format", "thinking", "attempt", "usage", "latency_ms", "finish_reason",
        "reasoning_chars", "outcome", "detail",
    }
)  # fmt: skip
_USAGE_MEMBERS: Final = frozenset({"prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "billed", "estimated"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CAPTURE = re.compile(r"cap-[0-9a-f]{32}")
_STAGE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_VERSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
_TEMPERATURE = re.compile(r"(?:[01](?:\.[0-9]{1,3})?|2(?:\.0{1,3})?)")
FORMATS: Final = (None, "json_object", "json_schema")
MAX_TOKENS: Final = 65_536


class ModelCallRecordError(ValueError):
    """A call that cannot be recorded, or a record that is not one."""


def _int(value: object, what: str, low: int = 0, high: int = MAX_COUNT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ModelCallRecordError(f"{what} is a whole number from {low} to {high}")
    return value


def _optional_int(value: object, what: str) -> int | None:
    return None if value is None else _int(value, what)


def _text(value: object, what: str, pattern: re.Pattern[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ModelCallRecordError(f"{what} is not in the form this record takes")


def check(call: ModelCall) -> None:
    """The call is one that can be recorded, or an error."""
    _text(call.stage, "the stage", _STAGE)
    _text(call.prompt_version, "the prompt version", _VERSION)
    _text(call.request_digest, "the request digest", _SHA256)
    _text(call.args_digest, "the arguments digest", _SHA256)
    _text(call.capture_id, "the capture id", _CAPTURE, optional=True)
    _text(call.response_digest, "the response digest", _SHA256, optional=True)
    if (call.capture_id is None) != (call.response_digest is None):
        raise ModelCallRecordError("a call that has a stored answer names it by id and by digest, and one that has none names neither")
    if not isinstance(call.model, str) or not 1 <= len(call.model) <= 100:
        raise ModelCallRecordError("the model is an id of 1 to 100 characters")
    if call.reported_model is not None and not held(call.reported_model, MAX_REPORTED_MODEL):
        raise ModelCallRecordError(REPORTED_MODEL)
    _int(call.max_tokens, "the token ceiling", 1, MAX_TOKENS)
    _text(call.temperature, "the temperature", _TEMPERATURE, optional=True)
    if call.seed is not None:
        _int(call.seed, "the seed", 0, 2**31 - 1)
    if call.response_format not in FORMATS:
        raise ModelCallRecordError("the response format is none, json_object or json_schema")
    if call.thinking is not None and not isinstance(call.thinking, bool):
        raise ModelCallRecordError("thinking is true, false or left to the provider")
    _int(call.attempt, "the attempt", 1, 2)
    if call.latency_ms is not None:
        _int(call.latency_ms, "the latency", 0, 10**9)
    if call.finish_reason is not None and not held(call.finish_reason, MAX_FINISH_REASON):
        raise ModelCallRecordError(FINISH_REASON)
    _int(call.reasoning_chars, "the length of the reasoning", 0, 10**9)
    if not isinstance(call.outcome, Outcome):
        raise ModelCallRecordError("the outcome is one of the outcomes")
    if call.detail is not None and not held(call.detail, MAX_DETAIL):
        raise ModelCallRecordError(f"the detail is valid canonical text of 1 to {MAX_DETAIL} characters")
    if call.usage is not None:
        u = call.usage
        for what, value in (
            ("prompt tokens", u.prompt_tokens),
            ("completion tokens", u.completion_tokens),
            ("reasoning tokens", u.reasoning_tokens),
            ("cached tokens", u.cached_tokens),
        ):
            _optional_int(value, f"the {what}")
        _int(u.billed, "the billed tokens")
        if not isinstance(u.estimated, bool) or u.estimated != (u.prompt_tokens is None or u.completion_tokens is None):
            raise ModelCallRecordError("usage is an estimate exactly when the provider did not give both counts")
        if not u.estimated and u.billed != cast(int, u.prompt_tokens) + cast(int, u.completion_tokens):
            raise ModelCallRecordError("billed tokens are the prompt count plus the completion count")
    if (call.usage is None) != (call.capture_id is None or call.outcome is Outcome.UNREADABLE):
        raise ModelCallRecordError("a call has usage exactly when its answer was read")


def record_id(call: ModelCall, created_at_ms: int) -> str:
    if call.capture_id is not None:
        return natural_id("mc", {"capture": call.capture_id})
    return natural_id("mc", {"args": call.args_digest, "attempt": call.attempt, "outcome": call.outcome.value, "at_ms": created_at_ms})


def _usage_body(usage: Usage | None) -> canonical.JsonValue:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cached_tokens": usage.cached_tokens,
        "billed": usage.billed,
        "estimated": usage.estimated,
    }


def to_record(call: ModelCall, source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger."""
    check(call)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise ModelCallRecordError("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": record_id(call, created_at_ms),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": {
            "stage": call.stage,
            "prompt_version": call.prompt_version,
            "request_digest": call.request_digest,
            "args_digest": call.args_digest,
            "capture_id": call.capture_id,
            "response_digest": call.response_digest,
            "model": call.model,
            "reported_model": call.reported_model,
            "max_tokens": call.max_tokens,
            "temperature": call.temperature,
            "seed": call.seed,
            "response_format": call.response_format,
            "thinking": call.thinking,
            "attempt": call.attempt,
            "usage": _usage_body(call.usage),
            "latency_ms": call.latency_ms,
            "finish_reason": call.finish_reason,
            "reasoning_chars": call.reasoning_chars,
            "outcome": call.outcome.value,
            "detail": call.detail,
        },
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise ModelCallRecordError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def _usage(raw: object) -> Usage | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _USAGE_MEMBERS:
        raise ModelCallRecordError("usage has the members of a usage record")
    estimated = raw["estimated"]
    if not isinstance(estimated, bool):
        raise ModelCallRecordError("usage says whether it is an estimate")
    return Usage(
        _optional_int(raw["prompt_tokens"], "the prompt tokens"),
        _optional_int(raw["completion_tokens"], "the completion tokens"),
        _optional_int(raw["reasoning_tokens"], "the reasoning tokens"),
        _optional_int(raw["cached_tokens"], "the cached tokens"),
        _int(raw["billed"], "the billed tokens"),
        estimated,
    )


def from_record(record: dict[str, canonical.JsonValue]) -> ModelCall:
    """The call a record holds, read as strictly as it was written. The record's time is the one its id was made with."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise ModelCallRecordError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise ModelCallRecordError("not a model call record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    if set(body) != _MEMBERS:
        raise ModelCallRecordError(f"the body has the members {sorted(_MEMBERS)}")
    try:
        outcome = Outcome(cast(str, body["outcome"]))
    except ValueError:
        raise ModelCallRecordError("the outcome is one of the outcomes") from None
    call = ModelCall(
        cast(str, body["stage"]),
        cast(str, body["prompt_version"]),
        cast(str, body["request_digest"]),
        cast(str, body["args_digest"]),
        cast(str | None, body["capture_id"]),
        cast(str | None, body["response_digest"]),
        cast(str, body["model"]),
        cast(str | None, body["reported_model"]),
        cast(int, body["max_tokens"]),
        cast(str | None, body["temperature"]),
        cast(int | None, body["seed"]),
        cast(str | None, body["response_format"]),
        cast(int, body["attempt"]),
        _usage(body["usage"]),
        cast(int | None, body["latency_ms"]),
        cast(str | None, body["finish_reason"]),
        cast(int, body["reasoning_chars"]),
        outcome,
        cast(str | None, body["detail"]),
        cast(bool | None, body["thinking"]),
    )
    check(call)
    if record["id"] != record_id(call, to_ms(parse_rfc3339(cast(str, record["created_at"])))):
        raise ModelCallRecordError("the id is not the id of what the record says")
    return call
