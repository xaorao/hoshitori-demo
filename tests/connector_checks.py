"""Checks of the shared connector vocabulary (``CONNECTOR_CONTRACTS`` section 2), runnable against the real module or a mutant."""

from __future__ import annotations

import types
from datetime import UTC, datetime

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)

STATUS_CLASS = {
    100: "UNCLASSIFIED",
    199: "UNCLASSIFIED",
    200: None,
    201: None,
    204: None,
    299: None,
    300: "UNCLASSIFIED",
    301: "UNCLASSIFIED",
    304: "UNCLASSIFIED",
    399: "UNCLASSIFIED",
    400: "INVALID_ARGS",
    401: "AUTH",
    402: "INVALID_ARGS",
    403: "AUTH",
    404: "NOT_FOUND",
    405: "INVALID_ARGS",
    408: "TIMEOUT",
    409: "INVALID_ARGS",
    418: "BANNED",
    422: "INVALID_ARGS",
    429: "RATE_LIMIT",
    451: "GEO_BLOCKED",
    499: "INVALID_ARGS",
    500: "UPSTREAM",
    502: "UPSTREAM",
    503: "UPSTREAM",
    504: "UPSTREAM",
    599: "UPSTREAM",
    600: "UNCLASSIFIED",
}

# (retry, counts toward a breaker, alarm) exactly as the specification's table states them
POLICY = {
    "RATE_LIMIT": ("AFTER_WINDOW", False, False),
    "BANNED": ("NEVER", True, False),
    "AUTH": ("NEVER", True, False),
    "GEO_BLOCKED": ("NEVER", True, False),
    "TIMEOUT": ("BACKOFF", True, False),
    "UPSTREAM": ("BACKOFF", True, False),
    "MAINTENANCE": ("NEVER", True, False),
    "INVALID_ARGS": ("NEVER", False, True),
    "NOT_FOUND": ("NEVER", False, False),
    "CONTRACT_DRIFT": ("NEVER", True, True),
    "MALFORMED": ("NEVER", True, True),
    "UNCLASSIFIED": ("BACKOFF", True, True),
}


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []

    classes = {c.value for c in mod.ErrorClass}
    if classes != set(POLICY):
        bad.append(f"the error classes are {sorted(classes)}, expected {sorted(POLICY)}")
    for name, (retry, breaker, alarm) in POLICY.items():
        got = mod.POLICY[mod.ErrorClass(name)]
        if (got.retry.value, got.breaker, got.alarm) != (retry, breaker, alarm):
            bad.append(f"policy of {name} is {(got.retry.value, got.breaker, got.alarm)}, expected {(retry, breaker, alarm)}")
    if set(mod.POLICY) != set(mod.ErrorClass):
        bad.append("a class has no policy")

    for status, expected in STATUS_CLASS.items():
        got = mod.classify_status(status)
        if (got.value if got is not None else None) != expected:
            bad.append(f"status {status} is {got}, expected {expected}")
    for status in range(100, 600):  # no status is ever left unclassified or crashes
        mod.classify_status(status)
    try:
        mod.classify_status(None)
        bad.append("a missing status was classified")
    except ValueError:
        pass

    ra = mod.parse_retry_after
    for text, expected in [
        ("5", 5.0),
        ("0", 0.0),
        ("  7 ", 7.0),
        ("60", 60.0),
        ("000000012", 12.0),
        ("259200", 259200.0),
        ("999999999", 259200.0),
        ("-1", None),
        ("5.5", None),
        ("", None),
        ("abc", None),
        ("1e3", None),
        ("5 s", None),
        ("1234567890", None),
        (None, None),
        ("+5", None),
        ("\uff15", None),
    ]:
        got = ra(text, NOW)
        if got != expected:
            bad.append(f"Retry-After {text!r} gives {got!r}, expected {expected!r}")
    for text, expected in [
        ("Thu, 29 Oct 2026 12:00:30 GMT", 30.0),
        ("Thu, 29 Oct 2026 11:00:00 GMT", 0.0),
        ("Thu, 29 Oct 2026 12:00:00 GMT", 0.0),
        ("Thu, 29 Oct 2026 13:00:00 +0100", 0.0),
        ("Thu, 29 Oct 2026 12:30:00 +0000", 1800.0),
        ("Mon, 01 Jan 2035 00:00:00 GMT", 259200.0),
        ("Thu, 29 Oct 2026 12:00:30", None),  # no zone: not believed
        ("not a date", None),
        ("Thu, 99 Oct 2026 12:00:30 GMT", None),
    ]:
        got = ra(text, NOW)
        if got != expected:
            bad.append(f"Retry-After date {text!r} gives {got!r}, expected {expected!r}")

    error = mod.ConnectorError(mod.ErrorClass.RATE_LIMIT, "slow down", retry_after_s=12.0, trace_id="t1")
    if (
        error.error_class is not mod.ErrorClass.RATE_LIMIT
        or error.retry_after_s != 12.0
        or error.trace_id != "t1"
        or error.message != "slow down"
    ):
        bad.append("a connector error loses its fields")
    if error.policy != mod.POLICY[mod.ErrorClass.RATE_LIMIT] or "RATE_LIMIT" not in str(error):
        bad.append("a connector error's policy or text is wrong")
    return bad
