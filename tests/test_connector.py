"""The shared error taxonomy and status classification, plus mutants."""

from __future__ import annotations

import pytest

from hoshitori import connector
from tests.connector_checks import failures
from tests.mutation import mutant

MUTATIONS = {
    "429 is not rate limiting": (
        "    if status == 429:\n        return ErrorClass.RATE_LIMIT",
        "    if status == 429:\n        return ErrorClass.UPSTREAM",
    ),
    "418 is not a ban": (
        "    if status == 418:\n        return ErrorClass.BANNED",
        "    if status == 418:\n        return ErrorClass.AUTH",
    ),
    "403 is not an authentication failure": ("    if status in (401, 403):", "    if status == 401:"),
    "401 is not an authentication failure": ("    if status in (401, 403):", "    if status == 403:"),
    "451 is not a geographic block": (
        "    if status == 451:\n        return ErrorClass.GEO_BLOCKED",
        "    if status == 451:\n        return ErrorClass.AUTH",
    ),
    "404 is not a missing thing": (
        "    if status == 404:\n        return ErrorClass.NOT_FOUND",
        "    if status == 404:\n        return ErrorClass.INVALID_ARGS",
    ),
    "408 is not a timeout": ("    if status == 408:", "    if status == 0:"),
    "other 4xx are not our bug": (
        "    if 400 <= status < 500:\n        return ErrorClass.INVALID_ARGS",
        "    if 400 <= status < 500:\n        return ErrorClass.UNCLASSIFIED",
    ),
    "5xx are not upstream failures": (
        "    if 500 <= status < 600:\n        return ErrorClass.UPSTREAM",
        "    if 500 <= status < 600:\n        return ErrorClass.UNCLASSIFIED",
    ),
    "a redirect is read as success": (
        "    if 200 <= status < 300:\n        return None",
        "    if 200 <= status < 400:\n        return None",
    ),
    "the success range starts too high": ("    if 200 <= status < 300:", "    if 201 <= status < 300:"),
    "the success range ends too high": ("    if 200 <= status < 300:", "    if 200 <= status < 301:"),
    "a missing status is read as success": (
        '    if status is None:\n        raise ValueError("no HTTP status: classify the transport failure instead")',
        "    if status is None:\n        return None",
    ),
    "rate limiting trips the breaker": (
        "ErrorClass.RATE_LIMIT: Policy(Retry.AFTER_WINDOW, breaker=False)",
        "ErrorClass.RATE_LIMIT: Policy(Retry.AFTER_WINDOW, breaker=True)",
    ),
    "a ban is retried": ("ErrorClass.BANNED: Policy(Retry.NEVER, breaker=True)", "ErrorClass.BANNED: Policy(Retry.BACKOFF, breaker=True)"),
    "bad arguments are retried": (
        "ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=False, alarm=True)",
        "ErrorClass.INVALID_ARGS: Policy(Retry.BACKOFF, breaker=False, alarm=True)",
    ),
    "bad arguments trip the breaker": (
        "ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=False, alarm=True)",
        "ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=True, alarm=True)",
    ),
    "bad arguments raise no alarm": (
        "ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=False, alarm=True)",
        "ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=False)",
    ),
    "a timeout is not retried": (
        "ErrorClass.TIMEOUT: Policy(Retry.BACKOFF, breaker=True)",
        "ErrorClass.TIMEOUT: Policy(Retry.NEVER, breaker=True)",
    ),
    "an unclassified failure is not retried": (
        "ErrorClass.UNCLASSIFIED: Policy(Retry.BACKOFF, breaker=True, alarm=True)",
        "ErrorClass.UNCLASSIFIED: Policy(Retry.NEVER, breaker=True, alarm=True)",
    ),
    "an unclassified failure raises no alarm": (
        "ErrorClass.UNCLASSIFIED: Policy(Retry.BACKOFF, breaker=True, alarm=True)",
        "ErrorClass.UNCLASSIFIED: Policy(Retry.BACKOFF, breaker=True)",
    ),
    "a missing thing trips the breaker": (
        "ErrorClass.NOT_FOUND: Policy(Retry.NEVER, breaker=False)",
        "ErrorClass.NOT_FOUND: Policy(Retry.NEVER, breaker=True)",
    ),
    "drift raises no alarm": (
        "ErrorClass.CONTRACT_DRIFT: Policy(Retry.NEVER, breaker=True, alarm=True)",
        "ErrorClass.CONTRACT_DRIFT: Policy(Retry.NEVER, breaker=True)",
    ),
    "a malformed body is retried": (
        "ErrorClass.MALFORMED: Policy(Retry.NEVER, breaker=True, alarm=True)",
        "ErrorClass.MALFORMED: Policy(Retry.BACKOFF, breaker=True, alarm=True)",
    ),
    "Retry-After seconds are not capped": ("return float(min(int(text), MAX_RETRY_AFTER_S))", "return float(int(text))"),
    "Retry-After dates are not capped": (
        "return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))",
        "return float(max((moment - now).total_seconds(), 0.0))",
    ),
    "a date in the past asks for a negative wait": (
        "return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))",
        "return float(min((moment - now).total_seconds(), MAX_RETRY_AFTER_S))",
    ),
    "a Retry-After date without a zone is believed": ("    if moment.tzinfo is None:\n        return None\n", ""),
    "decimal Retry-After values are read": (
        '_DELTA_SECONDS: Final = re.compile(r"[0-9]{1,9}")',
        '_DELTA_SECONDS: Final = re.compile(r"[0-9.]{1,9}")',
    ),
    "Retry-After is not stripped": ("    text = value.strip()", "    text = value"),
    "a missing Retry-After asks for a wait": ("    if value is None:\n        return None", "    if value is None:\n        return 0.0"),
}


def test_real_module_passes() -> None:
    assert failures(connector) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.connector", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
