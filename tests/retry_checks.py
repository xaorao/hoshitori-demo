"""Scenario checks of the retry policy, runnable against the real module or a mutant."""

from __future__ import annotations

import random
import types
from datetime import UTC, datetime, timedelta

from hoshitori.connector import ErrorClass as E

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class FixedRandom(random.Random):
    """A generator whose uniform draw is a chosen fraction of the range, so the jitter is exact."""

    def __init__(self, fraction: float) -> None:
        super().__init__(0)
        self.fraction = fraction
        self.ranges: list[tuple[float, float]] = []

    def uniform(self, a: float, b: float) -> float:
        self.ranges.append((a, b))
        return a + (b - a) * self.fraction


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []

    def check(label: str, got: object, expected: object) -> None:
        if got != expected:
            bad.append(f"{label}: {got!r}, expected {expected!r}")

    def retrier(deadline: float = 1000, *, rng: random.Random | None = None, min_attempt: float = 10, cap: float = 200, **policy: object):  # type: ignore[no-untyped-def]
        return mod.Retrier(mod.RetryPolicy(**policy), at(deadline), min_attempt, cap, rng or FixedRandom(1.0))  # type: ignore[arg-type]

    # --- starting a call -------------------------------------------------------------------------------------------------------------
    r = retrier(100, cap=60)
    step = r.first_attempt(at(0))
    check("the first attempt", (step.retry, step.attempt_timeout_s, r.attempts), (True, 60, 1))
    r = retrier(100, cap=200)
    check("a timeout never passes the deadline", r.first_attempt(at(0)).attempt_timeout_s, 100)
    r = retrier(100)
    check(
        "a call that cannot finish is not started",
        (r.first_attempt(at(95)).retry, r.first_attempt(at(95)).reason, r.attempts),
        (False, "DEADLINE", 0),
    )
    check("exactly the minimum is enough", (r.first_attempt(at(90)).retry, r.first_attempt(at(90)).attempt_timeout_s), (True, 10))
    check("one tick short is not", retrier(100).first_attempt(at(90.001)).retry, False)
    check("after the deadline", retrier(100).first_attempt(at(101)).retry, False)

    # --- classes that are never retried ------------------------------------------------------------------------------------------------
    for cls in (E.BANNED, E.AUTH, E.GEO_BLOCKED, E.MAINTENANCE, E.INVALID_ARGS, E.NOT_FOUND, E.CONTRACT_DRIFT, E.MALFORMED):
        r = retrier()
        r.first_attempt(at(0))
        step = r.after_failure(at(1), cls)
        check(f"{cls.value} is not retried", (step.retry, step.reason), (False, "NOT_RETRYABLE"))

    # --- backoff with full jitter -----------------------------------------------------------------------------------------------------------
    rng = FixedRandom(1.0)  # the longest wait the jitter allows
    r = retrier(rng=rng)
    r.first_attempt(at(0))
    waits = []
    for i in range(5):
        step = r.after_failure(at(10 + i), E.UPSTREAM)
        waits.append(step.wait_s)
    check("exponential up to the cap", waits, [0.5, 1.0, 2.0, 4.0, 8.0])
    check("the draw is over the whole range from zero", rng.ranges[0], (0.0, 0.5))
    step = r.after_failure(at(30), E.UPSTREAM)
    check("a sixth upstream retry is refused", (step.retry, step.reason), (False, "EXHAUSTED"))
    rng = FixedRandom(0.0)
    r = retrier(rng=rng)
    r.first_attempt(at(0))
    check("the shortest wait is zero", r.after_failure(at(1), E.UPSTREAM).wait_s, 0.0)
    rng = FixedRandom(0.5)
    r = retrier(rng=rng, cap_s=3.0)
    r.first_attempt(at(0))
    got = [r.after_failure(at(1 + i), E.UPSTREAM).wait_s for i in range(4)]
    check("jitter scales the range and the cap applies", got, [0.25, 0.5, 1.0, 1.5])
    r = retrier(rng=FixedRandom(1.0), base_s=2.0, factor=3.0, cap_s=100.0)
    r.first_attempt(at(0))
    check("base and factor are the policy's", [r.after_failure(at(1 + i), E.UPSTREAM).wait_s for i in range(3)], [2.0, 6.0, 18.0])

    # --- Retry-After raises the wait, never lowers it ------------------------------------------------------------------------------------
    r = retrier(rng=FixedRandom(1.0))
    r.first_attempt(at(0))
    check("the provider's wait wins when longer", r.after_failure(at(1), E.UPSTREAM, 5.0).wait_s, 5.0)
    check("the backoff wins when longer", retrier(rng=FixedRandom(1.0)).after_failure(at(1), E.UPSTREAM, 0.1).wait_s, 0.5)
    check("a zero Retry-After changes nothing", retrier(rng=FixedRandom(1.0)).after_failure(at(1), E.UPSTREAM, 0.0).wait_s, 0.5)

    check(
        "after a timeout the provider's wait wins when longer",
        retrier(rng=FixedRandom(1.0)).after_failure(at(1), E.TIMEOUT, 5.0).wait_s,
        5.0,
    )
    check("and the backoff when longer", retrier(rng=FixedRandom(1.0)).after_failure(at(1), E.TIMEOUT, 0.1).wait_s, 0.5)

    # --- timeouts: twice at most --------------------------------------------------------------------------------------------------------------
    r = retrier(5000, cap=100)
    r.first_attempt(at(0))
    s1 = r.after_failure(at(100), E.TIMEOUT)
    s2 = r.after_failure(at(300), E.TIMEOUT)
    s3 = r.after_failure(at(500), E.TIMEOUT)
    check("two timeout retries", (s1.retry, s2.retry, s3.retry, s3.reason), (True, True, False, "EXHAUSTED"))
    check("a timeout is retried after a backoff", s1.wait_s, 0.5)
    check("each attempt gets the operation's timeout", (s1.attempt_timeout_s, s2.attempt_timeout_s), (100, 100))
    r = retrier(5000, timeout_retries=0)
    check("no timeout retries when the policy says none", r.after_failure(at(1), E.TIMEOUT).retry, False)
    r = retrier(5000)
    r.after_failure(at(1), E.TIMEOUT)
    r.after_failure(at(2), E.TIMEOUT)
    check("timeouts do not use up upstream retries", r.after_failure(at(3), E.UPSTREAM).retry, True)
    r = retrier(5000, backoff_retries=1)
    r.after_failure(at(1), E.UPSTREAM)
    check(
        "upstream retries do not use up timeout retries",
        (r.after_failure(at(2), E.UPSTREAM).retry, r.after_failure(at(3), E.TIMEOUT).retry),
        (False, True),
    )
    r = retrier(5000, backoff_retries=1)
    r.after_failure(at(1), E.UPSTREAM)
    check("unclassified failures share the upstream allowance", r.after_failure(at(2), E.UNCLASSIFIED).retry, False)

    # --- back-pressure: once, after the stated wait ------------------------------------------------------------------------------------------------
    r = retrier(5000)
    r.first_attempt(at(0))
    step = r.after_failure(at(1), E.RATE_LIMIT, 30.0)
    check("a rate limit is retried after the stated wait", (step.retry, step.wait_s), (True, 30.0))
    check(
        "not twice",
        (r.after_failure(at(40), E.RATE_LIMIT, 30.0).retry, r.after_failure(at(40), E.RATE_LIMIT, 30.0).reason),
        (False, "EXHAUSTED"),
    )
    check("with no stated wait, the default", retrier(5000).after_failure(at(1), E.RATE_LIMIT).wait_s, 60.0)
    check("a zero wait is a zero wait", retrier(5000).after_failure(at(1), E.RATE_LIMIT, 0.0).wait_s, 0.0)
    check("a negative wait is no wait", retrier(5000).after_failure(at(1), E.RATE_LIMIT, -3.0).wait_s, 0.0)
    check("the default is the policy's", retrier(5000, rate_limit_default_wait_s=7.0).after_failure(at(1), E.RATE_LIMIT).wait_s, 7.0)
    r = retrier(5000, rate_limit_retries=2)
    check(
        "the policy may allow more",
        (
            r.after_failure(at(1), E.RATE_LIMIT, 1.0).retry,
            r.after_failure(at(5), E.RATE_LIMIT, 1.0).retry,
            r.after_failure(at(9), E.RATE_LIMIT, 1.0).retry,
        ),
        (True, True, False),
    )
    check("a rate limit does not use the backoff", retrier(5000, rng=FixedRandom(1.0)).after_failure(at(1), E.RATE_LIMIT, 2.0).wait_s, 2.0)
    r = retrier(5000, rng=FixedRandom(1.0))
    r.after_failure(at(1), E.RATE_LIMIT, 2.0)
    check("a rate limit retry counts toward the backoff size", r.after_failure(at(5), E.UPSTREAM).wait_s, 1.0)

    # --- the deadline stops retries --------------------------------------------------------------------------------------------------------------------
    r = retrier(300, cap=161, min_attempt=60, rng=FixedRandom(1.0))
    r.first_attempt(at(0))
    first = r.after_failure(at(161), E.TIMEOUT)  # 139 s left: enough for one more attempt of at least 60 s
    check("a retry that can still run", (first.retry, first.attempt_timeout_s), (True, 300 - 161 - 0.5))
    second = r.after_failure(at(240), E.TIMEOUT)  # the next wait is 1 s, so 59 s would remain: less than a useful attempt
    check("a second retry would not fit", (second.retry, second.reason), (False, "DEADLINE"))
    r = retrier(100, min_attempt=10)
    check(
        "a wait that eats the deadline",
        (r.after_failure(at(95), E.UPSTREAM).retry, r.after_failure(at(95), E.UPSTREAM).reason),
        (False, "DEADLINE"),
    )
    r = retrier(100, min_attempt=10, rng=FixedRandom(1.0))
    step = r.after_failure(at(89.5), E.UPSTREAM)
    check("exactly enough time after the wait", (step.retry, step.attempt_timeout_s), (True, 10))
    r = retrier(100, min_attempt=10, rng=FixedRandom(1.0))
    check("one tick too little after the wait", r.after_failure(at(89.501), E.UPSTREAM).retry, False)
    r = retrier(100, min_attempt=10)
    check("a rate-limit wait longer than the deadline ends the call", r.after_failure(at(1), E.RATE_LIMIT, 200.0).reason, "DEADLINE")
    r = retrier(1000, min_attempt=10)
    r.after_failure(at(1), E.RATE_LIMIT, 200.0)
    check("an exhausted retry does not move the state", r.attempts, 1)
    check("the attempt count", retrier(5000).first_attempt(at(0)).retry and retrier(5000).after_failure(at(1), E.UPSTREAM).retry, True)
    r = retrier(5000)
    r.first_attempt(at(0))
    r.after_failure(at(1), E.UPSTREAM)
    r.after_failure(at(2), E.UPSTREAM)
    check("attempts and waits are recorded", (r.attempts, r.retries, len(r.waits)), (3, 2, 2))
    r = retrier(100, min_attempt=10)
    r.after_failure(at(95), E.UPSTREAM)
    check("a refused retry records nothing", (r.attempts, r.retries, r.waits), (0, 0, []))

    # --- validation -----------------------------------------------------------------------------------------------------------------------------
    for label, build in [
        ("a zero base", lambda: mod.RetryPolicy(base_s=0)),
        ("a factor below one", lambda: mod.RetryPolicy(factor=0.5)),
        ("a cap below the base", lambda: mod.RetryPolicy(base_s=2, cap_s=1)),
        ("negative timeout retries", lambda: mod.RetryPolicy(timeout_retries=-1)),
        ("negative backoff retries", lambda: mod.RetryPolicy(backoff_retries=-1)),
        ("negative rate-limit retries", lambda: mod.RetryPolicy(rate_limit_retries=-1)),
        ("a negative default wait", lambda: mod.RetryPolicy(rate_limit_default_wait_s=-1)),
        ("a zero minimum attempt", lambda: retrier(min_attempt=0)),
        ("a cap below the minimum", lambda: retrier(min_attempt=10, cap=5)),
        ("a naive deadline", lambda: mod.Retrier(mod.RetryPolicy(), T0.replace(tzinfo=None), 1, 2, random.Random(0))),
    ]:
        try:
            build()
            bad.append(f"{label}: accepted")
        except (mod.RetryError, ValueError):
            pass
    return bad
