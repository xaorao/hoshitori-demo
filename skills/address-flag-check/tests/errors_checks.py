"""Checks of ``address_flag_check.errors``, runnable against a mutant: the refusal envelope and the closed set of failure reasons (D3 4.4)."""

from __future__ import annotations

import types

from tests.checks_support import Case


def check(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    e = mod.InputError("chain", "must be one of ['ethereum']", {"address": "0x0", "chain": "ethereum"})
    c.eq("message", str(e), "chain: must be one of ['ethereum']")
    c.eq("field and detail", (e.field, e.detail, e.code), ("chain", "must be one of ['ethereum']", "INVALID_ARGUMENTS"))
    c.eq(
        "envelope",
        e.to_envelope("t1"),
        {
            "code": "INVALID_ARGUMENTS",
            "message": "chain: must be one of ['ethereum']",
            "details": {"field": "chain", "example": {"address": "0x0", "chain": "ethereum"}},
            "trace_id": "t1",
        },
    )
    c.eq("envelope without an example", mod.InputError("address", "is required").to_envelope("t2")["details"], {"field": "address"})
    c.true("a refusal is a ValueError", isinstance(e, ValueError))
    c.eq(
        "the closed set of reasons with their fixed text",
        mod.REASON_TEXT,
        {
            "timeout": "the source did not answer within the time budget",
            "rate_limited": "the source is rate limiting requests",
            "server_error": "the source returned a server error",
            "network_error": "the source could not be reached",
            "bad_response": "the source returned a reply that could not be used",
            "list_not_loaded": "no copy of the list has been loaded",
            "cooling_down": "the source is in a cool-down after rate limiting",
            "budget_exhausted": "the time budget for this call ran out",
            "tls_error": "this machine could not check the source's TLS certificate",
        },
    )
    s = mod.SourceError("rate_limited", 429, 2.0)
    c.eq(
        "a source error carries its fixed text only",
        (str(s), s.reason, s.http_status, s.retry_after_s),
        ("the source is rate limiting requests", "rate_limited", 429, 2.0),
    )
    c.eq("defaults", (mod.SourceError("timeout").http_status, mod.SourceError("timeout").retry_after_s), (None, None))
    c.raises("an unknown reason", lambda: mod.SourceError("vibes"), ValueError, "unknown reason 'vibes'")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    try:
        return check(mod)
    except Exception as exc:
        return [f"check raised {type(exc).__name__}: {exc}"]
