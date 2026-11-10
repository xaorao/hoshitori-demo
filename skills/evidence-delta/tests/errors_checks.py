"""Checks of ``evidence_delta.errors``, runnable against a mutant: the fixed wording of every failure."""

from __future__ import annotations

import types

from tests.checks_support import Case


def check_input_error(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    e = mod.InputError("f", "is bad", {"x": 1})
    c.eq("is a ValueError", isinstance(e, ValueError), True)
    c.eq("str", str(e), "f: is bad")
    c.eq("attributes", (e.field, e.detail, e.example, e.code), ("f", "is bad", {"x": 1}, "INVALID_ARGUMENTS"))
    c.eq(
        "envelope",
        e.to_envelope("T"),
        {"code": "INVALID_ARGUMENTS", "message": "f: is bad", "details": {"field": "f", "example": {"x": 1}}, "trace_id": "T"},
    )
    bare = mod.InputError("g", "no example")
    c.eq(
        "no example",
        bare.to_envelope("U"),
        {"code": "INVALID_ARGUMENTS", "message": "g: no example", "details": {"field": "g"}, "trace_id": "U"},
    )
    empty = mod.InputError("h", "empty example", {})
    c.eq("an empty example is still given", empty.to_envelope("V")["details"], {"field": "h", "example": {}})
    return c.bad


def check_source_error(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "reason text",
        mod.REASON_TEXT,
        {
            "timeout": "the source did not answer within the time budget",
            "rate_limited": "the source is rate limiting requests",
            "server_error": "the source returned a server error",
            "network_error": "the source could not be reached",
            "bad_response": "the source returned a reply that could not be used",
            "cooling_down": "the source is in a cool-down after rate limiting",
            "budget_exhausted": "the time budget for this call ran out",
            "tls_error": "this machine could not check the source's TLS certificate",
        },
    )
    e = mod.SourceError("timeout")
    c.eq(
        "defaults",
        (str(e), e.reason, e.http_status, e.retry_after_s),
        ("the source did not answer within the time budget", "timeout", None, None),
    )
    e = mod.SourceError("rate_limited", 429, 2.5)
    c.eq("given", (str(e), e.reason, e.http_status, e.retry_after_s), ("the source is rate limiting requests", "rate_limited", 429, 2.5))
    c.eq("is an Exception, not a ValueError", (isinstance(e, Exception), isinstance(e, ValueError)), (True, False))

    def make() -> object:
        return mod.SourceError("nope")

    try:
        make()
        c.bad.append("an unknown reason did not raise")
    except ValueError as exc:
        c.eq("an unknown reason", (type(exc).__name__, str(exc)), ("ValueError", "unknown reason 'nope'"))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_input_error, check_source_error):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
