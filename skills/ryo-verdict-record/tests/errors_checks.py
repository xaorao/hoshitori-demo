"""Checks of ``ryo_verdict_record.errors``, runnable against a mutant: the exact words and shape of an input error and of a source error."""

from __future__ import annotations

import types

REASONS = {
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
    out: list[str] = []
    err = mod.InputError("symbol", "must be short", {"a": 1})
    if not isinstance(err, ValueError) or str(err) != "symbol: must be short":
        out.append(f"InputError text is {str(err)!r}")
    if (err.field, err.detail, err.example, err.code, mod.InputError.code) != (
        "symbol",
        "must be short",
        {"a": 1},
        "INVALID_ARGUMENTS",
        "INVALID_ARGUMENTS",
    ):
        out.append("InputError members differ")
    want = {
        "code": "INVALID_ARGUMENTS",
        "message": "symbol: must be short",
        "details": {"field": "symbol", "example": {"a": 1}},
        "trace_id": "t1",
    }
    got = err.to_envelope("t1")
    if got != want or list(got) != list(want) or list(got["details"]) != ["field", "example"]:
        out.append(f"to_envelope with an example is {got}")
    bare = mod.InputError("x", "y").to_envelope("t2")
    if (
        bare != {"code": "INVALID_ARGUMENTS", "message": "x: y", "details": {"field": "x"}, "trace_id": "t2"}
        or mod.InputError("x", "y").example is not None
    ):
        out.append(f"to_envelope with no example is {bare}")
    empty = mod.InputError("arguments", "this tool takes none", {}).to_envelope("t3")
    if empty["details"] != {"field": "arguments", "example": {}}:
        out.append(f"an empty example is dropped: {empty['details']}")
    if mod.REASON_TEXT != REASONS or list(mod.REASON_TEXT) != list(REASONS):
        out.append("REASON_TEXT differs")
    for reason, text in REASONS.items():
        e = mod.SourceError(reason)
        if str(e) != text or (e.reason, e.http_status, e.retry_after_s) != (reason, None, None) or e.args != (text,):
            out.append(f"SourceError({reason}) is {str(e)!r} {(e.reason, e.http_status, e.retry_after_s)}")
    e = mod.SourceError("rate_limited", 429, 7.5)
    if (e.http_status, e.retry_after_s) != (429, 7.5) or isinstance(e, ValueError):
        out.append("SourceError keeps neither its status nor its retry-after")
    for bad in ("", "nope", "TIMEOUT", "timeout "):
        try:
            mod.SourceError(bad)
        except ValueError as exc:
            if str(exc) != f"unknown reason {bad!r}":
                out.append(f"SourceError({bad!r}) refused with {exc}")
        else:
            out.append(f"SourceError({bad!r}) was accepted")
    return out
