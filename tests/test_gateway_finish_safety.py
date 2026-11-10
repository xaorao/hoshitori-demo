"""An exception while a dispatch is being finished (the adapter cannot classify an answer, the capture cannot be stored) ends that
attempt as a failure and goes through the retry rules; it never leaves the call dispatched-and-forgotten, spends no token for nothing and
never raises into the loop that reports the exchange."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hoshitori import gateway
from tests.gateway_support import Harness, ok


def test_a_store_that_fails_once_costs_one_attempt_and_the_call_still_finishes(tmp_path: Path, monkeypatch: Any) -> None:
    h = Harness(gateway, tmp_path, limit=100)
    real = h.store.append
    seen = {"n": 0}

    def flaky(capture: Any) -> Any:
        seen["n"] += 1
        if seen["n"] == 1:
            raise OSError("disk full")
        return real(capture)

    monkeypatch.setattr(h.store, "append", flaky)
    call_id = h.submit()
    h.run(600)
    result = h.result(call_id)
    assert result is not None and result.outcome is gateway.Outcome.OK
    assert result.attempts == 2, "the failed store was an attempt of its own, taken from the retry budget"


def test_an_adapter_that_cannot_classify_an_answer_fails_the_call_in_order(tmp_path: Path, monkeypatch: Any) -> None:
    h = Harness(gateway, tmp_path, limit=100, script=lambda d: ok())

    def broken(capture: Any, now: Any) -> Any:
        raise ValueError("an answer the adapter cannot read")

    monkeypatch.setattr(h.adapter, "classify", broken)
    call_id = h.submit()
    h.run(3000)
    result = h.result(call_id)
    # an unclassified failure counts against the breaker, which opens after three of them in a row: the call ends, after three attempts
    assert result is not None and result.outcome in (gateway.Outcome.FAILED, gateway.Outcome.BREAKER_OPEN) and result.attempts == 3
    assert result.error is not None and result.error.error_class.value == "UNCLASSIFIED"
    assert h.gateway.providers["ryo"].in_flight == 0, "nothing is left in flight"
