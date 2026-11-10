"""The adversarial set in one place: each attack on the record, run through the real code path by the checks that own it.

Each case names what is attacked and what must happen; the check it calls is the module's own (its scenarios are worked out by hand and
its code is swept), so a pass here means that check passes on the real module now. ``docs/ADVERSARIAL.md`` lists the cases with what each
proves and what it does not.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hoshitori import (
    claim_extract,
    deployment,
    forgery,
    grounding,
    injection,
    league,
    settle_job,
)


def _with_root(check: Callable[[Any, Path], list[str]], mod: Any) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        return check(mod, Path(tmp))


def _resettle() -> list[str]:
    from tests.test_resettle_published import test_an_edited_start_end_or_outcome_is_caught

    with tempfile.TemporaryDirectory() as tmp:
        try:
            test_an_edited_start_end_or_outcome_is_caught(Path(tmp))
        except AssertionError as exc:
            return [f"the edited calls were not all caught: {exc}"]
    return []


def _cases() -> list[tuple[str, Callable[[], list[str]]]]:
    from tests import (
        claim_extract_checks,
        deployment_checks,
        forgery_checks,
        grounding_checks,
        injection_checks,
        league_checks,
        settle_job_checks,
    )

    return [
        (
            "delayed data: a missing outcome candle is retried, then the call is unsettleable, never guessed",
            lambda: _with_root(settle_job_checks.check_missing, settle_job),
        ),
        (
            "contradicting venues: oracles that disagree make the call unsettleable, a tie is void",
            lambda: _with_root(settle_job_checks.check_outcomes, settle_job),
        ),
        (
            "missing RYO evidence: a failed RYO call is recorded, the cycle commits on the other routes",
            lambda: _with_root(league_checks.check_ryo_route, league),
        ),
        ("RYO refuses the key: nothing is asked, the reason is recorded", lambda: _with_root(league_checks.check_ryo_key_status, league)),
        (
            "a required source down: the slot fails as missing evidence, never as an abstention",
            lambda: _with_root(league_checks.check_failed_evidence, league),
        ),
        (
            "clock skew: a stepped clock is refused by the writer and shown to the operator",
            lambda: _with_root(deployment_checks.check_clock_status, deployment),
        ),
        (
            "edited records: a forged bundle is refused by the verifier against the honest root",
            lambda: forgery_checks.check_the_forgery(forgery),
        ),
        ("unsupported model assertions: a number the evidence does not hold is refused", lambda: grounding_checks.check_numeric(grounding)),
        ("false source claims: a quote that is not in the text is refused", lambda: claim_extract_checks.check_to_graph(claim_extract)),
        (
            "embedded instructions: no instruction in untrusted text changes a decision",
            lambda: injection_checks.check_the_real_defences(injection),
        ),
        ("an outsider re-settles published calls from public candles and catches an edited one", _resettle),
    ]


@pytest.mark.parametrize(("attack", "check"), _cases(), ids=lambda v: v.split(":")[0] if isinstance(v, str) else "")
def test_the_attack_fails(attack: str, check: Callable[[], list[str]]) -> None:
    assert check() == [], attack
