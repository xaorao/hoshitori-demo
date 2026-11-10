"""Checks of ``exit_liquidity_check.errors``, runnable against a mutant: the fixed wording of every failure and the refusal envelope."""

from __future__ import annotations

import types

from tests.checks_support import Case

TEXT = {
    "timeout": "the source did not answer within the time budget",
    "rate_limited": "the source is rate limiting requests",
    "server_error": "the source returned a server error",
    "network_error": "the source could not be reached",
    "bad_response": "the source returned a reply that could not be used",
    "cooling_down": "the source is in a cool-down after rate limiting",
    "budget_exhausted": "the time budget for this call ran out",
    "tls_error": "this machine could not check the source's TLS certificate",
}


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("reason texts", mod.REASON_TEXT, TEXT)
    e = mod.InputError("size_usd", "must be a number", {"token": "SOL"})
    c.eq(
        "input error",
        (str(e), e.field, e.detail, e.example, e.code, isinstance(e, ValueError)),
        ("size_usd: must be a number", "size_usd", "must be a number", {"token": "SOL"}, "INVALID_ARGUMENTS", True),
    )
    c.eq(
        "envelope with an example",
        e.to_envelope("t1"),
        {
            "code": "INVALID_ARGUMENTS",
            "message": "size_usd: must be a number",
            "details": {"field": "size_usd", "example": {"token": "SOL"}},
            "trace_id": "t1",
        },
    )
    bare = mod.InputError("token", "is required")
    c.eq(
        "envelope without an example",
        bare.to_envelope("t2"),
        {"code": "INVALID_ARGUMENTS", "message": "token: is required", "details": {"field": "token"}, "trace_id": "t2"},
    )
    c.eq("an empty example is still an example", mod.InputError("a", "b", {}).to_envelope("t")["details"], {"field": "a", "example": {}})
    for reason, text in TEXT.items():
        s = mod.SourceError(reason)
        c.eq(f"source error {reason}", (str(s), s.reason, s.http_status, s.retry_after_s), (text, reason, None, None))
    s = mod.SourceError("rate_limited", 429, 2.5)
    c.eq("source error with a status and a wait", (s.reason, s.http_status, s.retry_after_s), ("rate_limited", 429, 2.5))
    c.raises("an unknown reason", lambda: mod.SourceError("nope"), ValueError, "unknown reason 'nope'")
    c.eq("a source error carries no upstream text", str(mod.SourceError("bad_response", 500)), TEXT["bad_response"])
    return c.failed
