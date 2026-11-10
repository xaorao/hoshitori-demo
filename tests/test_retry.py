"""The retry policy: scenarios, a property test of the deadline, and mutants."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import retry
from hoshitori.connector import ErrorClass
from tests.mutation import mutant
from tests.retry_checks import failures

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def test_real_module_passes_every_scenario() -> None:
    assert failures(retry) == []


@settings(max_examples=300, deadline=None)
@given(
    st.integers(0, 2**32),
    st.floats(20, 2000),
    st.lists(
        st.tuples(st.sampled_from(list(ErrorClass)), st.floats(0, 200), st.one_of(st.none(), st.floats(0, 100))), min_size=1, max_size=12
    ),
)
def test_a_call_never_runs_past_its_deadline(seed: int, window: float, outcomes: list) -> None:  # type: ignore[type-arg]
    deadline = T0 + timedelta(seconds=window)
    r = retry.Retrier(retry.RetryPolicy(), deadline, 10.0, 60.0, random.Random(seed))
    now = T0
    step = r.first_attempt(now)
    for error_class, took, retry_after in outcomes:
        if not step.retry:
            break
        assert step.attempt_timeout_s >= 10.0 and step.attempt_timeout_s <= 60.0
        start = now + timedelta(seconds=step.wait_s)
        assert start + timedelta(seconds=step.attempt_timeout_s) <= deadline + timedelta(milliseconds=1)
        now = start + timedelta(seconds=min(took, step.attempt_timeout_s))
        step = r.after_failure(now, error_class, retry_after)
    # nothing is retried that must not be, and the counters never exceed the policy
    assert (
        r.timed_out <= r.policy.timeout_retries
        and r.backed_off <= r.policy.backoff_retries
        and r.rate_limited <= r.policy.rate_limit_retries
    )
    assert r.attempts == 1 + r.retries or r.attempts == 0


MUTATIONS: dict[str, tuple[str, str]] = {
    # configuration
    "a zero base is allowed": (
        "if self.base_s <= 0 or self.factor < 1 or self.cap_s < self.base_s:",
        "if self.factor < 1 or self.cap_s < self.base_s:",
    ),
    "a shrinking factor is allowed": (
        "if self.base_s <= 0 or self.factor < 1 or self.cap_s < self.base_s:",
        "if self.base_s <= 0 or self.cap_s < self.base_s:",
    ),
    "a cap below the base is allowed": (
        "if self.base_s <= 0 or self.factor < 1 or self.cap_s < self.base_s:",
        "if self.base_s <= 0 or self.factor < 1:",
    ),
    "negative timeout retries are allowed": (
        "if min(self.timeout_retries, self.backoff_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:",
        "if min(self.backoff_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:",
    ),
    "negative backoff retries are allowed": (
        "if min(self.timeout_retries, self.backoff_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:",
        "if min(self.timeout_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:",
    ),
    "negative rate-limit retries are allowed": (
        "if min(self.timeout_retries, self.backoff_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:",
        "if min(self.timeout_retries, self.backoff_retries) < 0 or self.rate_limit_default_wait_s < 0:",
    ),
    "a negative default wait is allowed": ("or self.rate_limit_default_wait_s < 0:", ":"),
    "a zero minimum attempt is allowed": (
        "        if self.min_attempt_s <= 0 or self.attempt_cap_s < self.min_attempt_s:",
        "        if self.attempt_cap_s < self.min_attempt_s:",
    ),
    "a cap below the minimum is allowed": (
        "        if self.min_attempt_s <= 0 or self.attempt_cap_s < self.min_attempt_s:",
        "        if self.min_attempt_s <= 0:",
    ),
    "a naive deadline is allowed": ("        self.deadline = ensure_utc(self.deadline)\n", ""),
    # starting
    "a call is started past its deadline": (
        '        timeout = self._timeout_at(ensure_utc(now))\n        if timeout is None:\n            return Step(False, "DEADLINE")\n        self.attempts += 1\n        return Step(True, "RETRY", 0.0, timeout)',
        '        timeout = self._timeout_at(ensure_utc(now)) or 1.0\n        self.attempts += 1\n        return Step(True, "RETRY", 0.0, timeout)',
    ),
    "the first attempt is not counted": (
        '        self.attempts += 1\n        return Step(True, "RETRY", 0.0, timeout)',
        '        return Step(True, "RETRY", 0.0, timeout)',
    ),
    "a call may start with too little time": (
        "        if remaining < self.min_attempt_s:\n            return None",
        "        if remaining <= 0:\n            return None",
    ),
    "a call needs one tick more than the minimum": (
        "        if remaining < self.min_attempt_s:\n            return None",
        "        if remaining <= self.min_attempt_s:\n            return None",
    ),
    "an attempt may run past the deadline": ("        return min(self.attempt_cap_s, remaining)", "        return self.attempt_cap_s"),
    "an attempt is cut to the remaining time only": ("        return min(self.attempt_cap_s, remaining)", "        return remaining"),
    # classes
    "nothing is protected from retry": ('        if kind is Retry.NEVER:\n            return Step(False, "NOT_RETRYABLE")\n', ""),
    "retries are attempted for every class": ("        kind = POLICY[error_class].retry\n", "        kind = Retry.BACKOFF\n"),
    # backoff
    "the backoff has no jitter": ("        return self.rng.uniform(0.0, ceiling)", "        return ceiling"),
    "the jitter has a floor": ("        return self.rng.uniform(0.0, ceiling)", "        return self.rng.uniform(ceiling / 2, ceiling)"),
    "the backoff does not grow": ("self.policy.base_s * self.policy.factor**self.retries", "self.policy.base_s"),
    "the backoff grows from the second retry": (
        "self.policy.base_s * self.policy.factor**self.retries",
        "self.policy.base_s * self.policy.factor ** (self.retries + 1)",
    ),
    "the backoff has no cap": (
        "        ceiling = min(self.policy.cap_s, self.policy.base_s * self.policy.factor**self.retries)",
        "        ceiling = self.policy.base_s * self.policy.factor**self.retries",
    ),
    "the backoff ignores the base": ("self.policy.base_s * self.policy.factor**self.retries", "self.policy.factor**self.retries"),
    "the backoff ignores the factor": ("self.policy.base_s * self.policy.factor**self.retries", "self.policy.base_s * 2**self.retries"),
    "the backoff ignores the policy's cap": ("min(self.policy.cap_s, self.policy.base_s", "min(8.0, self.policy.base_s"),
    "Retry-After lowers the wait": (
        "            wait = max(self._backoff(), retry_after_s or 0.0)\n        else:",
        "            wait = retry_after_s if retry_after_s is not None else self._backoff()\n        else:",
    ),
    "Retry-After is ignored after upstream failures": (
        '        else:\n            if self.backed_off >= self.policy.backoff_retries:\n                return Step(False, "EXHAUSTED")\n            wait = max(self._backoff(), retry_after_s or 0.0)',
        '        else:\n            if self.backed_off >= self.policy.backoff_retries:\n                return Step(False, "EXHAUSTED")\n            wait = self._backoff()',
    ),
    "Retry-After is ignored after timeouts": (
        '                return Step(False, "EXHAUSTED")\n            wait = max(self._backoff(), retry_after_s or 0.0)\n        else:',
        '                return Step(False, "EXHAUSTED")\n            wait = self._backoff()\n        else:',
    ),
    "the backoff replaces Retry-After": (
        '            if self.backed_off >= self.policy.backoff_retries:\n                return Step(False, "EXHAUSTED")\n            wait = max(self._backoff(), retry_after_s or 0.0)',
        '            if self.backed_off >= self.policy.backoff_retries:\n                return Step(False, "EXHAUSTED")\n            wait = min(self._backoff(), retry_after_s or 0.0)',
    ),
    # limits
    "timeouts are retried without limit": (
        '            if self.timed_out >= self.policy.timeout_retries:\n                return Step(False, "EXHAUSTED")\n',
        "",
    ),
    "timeouts are retried once too many": (
        "            if self.timed_out >= self.policy.timeout_retries:",
        "            if self.timed_out > self.policy.timeout_retries:",
    ),
    "timeouts are retried once too few": (
        "            if self.timed_out >= self.policy.timeout_retries:",
        "            if self.timed_out >= self.policy.timeout_retries - 1:",
    ),
    "upstream failures are retried without limit": (
        '            if self.backed_off >= self.policy.backoff_retries:\n                return Step(False, "EXHAUSTED")\n',
        "",
    ),
    "upstream failures are retried once too many": (
        "            if self.backed_off >= self.policy.backoff_retries:",
        "            if self.backed_off > self.policy.backoff_retries:",
    ),
    "rate limits are retried without limit": (
        '            if self.rate_limited >= self.policy.rate_limit_retries:\n                return Step(False, "EXHAUSTED")\n',
        "",
    ),
    "rate limits are retried once too many": (
        "            if self.rate_limited >= self.policy.rate_limit_retries:",
        "            if self.rate_limited > self.policy.rate_limit_retries:",
    ),
    "timeouts use the upstream allowance": (
        "        elif error_class is ErrorClass.TIMEOUT:\n            self.timed_out += 1\n        else:\n            self.backed_off += 1",
        "        else:\n            self.backed_off += 1",
    ),
    "upstream failures use the timeout allowance": (
        "        elif error_class is ErrorClass.TIMEOUT:\n            self.timed_out += 1\n        else:\n            self.backed_off += 1",
        "        else:\n            self.timed_out += 1",
    ),
    "rate limits use the upstream allowance": (
        "        if kind is Retry.AFTER_WINDOW:\n            self.rate_limited += 1\n",
        "        if False:\n            self.rate_limited += 1\n",
    ),
    "a rate limit retry is not counted as a retry": (
        "        self.attempts += 1\n        self.retries += 1\n        if kind",
        "        self.attempts += 1\n        if kind",
    ),
    "a retry is not counted as an attempt": (
        "        self.attempts += 1\n        self.retries += 1\n        if kind",
        "        self.retries += 1\n        if kind",
    ),
    "a wait is not recorded": ("        self.waits.append(wait)\n", ""),
    # back-pressure
    "a rate limit waits for the backoff": (
        "            wait = max(0.0, retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s)",
        "            wait = self._backoff()",
    ),
    "a rate limit without a stated wait does not wait": (
        "retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s",
        "retry_after_s if retry_after_s is not None else 0.0",
    ),
    "a rate limit ignores the policy's default": (
        "retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s",
        "retry_after_s if retry_after_s is not None else 60.0",
    ),
    "a negative wait is believed": (
        "            wait = max(0.0, retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s)",
        "            wait = retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s",
    ),
    "a stated wait of zero is replaced by the default": (
        "retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s",
        "retry_after_s or self.policy.rate_limit_default_wait_s",
    ),
    # the deadline
    "a retry is planned without looking at the deadline": (
        '        timeout = self._timeout_at(now + timedelta(seconds=wait))\n        if timeout is None:\n            return Step(False, "DEADLINE")\n',
        "        timeout = self._timeout_at(now + timedelta(seconds=wait)) or self.attempt_cap_s\n",
    ),
    "the wait is left out of the deadline": (
        "        timeout = self._timeout_at(now + timedelta(seconds=wait))",
        "        timeout = self._timeout_at(now)",
    ),
    "the deadline is judged from the wrong time": (
        "        timeout = self._timeout_at(now + timedelta(seconds=wait))",
        "        timeout = self._timeout_at(self.deadline - timedelta(seconds=self.min_attempt_s))",
    ),
    "a refused retry is still counted": (
        '        timeout = self._timeout_at(now + timedelta(seconds=wait))\n        if timeout is None:\n            return Step(False, "DEADLINE")\n        self.attempts += 1',
        '        self.attempts += 1\n        timeout = self._timeout_at(now + timedelta(seconds=wait))\n        if timeout is None:\n            return Step(False, "DEADLINE")\n        self.attempts += 0',
    ),
    "a step does not carry its timeout": (
        '        return Step(True, "RETRY", wait, timeout)',
        '        return Step(True, "RETRY", wait, self.attempt_cap_s)',
    ),
    "a step does not carry its wait": (
        '        return Step(True, "RETRY", wait, timeout)',
        '        return Step(True, "RETRY", 0.0, timeout)',
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.retry", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
